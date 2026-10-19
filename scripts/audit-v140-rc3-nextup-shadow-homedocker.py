#!/usr/bin/env python3
"""HomeDocker RC3-P1: confidential, read-only AIO NextUp paging shadow probe.

Standalone host-side script, NOT part of the running Bridge or production
Gateway. Requires a verified mapped Jellyfin user session stored mode 0600
in /run; sends only local GET, does not write Trakt, SQLite, nginx or Docker.
No credential, ID, episode name, playback timestamp or URL is emitted.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

TOKEN = Path("/run/homedocker-r4/aio-user-token")
MAPPING = Path("/etc/homedocker-trakt-bridge/nextup-auth/jellyfin-user.env")
ROOT = Path("/run/homedocker-r4")
AIO = "http://127.0.0.1:3240/jellyfin"
LIMIT = 20
MAX_PAGES = 4
MAX_BODY = 1024 * 1024
DEADLINE_SECONDS = 18
MAX_CALL_SECONDS = 6
FIRST_PAGE_QUERY = {"StartIndex": "0", "Limit": str(LIMIT)}

BRIDGE_GATE_JS = """
import {loadConfig} from './src/config.mjs';
import {deriveProfileKey} from './src/crypto.mjs';
const c=loadConfig();
if (c.jellyfinGatewaySourceKind !== 'aiostreams') {
  process.stdout.write('SOURCE_UNEXPECTED'); process.exit(2);
}
const u = new URL('/u/' + c.jellyfinGatewayProfileId
  + '/gateway/status.json','http://127.0.0.1:7000');
u.searchParams.set('key',deriveProfileKey(
  c.bridgeSecret,'setup',c.jellyfinGatewayProfileId));
const r=await fetch(u, {signal:AbortSignal.timeout(5000)});
if (!r.ok) {process.stdout.write('STATUS_UNAVAILABLE');process.exit(2);}
const j=await r.json();
process.stdout.write(
  j.listening && !j.filteringEnabled ? 'LISTENER_ACTIVE_FILTER_OFF'
    : 'FILTER_OR_LISTENER_NOT_SAFE');
if (j.filteringEnabled || !j.listening) process.exit(2);
"""


class StopAudit(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_read_file(path, expected_mode=0o600):
    try:
        info = path.lstat()
    except OSError:
        raise StopAudit("PRIVATE_FILE_NOT_AVAILABLE") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 \
            or stat.S_IMODE(info.st_mode) != expected_mode:
        raise StopAudit("PRIVATE_FILE_UNSAFE")
    return path.read_text(encoding="utf-8").strip()


def credential():
    try:
        info = ROOT.lstat()
    except OSError:
        raise StopAudit("PRIVATE_DIR_NOT_AVAILABLE") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 \
            or stat.S_IMODE(info.st_mode) != 0o700:
        raise StopAudit("PRIVATE_DIR_UNSAFE")
    token = safe_read_file(TOKEN)
    if not 16 <= len(token) <= 4096 or any(ord(x) < 33 or ord(x) > 126 for x in token):
        raise StopAudit("TOKEN_FORMAT_INVALID")
    value = safe_read_file(MAPPING)
    ids = []
    for line in value.splitlines():
        key, sep, raw = line.strip().partition("=")
        if sep and key.strip() in ("JELLYFIN_GATEWAY_USER_ID", "JELLYFIN_GATEWAY_AIO_USER_ID"):
            ids.append(raw.strip().strip("'\""))
    if len(ids) != 1 or not ids[0]:
        raise StopAudit("MAPPED_USER_ID_UNAVAILABLE")
    return token, ids[0]


def bridge_gate():
    try:
        outcome = subprocess.run(
            ["docker", "exec", "trakt-bridge", "node",
             "--input-type=module", "-e", BRIDGE_GATE_JS],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise StopAudit("BRIDGE_GATE_UNAVAILABLE") from None
    if outcome.returncode != 0 or outcome.stdout.strip() != "LISTENER_ACTIVE_FILTER_OFF":
        raise StopAudit("BRIDGE_FILTER_NOT_OFF_OR_SOURCE_UNEXPECTED")
    return True


def make_reader(token, deadline):
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(path):
        seconds = min(MAX_CALL_SECONDS, deadline - time.monotonic())
        if seconds <= 0:
            raise StopAudit("AUDIT_DEADLINE_EXCEEDED")
        req = Request(
            AIO + path,
            method="GET",
            headers={
                "X-Emby-Token": token,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "homedocker-rc3-shadow/1",
            },
        )
        try:
            with opener.open(req, timeout=seconds) as r:
                if r.status != 200:
                    raise StopAudit("AIO_GET_NON_200")
                raw = r.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise StopAudit("AIO_RESPONSE_TOO_LARGE")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise StopAudit("AIO_JSON_CONTRACT")
                return data
        except HTTPError:
            raise StopAudit("AIO_GET_NON_200") from None
        except (URLError, TimeoutError, ValueError, UnicodeError, OSError):
            raise StopAudit("AIO_GET_UNAVAILABLE") from None

    return read


def inspect_page(page, offset):
    rows = page.get("Items")
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise StopAudit("PAGE_CONTRACT_INVALID")
    if "StartIndex" not in page:
        raise StopAudit("STARTINDEX_FIELD_ABSENT")
    if (not isinstance(page["StartIndex"], int)
        or isinstance(page["StartIndex"], bool)):
        raise StopAudit("STARTINDEX_ECHO_INVALID")
    if page["StartIndex"] != offset:
        raise StopAudit("STARTINDEX_NOT_HONORED")
    total = page.get("TotalRecordCount")
    if not isinstance(total, int) or isinstance(total, bool) \
            or total < offset + len(rows):
        raise StopAudit("PAGE_TOTAL_INCOHERENT")
    if "Count" in page and page["Count"] != len(rows):
        raise StopAudit("PAGE_COUNT_INCOHERENT")
    for row in rows:
        if not isinstance(row, dict) or row.get("Type") != "Episode" \
                or not isinstance(row.get("Id"), str) \
                or not 1 <= len(row["Id"]) <= 512 \
                or not isinstance(row.get("SeriesId"), str) \
                or not 1 <= len(row["SeriesId"]) <= 512:
            raise StopAudit("PAGE_EPISODE_INVALID")
    serialized = json.dumps(page, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_BODY:
        raise StopAudit("PAGE_TOO_LARGE")
    return hashlib.sha256(serialized.encode("utf-8")).digest()


def scan_pages(read):
    seen = set()
    pages = []
    counts = []
    requests = 0
    for index in range(MAX_PAGES):
        offset = index * LIMIT
        requests += 1
        page = read("/Shows/NextUp?" + urlencode({
            "StartIndex": offset, "Limit": LIMIT,
        }))
        fingerprint = inspect_page(page, offset)
        rows = page["Items"]
        for row in rows:
            if row["Id"] in seen:
                raise StopAudit("EPISODE_OVERLAP_ACROSS_PAGES")
            seen.add(row["Id"])
        pages.append((offset, fingerprint, page["TotalRecordCount"], len(rows)))
        counts.append(len(rows))
        if len(rows) < LIMIT:
            if not rows:
                raise StopAudit("EMPTY_TAIL_NOT_PROVEN")
            break
    else:
        return {"status": "UNPROVEN_FULL_WINDOW", "requests": requests,
                "pages": len(pages), "counts": counts}
    sum_items = sum(counts)
    is_prefix = all(total == offset + size for offset, _, total, size in pages)
    is_global = all(total == sum_items for _, _, total, _ in pages)
    if not is_prefix and not is_global:
        raise StopAudit("TOTAL_MODE_MIXED_OR_INCOHERENT")
    for offset, fingerprint, _, _ in pages:
        requests += 1
        replay = read("/Shows/NextUp?" + urlencode({
            "StartIndex": offset, "Limit": LIMIT,
        }))
        if inspect_page(replay, offset) != fingerprint:
            raise StopAudit("PAGINATION_SNAPSHOT_DRIFT")
    return {"status": "SHADOW_STABLE_SHORT_TAIL_ONLY",
            "requests": requests, "pages": len(pages), "counts": counts}


def main():
    # All output is strictly controlled and free of bearer, URLs and item IDs.
    token, mapped = credential()
    bridge_gate()
    reader = make_reader(token, time.monotonic() + DEADLINE_SECONDS)
    user = reader("/Users/Me")
    if user.get("Id") != mapped:
        raise StopAudit("AUTHENTICATED_USER_NOT_MAPPED")
    print("R4C_USER_IDENTITY=MATCHED")
    print("R4C_GATEWAY_FILTER=OFF")
    print("R4C_HTTP_METHOD=GET_ONLY")
    audit = scan_pages(reader)
    # Detect external UI preference mutations during the audit.
    bridge_gate()
    print("R4C_PAGE_COUNTS=" + ",".join(str(n) for n in audit["counts"]))
    print("R4C_NEXTUP_GET_REQUESTS=" + str(audit["requests"]))
    print("R4C_AIO_JELLYFIN_GET_REQUESTS=" + str(audit["requests"] + 1))
    print("R4C_PAGINATION=" + audit["status"])
    print("R4C_FILTER_FINAL=OFF")
    print("R4C_CLIENT_ROUTING_CHANGED=NO")
    print("R4C_WATCH_HISTORY_WRITES=0_FROM_AUDIT")
    print("R4C_SECRETS_PRINTED=NO")
    print("R4C_PRODUCTION_INGRESS_QUALIFIED=NO")


if __name__ == "__main__":
    try:
        main()
    except (StopAudit, OSError, ValueError) as error:
        message = str(error)
        print("R4C_RESULT=STOP:" + (
            message if isinstance(error, StopAudit) else "LOCAL_AUDIT_UNAVAILABLE"))
        print("R4C_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
