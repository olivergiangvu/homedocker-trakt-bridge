#!/usr/bin/env python3
"""Read-only request DELTA observer for one user-initiated client action.

Records only file descriptor positions for existing plain nginx access logs,
waits for an operator to use ONE client, then processes only newly appended
log bytes. Raw lines exist in memory only and are NEVER printed, written or
exported. No network operations, Nginx reload, container action or DB writes.

User-Agent labels are untrusted hints and cannot establish actual client or
gateway route identity; all logs in /var/log/nginx may contain other traffic.
"""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
import time

SOURCE = Path(__file__).with_name("audit-v140-client-ingress.py")
spec = importlib.util.spec_from_file_location("v140_ingress_classify", SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

MAX_APPENDED_BYTES = 4 * 1024 * 1024

def snapshot(directory):
    result = {}
    for path in core.log_candidates(directory):
        try:
            st = path.stat()
        except OSError:
            continue
        result[path.name] = (st.st_dev, st.st_ino, st.st_size)
    return result

def parse_new_line(line):
    matched = core.REQUEST.search(line)
    if not matched:
        return None
    method, target, status = matched.groups()
    route = core.classify(target)
    if route is None:
        return None
    return route, method, status, core.ua_hint(line)

def compare(directory, before):
    after = {path.name: path for path in core.log_candidates(directory)}
    groups = Counter()
    routes = Counter()
    statuses = Counter()
    hints = Counter()
    methods = Counter()
    missing_or_rotated = []
    oversized = []
    unreadable = []
    for name, (dev, ino, offset) in sorted(before.items()):
        path = after.get(name)
        if not path:
            missing_or_rotated.append("log_missing_or_rotated")
            continue
        try:
            st = path.stat()
            if st.st_dev != dev or st.st_ino != ino or st.st_size < offset:
                missing_or_rotated.append("log_missing_or_rotated")
                continue
            delta = st.st_size - offset
            if delta > MAX_APPENDED_BYTES:
                oversized.append("log_delta_exceeds_limit")
                continue
            if delta == 0:
                continue
            with path.open("rb") as source:
                source.seek(offset)
                raw = source.read(delta)
        except OSError:
            unreadable.append("log_unreadable")
            continue
        # If the byte range ends mid-line, do not count that partial record.
        if raw and not raw.endswith(b"\n"):
            raw = raw.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in raw else b""
        for line in raw.decode("utf-8", errors="replace").splitlines():
            row = parse_new_line(line)
            if row is None:
                continue
            route, method, status, hint = row
            groups[(route, method, status, hint)] += 1
            routes[route] += 1
            statuses[status] += 1
            hints[hint] += 1
            methods[method] += 1

    complete = not (missing_or_rotated or oversized or unreadable)
    return {
        "audit": "V140_CLIENT_EXACT_APPEND_DELTA",
        "observation_complete": complete,
        "files_at_start": len(before),
        "files_unchanged_or_appended": len(before)
            - len(missing_or_rotated) - len(oversized) - len(unreadable),
        "files_changed_or_rotated": len(missing_or_rotated),
        "files_over_capacity": len(oversized),
        "files_unreadable": len(unreadable),
        "new_jellyfin_candidate_requests": sum(groups.values()),
        "api_path_groups": dict(sorted(routes.items())),
        "exact_status_counts": dict(sorted(statuses.items())),
        "http_methods": dict(sorted(methods.items())),
        "untrusted_user_agent_hints": dict(sorted(hints.items())),
        "route_method_status_ua_counts": [
            {"route": route, "method": method, "status": status,
             "untrusted_ua_hint": hint, "count": count}
            for (route, method, status, hint), count in sorted(groups.items())
        ],
        "client_identity_confirmed": False,
        "gateway_routing_confirmed": False,
        "native_aio_api_coverage": "NOT_TESTED",
        "warning": "Only appended nginx log records; other clients may share logs, some routes may bypass nginx",
        "raw_urls_ips_domains_tokens_user_ids_or_media_ids_printed": False,
        "docker_nginx_watch_state_changes": 0,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("odin", "aio-desktop"),
                        required=True, help="operator test label; not client authentication")
    parser.add_argument("--settle-seconds", type=int, choices=range(0, 6),
                        default=2, metavar="{0..5}")
    args = parser.parse_args()
    try:
        before = snapshot(Path("/var/log/nginx"))
        if not before:
            raise RuntimeError("No readable nginx plain log candidates")
        print("R17_SNAPSHOT_CAPTURED=TRUE", flush=True)
        print("Open Continue Watching in ONLY the chosen client; then press Enter.", flush=True)
        input()
        if args.settle_seconds:
            time.sleep(args.settle_seconds)
        result = compare(Path("/var/log/nginx"), before)
        result["operator_test_label"] = args.client
        print(json.dumps(result, indent=2))
        print("R17_READ_ONLY_DELTA_COMPLETE", flush=True)
    except (OSError, ValueError, RuntimeError, EOFError):
        print("STOP: observation incomplete; no host changes were made",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
