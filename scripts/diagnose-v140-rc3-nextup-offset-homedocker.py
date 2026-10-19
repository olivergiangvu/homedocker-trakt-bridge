#!/usr/bin/env python3
"""RC3-P1 diagnostic: distinguish absent Jellyfin StartIndex echo from ignored offset.

GET-only localhost, exactly 3 bounded NextUp probes plus 1 Users/Me,
no CLI credentials, no output of user/episode IDs, tokens, URLs or content.
Uses existing root-only /run token, verifies mapped user and Gateway OFF twice.
Does NOT qualify RC3 pagination, mutate anything or route clients.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from urllib.parse import urlencode

PARENT = Path(__file__).with_name("audit-v140-rc3-nextup-shadow-homedocker.py")
spec = importlib.util.spec_from_file_location("rc3_shadow_core", PARENT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
LIMIT = 20


def validate(page):
    rows = page.get("Items")
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise core.StopAudit("DIAG_PAGE_CONTRACT")
    ids = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("Id"), str)
            or not 1 <= len(row["Id"]) <= 512):
            raise core.StopAudit("DIAG_ITEM_CONTRACT")
        ids.append(row["Id"])
    if len(ids) != len(set(ids)):
        raise core.StopAudit("DIAG_DUPLICATE_IDS_IN_PAGE")
    return ids


def echo_status(page, offset):
    if "StartIndex" not in page:
        return "ABSENT"
    actual = page["StartIndex"]
    if type(actual) is not int:
        return "INVALID_TYPE"
    return "MATCH" if actual == offset else "MISMATCH"


def safe_page_fingerprint(page):
    raw = json.dumps(page, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    if len(raw) > core.MAX_BODY:
        raise core.StopAudit("DIAG_BODY_TOO_LARGE")
    return hashlib.sha256(raw).digest()


def diagnose(read):
    pages = []
    # Only 3 GET NextUp. Page0 replay distinguishes a static repeated result
    # from an endpoint whose contents changed while we were testing offsets.
    for offset in (0, 20, 0):
        page = read("/Shows/NextUp?" + urlencode({
            "StartIndex": offset, "Limit": LIMIT,
        }))
        ids = validate(page)
        pages.append((offset, page, ids))
    (_, p0, i0), (_, p20, i20), (_, p0again, i0again) = pages
    stable = safe_page_fingerprint(p0) == safe_page_fingerprint(p0again)
    overlap = len(set(i0).intersection(i20))
    same_sequence = i0 == i20
    e0 = echo_status(p0, 0)
    e20 = echo_status(p20, 20)
    e0again = echo_status(p0again, 0)

    if not stable:
        decision = "SOURCE_CHANGED_DURING_DIAGNOSTIC"
    elif any(e in ("MISMATCH", "INVALID_TYPE") for e in (e0, e20, e0again)):
        decision = "EXPLICIT_OFFSET_ECHO_INVALID"
    elif same_sequence and len(i0) == LIMIT:
        decision = "OFFSET_IGNORED_OR_CONTENT_REPEATED"
    elif overlap:
        decision = "PAGES_OVERLAP_NO_SAFE_SLICE"
    elif e0 == e20 == e0again == "MATCH":
        decision = "OFFSETS_OBSERVED_NOT_SNAPSHOT_PROOF"
    else:
        decision = "MISSING_ECHO_OFFSETS_UNPROVEN"

    return {
        "first_items": len(i0),
        "second_items": len(i20),
        "first_echo": e0,
        "second_echo": e20,
        "first_replay_echo": e0again,
        "first_replay_stable": stable,
        "same_episode_sequence": same_sequence,
        "cross_page_overlap_count": overlap,
        "decision": decision,
    }


def main():
    token, mapped = core.credential()
    core.bridge_gate()
    reader = core.make_reader(token, time.monotonic() + core.DEADLINE_SECONDS)
    if reader("/Users/Me").get("Id") != mapped:
        raise core.StopAudit("AUTHENTICATED_USER_NOT_MAPPED")
    outcome = diagnose(reader)
    core.bridge_gate()
    print("R4C_DIAG_IDENTITY=MATCHED")
    print("R4C_DIAG_PAGE0_ITEMS=" + str(outcome["first_items"]))
    print("R4C_DIAG_PAGE20_ITEMS=" + str(outcome["second_items"]))
    print("R4C_DIAG_PAGE0_STARTINDEX=" + outcome["first_echo"])
    print("R4C_DIAG_PAGE20_STARTINDEX=" + outcome["second_echo"])
    print("R4C_DIAG_PAGE0_REPLAY_STARTINDEX=" + outcome["first_replay_echo"])
    print("R4C_DIAG_PAGE0_REPLAY_STABLE=" + str(outcome["first_replay_stable"]).lower())
    print("R4C_DIAG_SAME_EPISODE_SEQUENCE=" + str(outcome["same_episode_sequence"]).lower())
    print("R4C_DIAG_CROSS_PAGE_OVERLAP=" + str(outcome["cross_page_overlap_count"]))
    print("R4C_DIAG_DECISION=" + outcome["decision"])
    print("R4C_DIAG_GET_REQUESTS=4")
    print("R4C_DIAG_GATEWAY_FILTER=OFF")
    print("R4C_DIAG_PRODUCTION_CHANGES=0")
    print("R4C_DIAG_CLIENT_ROUTING_QUALIFIED=NO")
    print("R4C_DIAG_SECRETS_PRINTED=NO")


if __name__ == "__main__":
    try:
        main()
    except (core.StopAudit, OSError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, core.StopAudit) else "LOCAL_DIAG_UNAVAILABLE"
        print("R4C_DIAG_STOP=" + detail)
        print("R4C_DIAG_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
