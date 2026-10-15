#!/usr/bin/env python3
"""Read-only, aggregate-only audit of candidate Jellyfin client HTTP paths.

Reads recent host nginx access logs. NEVER prints IPs, domains, query strings,
raw paths, user-agent values, authentication headers, media IDs, user IDs,
user names or watch data. No Docker, nginx, network, filesystem writes.

A matching path does not prove which specific player sent it, which nginx
server block served it or that Gateway routing was active. Absence may mean
the client uses native AIO endpoints, different logs or direct connections.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

LOG_DIR = Path("/var/log/nginx")
MAX_LOGS = 80
MAX_TAIL_BYTES = 12 * 1024 * 1024
MAX_MINUTES = 1440
STAMP = re.compile(r"\[([0-9]{2}/[A-Za-z]{3}/[0-9]{4}:[0-9]{2}:[0-9]{2}:[0-9]{2} [+-][0-9]{4})\]")
REQUEST = re.compile(r'"(GET|HEAD|POST|PUT|DELETE|PATCH|OPTIONS) ([^" ]+) HTTP/[0-9.]+"\s+([0-9]{3})\b')
LEGACY = re.compile(r"^/users/[^/]{8,128}/items/resume$")
USER_DATA = re.compile(r"^/users/[^/]{8,128}/items/")

def classify(target):
    # Ignore ALL query parameters: they may carry secret tokens, episode IDs
    # and session identifiers. No target or query value is ever emitted.
    route = target.split("?", 1)[0].lower()
    if route.startswith("/jellyfin/"):
        route = route[len("/jellyfin"):]
    if route == "/shows/nextup":
        return "jellyfin_nextup"
    if route == "/useritems/resume":
        return "jellyfin_resume_modern"
    if LEGACY.fullmatch(route):
        return "jellyfin_resume_legacy"
    if route in ("/users/me", "/system/info", "/system/info/public"):
        return "jellyfin_auth_or_discovery"
    if route.startswith("/items/") or USER_DATA.match(route):
        return "jellyfin_items_other"
    if route.startswith(("/sessions/", "/playback/", "/videos/")):
        return "jellyfin_playback_related"
    return None

def ua_hint(line):
    # Request is typically first quoted field. UA is third in nginx combined
    # format. Strictly return a controlled category, never its raw value.
    parts = re.findall(r'"([^"]*)"', line)
    if len(parts) < 3:
        return "unknown"
    ua = parts[2].lower()[:512]
    if "odin" in ua:
        return "possible_odin"
    if "aiostream" in ua and ("desktop" in ua or "electron" in ua):
        return "possible_aiostreams_desktop"
    if "remux" in ua:
        return "possible_remux"
    if "trellis" in ua:
        return "possible_trellis"
    if "strand" in ua:
        return "possible_strand"
    return "other_or_generic"

def audit_line(line, cutoff, future):
    stamp = STAMP.search(line)
    match = REQUEST.search(line)
    if not stamp or not match:
        return None
    try:
        parsed = datetime.strptime(stamp.group(1), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    if parsed < cutoff or parsed > future:
        return None
    method, target, status = match.groups()
    category = classify(target)
    if category is None:
        return None
    return category, method, status[0] + "xx", ua_hint(line)

def log_candidates(directory):
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Expected a real nginx log directory")
    candidates = []
    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_file():
            continue
        # Never parse .gz/.zip or arbitrary system files. Only plain logs,
        # plus one logrotate generation where the suffix is .log.1.
        if entry.name.endswith(".log") or entry.name.endswith(".log.1"):
            candidates.append(entry)
    if len(candidates) > MAX_LOGS:
        raise ValueError("Too many nginx logs: narrow audit before retry")
    return candidates

def tail_lines(path):
    with path.open("rb") as source:
        length = source.seek(0, 2)
        start = max(0, length - MAX_TAIL_BYTES)
        source.seek(start)
        raw = source.read(MAX_TAIL_BYTES)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # discard incomplete first line
    return lines, start > 0

def audit(directory=LOG_DIR, minutes=60, now=None):
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError("minutes must be between 1 and 1440")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)
    future = now + timedelta(minutes=1)
    paths = log_candidates(directory)
    groups = Counter()
    statuses = Counter()
    methods = Counter()
    hints = Counter()
    parsed = 0
    partial = 0
    unreadable = 0
    for path in paths:
        try:
            lines, truncated = tail_lines(path)
        except OSError:
            unreadable += 1
            continue
        partial += int(truncated)
        for line in lines:
            row = audit_line(line, cutoff, future)
            if row is None:
                continue
            category, method, status, hint = row
            groups[category] += 1
            statuses[status] += 1
            methods[method] += 1
            hints[hint] += 1
            parsed += 1
    return {
        "audit": "V140_JELLYFIN_CLIENT_PATH_OBSERVATION",
        "window_minutes": minutes,
        "nginx_log_files_scanned": len(paths) - unreadable,
        "nginx_log_files_unreadable": unreadable,
        "log_files_tail_truncated": partial,
        "candidate_jellyfin_requests": parsed,
        "api_path_groups": dict(sorted(groups.items())),
        "status_families": dict(sorted(statuses.items())),
        "http_methods": dict(sorted(methods.items())),
        "untrusted_user_agent_hints": dict(sorted(hints.items())),
        "client_identification_confirmed": False,
        "gateway_routing_confirmed": False,
        "native_aio_api_coverage": "NOT_PROVEN_BY_JELLYFIN_PATH_SCAN",
        "warning": "Shared nginx access logs can include unrelated clients; labels are only path observations",
        "secrets_paths_user_ids_or_domains_printed": False,
        "docker_nginx_or_watch_history_changes": 0,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=60,
                        help="recent nginx access-log window (1..1440), default 60")
    args = parser.parse_args()
    try:
        print(json.dumps(audit(minutes=args.minutes), indent=2))
    except (OSError, ValueError) as exc:
        print("STOP: " + type(exc).__name__ + "; client routing remains unverified", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
