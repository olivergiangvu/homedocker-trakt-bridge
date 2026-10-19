#!/usr/bin/env python3
"""RC3-P2 HomeDocker-only GET shadow: verify a 50-item NextUp refill opportunity.

READ ONLY. Does not launch a second Bridge writer or change nginx, Trakt,
watch_state, AIO, Docker images or production Gateway ON/OFF. Token comes
from existing root-only /run file. No item IDs/titles/watch positions/URLs/
access tokens or account IDs are printed. This tool is a *candidate* audit,
NOT a client-facing Jellyfin paging fix.
"""
import concurrent.futures
from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote

CORE_PATH = Path(__file__).with_name("audit-v140-rc3-nextup-shadow-homedocker.py")
spec = importlib.util.spec_from_file_location("rc3_core_for_adaptive", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

DB_PATH = Path("/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db")
MAX_METADATA = 50
MAX_ACTIVE = 4
MAX_PROBE_SECONDS = 35
CLIENT_LIMIT = 20
WINDOW_LIMIT = 50
IMDB = re.compile(r"tt[0-9]{7,12}\Z")


def read_graph():
    if not DB_PATH.is_file() or DB_PATH.is_symlink():
        raise core.StopAudit("BRIDGE_SQLITE_UNAVAILABLE")
    with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=3)) as db:
        db.execute("PRAGMA query_only=ON")
        profiles = db.execute(
            "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
        ).fetchall()
        if len(profiles) != 1:
            raise core.StopAudit("CONNECTED_PROFILE_NOT_UNIQUE")
        profile = profiles[0][0]
        result = db.execute(
            "SELECT payload_json FROM media_cache WHERE cache_key=? "
            "AND expires_at > CAST(strftime('%s','now') AS INTEGER)",
            ("identity-graph:v1:" + profile,)
        ).fetchone()
    try:
        graph = json.loads(result[0]) if result else {}
    except (ValueError, TypeError):
        raise core.StopAudit("GRAPH_MALFORMED") from None
    shows = graph.get("shows")
    conflicts = graph.get("conflicts", [])
    if not isinstance(shows, dict) or not isinstance(conflicts, list) \
            or len(shows) > 10000 or len(conflicts) > 100:
        raise core.StopAudit("GRAPH_UNAVAILABLE")
    canonical = {}
    quarantined = {
        item.get("id") for item in conflicts
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for trakt, value in shows.items():
        if not trakt.isdigit() or not isinstance(value, dict):
            continue
        ident = value.get("canonicalImdb")
        aliases = value.get("aliases")
        if not isinstance(ident, str) or not IMDB.fullmatch(ident) \
                or not isinstance(aliases, list) or len(aliases) > 12:
            continue
        for spelling in [ident, *aliases]:
            if not isinstance(spelling, str) or not IMDB.fullmatch(spelling) \
                    or spelling in quarantined:
                continue
            canonical.setdefault(spelling, set()).add(trakt)
    if not any(isinstance(v, dict) and v.get("aliases") for v in shows.values()):
        raise core.StopAudit("NO_VERIFIED_ALIAS_CANDIDATES")
    return canonical


def items(page, maximum):
    rows = page.get("Items")
    if not isinstance(rows, list) or len(rows) > maximum:
        raise core.StopAudit("INVALID_NEXTUP_PAGE")
    if page.get("StartIndex", 0) != 0:
        raise core.StopAudit("UNEXPECTED_FIRST_PAGE_OFFSET")
    total = page.get("TotalRecordCount")
    if type(total) is not int or total < len(rows):
        raise core.StopAudit("INVALID_TOTAL_COUNT")
    if "Count" in page and page["Count"] != len(rows):
        raise core.StopAudit("INVALID_COUNT")
    if not all(isinstance(row, dict) and row.get("Type") == "Episode"
               and isinstance(row.get("Id"), str) and row["Id"]
               and isinstance(row.get("SeriesId"), str) and row["SeriesId"]
               and len(row["SeriesId"]) <= 512
               for row in rows):
        raise core.StopAudit("INVALID_EPISODE_SHAPE")
    ids = [row["Id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise core.StopAudit("EPISODE_IDS_OVERLAP")
    return rows


def when(value):
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None:
            return None
        return t.timestamp()
    except (ValueError, OverflowError):
        return None


def choose(a, b):
    def resumed(item):
        ticks = item.get("UserData", {}).get("PlaybackPositionTicks")
        return type(ticks) in (float, int) and ticks > 0
    a_active, b_active = resumed(a), resumed(b)
    if a_active and b_active:
        return None
    if a_active != b_active:
        return a if a_active else b
    aa = when(a.get("UserData", {}).get("LastPlayedDate"))
    bb = when(b.get("UserData", {}).get("LastPlayedDate"))
    if aa is not None and bb is not None and abs(aa - bb) > 2:
        return a if aa > bb else b
    return None


def analyze(original, window, metadata, canon):
    first = items(original, CLIENT_LIMIT)
    expanded = items(window, WINDOW_LIMIT)
    if len(first) != CLIENT_LIMIT or len(expanded) < CLIENT_LIMIT \
            or expanded[:CLIENT_LIMIT] != first:
        raise core.StopAudit("WINDOW_PREFIX_UNSTABLE")
    grouped = {}
    for row in expanded:
        imdb = metadata.get(row["SeriesId"])
        if imdb is None or len(canon.get(imdb, set())) != 1:
            continue
        group = next(iter(canon[imdb]))
        grouped.setdefault(group, []).append((row, imdb))
    drop = set()
    for group in grouped.values():
        if len(group) != 2:
            continue
        (a, aid), (b, bid) = group
        if aid == bid or a["SeriesId"] == b["SeriesId"]:
            continue
        winner = choose(a, b)
        if winner is None:
            continue
        drop.add(b["Id"] if winner is a else a["Id"])
    remaining = [row for row in expanded if row["Id"] not in drop]
    if len(remaining) < CLIENT_LIMIT:
        raise core.StopAudit("NOT_ENOUGH_ITEMS_AFTER_NORMALIZATION")
    changed = [x["Id"] for x in first] != [x["Id"] for x in remaining[:CLIENT_LIMIT]]
    return {
        "window_items": len(expanded),
        "candidate_removed": len(drop),
        "first_page_visible_change": changed,
        "able_to_fill_first_page": len(remaining) >= CLIENT_LIMIT,
        "total_known": len(expanded) < WINDOW_LIMIT
            and window.get("TotalRecordCount") == len(expanded),
    }


def main():
    token, mapped = core.credential()
    core.bridge_gate()
    deadline = time.monotonic() + MAX_PROBE_SECONDS
    reader = core.make_reader(token, deadline)
    if reader("/Users/Me").get("Id") != mapped:
        raise core.StopAudit("USER_IDENTITY_UNVERIFIED")
    canon = read_graph()
    origin = items(reader("/Shows/NextUp?StartIndex=0&Limit=20"), CLIENT_LIMIT)
    if not origin:
        raise core.StopAudit("EMPTY_NEXTUP")
    # Re-fetch exactly the same root page twice. Never use an unsupported
    # AIO StartIndex=20 as a source of additional cards.
    window_a = reader("/Shows/NextUp?StartIndex=0&Limit=50")
    window_b = reader("/Shows/NextUp?StartIndex=0&Limit=50")
    if window_a != window_b:
        raise core.StopAudit("NEXTUP_WINDOW_DRIFT")
    source_page = {"Items": origin, "TotalRecordCount": len(origin),
                   "StartIndex": 0}
    expanded = items(window_a, WINDOW_LIMIT)
    if expanded[:len(origin)] != origin:
        raise core.StopAudit("WINDOW_PREFIX_UNSTABLE")
    uniq_series = sorted({row["SeriesId"] for row in expanded})
    if len(uniq_series) > MAX_METADATA:
        raise core.StopAudit("METADATA_BUDGET")
    if time.monotonic() >= deadline:
        raise core.StopAudit("AUDIT_DEADLINE_EXCEEDED")

    # Same user token for every metadata GET. Concurrent calls are bounded.
    def detail(series_id):
        result = reader("/Items/" + quote(series_id, safe=""))
        if result.get("Type") != "Series" or result.get("Id") != series_id:
            raise core.StopAudit("METADATA_IDENTITY_MISMATCH")
        value = result.get("ProviderIds", {}).get("Imdb")
        return series_id, value.lower() if isinstance(value, str) \
            and IMDB.fullmatch(value.lower()) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_ACTIVE) as pool:
        try:
            details = dict(pool.map(detail, uniq_series))
        except (core.StopAudit, OSError, ValueError):
            raise core.StopAudit("METADATA_UNAVAILABLE") from None

    outcome = analyze(source_page, window_a, details, canon)
    core.bridge_gate()
    print("ADAPTIVE_USER=MAPPED")
    print("ADAPTIVE_GATEWAY_FILTER=OFF")
    print("ADAPTIVE_SOURCE_FIRST_PAGE_ITEMS=" + str(len(origin)))
    print("ADAPTIVE_SOURCE_WINDOW_ITEMS=" + str(outcome["window_items"]))
    print("ADAPTIVE_METADATA_LOOKUPS=" + str(len(uniq_series)))
    print("ADAPTIVE_VERIFIED_REMOVAL_CANDIDATES=" + str(outcome["candidate_removed"]))
    print("ADAPTIVE_CLIENT_FIRST_PAGE_WOULD_CHANGE=" +
          str(outcome["first_page_visible_change"]).lower())
    print("ADAPTIVE_REFILL_20_POSSIBLE=" +
          str(outcome["able_to_fill_first_page"]).lower())
    print("ADAPTIVE_GLOBAL_TOTAL_PROVEN=" + str(outcome["total_known"]).lower())
    print("ADAPTIVE_MODE=SHADOW_ONLY")
    print("ADAPTIVE_HTTP=AUTHENTICATED_GET_ONLY")
    print("ADAPTIVE_PRODUCTION_CHANGES=0")
    print("ADAPTIVE_SECRETS_PRINTED=NO")
    print("ADAPTIVE_CLIENT_ROUTING_QUALIFIED=NO")


if __name__ == "__main__":
    try:
        main()
    except (core.StopAudit, OSError, ValueError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, core.StopAudit) else "LOCAL_AUDIT_UNAVAILABLE"
        print("ADAPTIVE_RESULT=STOP:" + message)
        print("ADAPTIVE_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
