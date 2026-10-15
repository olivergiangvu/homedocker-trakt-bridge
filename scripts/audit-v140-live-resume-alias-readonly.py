#!/usr/bin/env python3
"""R23B-2B SOURCE ONLY: bounded live AIO Resume alias evidence for ONE Jellyfin user.

Reads existing RC6 verified alias in SQLite read-only and checks R23B localhost
health via tested R23B-2A helper. Authenticates using the existing official
Jellyfin login helper: one user-session creating POST, subsequent requests GET.
All interaction is on fixed AIO loopback 127.0.0.1:3240/jellyfin.

Two Resume pages with StartIndex=0,20 and Limit=20 (40 rows maximum), /Users/Me,
and bounded /Items/{opaque-series-id} metadata GETs. Never changes watch-state,
Trakt, RC6 DB, Docker, nginx or canary. AIO GET handlers MAY refresh cache.
No media, usernames, raw user IDs, token, history entries or SQLite values
are printed or persisted. Active positions are counted but never exposed.

Crucially, even if both verified identities are present in Resume, active
positions and non-atomic pagination mean automatic dedup is NOT safe.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
PROBE = HERE / "audit-v140-live-nextup-readonly.py"
spec = importlib.util.spec_from_file_location("r23b_nextup_support", PROBE)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)

PAGE_SIZE = 20
PAGES = 2
MAX_ROWS = PAGE_SIZE * PAGES

def read_resume_page(token, start_index):
    if start_index not in (0, PAGE_SIZE):
        raise support.Stop("invalid Resume page offset")
    route = f"/UserItems/Resume?StartIndex={start_index}&Limit={PAGE_SIZE}"
    data, duration_ms = support.fixed_get(route, token=token)
    rows = data.get("Items")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise support.Stop("invalid Resume page response")
    if any(not isinstance(row, dict) or not isinstance(row.get("Id"), str)
           or not row["Id"] for row in rows):
        raise support.Stop("invalid Resume item shape")
    return rows, duration_ms

def summarize_resume_rows(rows, fetched_series):
    episodes = [r for r in rows if r.get("Type") == "Episode"]
    movies = [r for r in rows if r.get("Type") == "Movie"]
    other = len(rows) - len(episodes) - len(movies)
    if other:
        raise support.Stop("unexpected Resume media kind")
    active = 0
    for row in rows:
        user_data = row.get("UserData")
        if not isinstance(user_data, dict):
            raise support.Stop("missing Resume UserData")
        ticks = user_data.get("PlaybackPositionTicks")
        if isinstance(ticks, bool) or not isinstance(ticks, (int, float)):
            raise support.Stop("invalid Resume position type")
        if ticks > 0:
            active += 1
    counts = {
        "canonical": 0, "alias": 0,
        "canonical_active": 0, "alias_active": 0,
        "episode_series_missing": 0,
    }
    for row in episodes:
        sid = row.get("SeriesId")
        if not isinstance(sid, str) or not sid:
            counts["episode_series_missing"] += 1
            continue
        imdb = fetched_series.get(sid)
        group = (
            "canonical" if imdb == support.KNOWN_PAIR[0]
            else "alias" if imdb == support.KNOWN_PAIR[1]
            else None
        )
        if group:
            counts[group] += 1
            if row["UserData"]["PlaybackPositionTicks"] > 0:
                counts[group + "_active"] += 1
    return episodes, movies, active, counts

def inspect_session(token, bridge_db):
    if not support.SESSION.fullmatch(token):
        raise support.Stop("invalid AIO session")
    # Read-only provenance, exactly same identity scope as NextUp audit.
    support.source_preflight(bridge_db)
    me, _ = support.run_phase(
        "AIO_USERS_ME", lambda: support.fixed_get(
            "/Users/Me", token=token, limit=128*1024
        )
    )
    if not support.USER_ID.fullmatch(str(me.get("Id") or "")):
        raise support.StageStop("AIO_USERS_ME", "AIO_USER_ID_SHAPE_INVALID")

    rows = []
    times = []
    page_sizes = []
    for page in range(PAGES):
        new_rows, elapsed = support.run_phase(
            "AIO_RESUME_PAGE_" + str(page),
            lambda page=page: read_resume_page(token, page * PAGE_SIZE),
        )
        rows.extend(new_rows)
        times.append(elapsed)
        page_sizes.append(len(new_rows))
        if len(new_rows) < PAGE_SIZE:
            break

    seen = set()
    repeated_item_ids = 0
    for row in rows:
        if row["Id"] in seen:
            repeated_item_ids += 1
        seen.add(row["Id"])

    series = list(dict.fromkeys(
        row["SeriesId"] for row in rows
        if row.get("Type") == "Episode" and isinstance(row.get("SeriesId"), str)
        and row["SeriesId"]
    ))
    if len(series) > MAX_ROWS:
        raise support.Stop("Resume series metadata bound exceeded")
    def fetch_series():
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda sid: support.item_imdb(sid, token), series))
    metadata = support.run_phase("AIO_RESUME_SERIES_METADATA", fetch_series)
    resolved = {sid: spelling if kind == "series" else None
                for sid, (kind, spelling) in zip(series, metadata)}
    episodes, movies, active, counts = summarize_resume_rows(rows, resolved)
    both = counts["canonical"] > 0 and counts["alias"] > 0

    return {
        "audit": "R23B2B_AUTHENTICATED_AIO_RESUME_SOURCE_PROOF",
        "status": ("VERIFIED_ALIAS_PAIR_IN_RESUME_SAMPLE" if both
                   else "VERIFIED_ALIAS_PAIR_NOT_OBSERVED_IN_RESUME_SAMPLE"),
        "aio_user_session_verified": True,
        "provenance_rc6_legacy_shows": 15,
        "provenance_verified_alias_groups": 1,
        "resume_route": "UserItems/Resume",
        "resume_requested_page_size": PAGE_SIZE,
        "resume_requested_offsets": [0, PAGE_SIZE][:len(page_sizes)],
        "resume_response_page_lengths": page_sizes,
        "resume_page_duration_ms": times,
        "resume_returned_items": len(rows),
        "resume_episode_items": len(episodes),
        "resume_movie_items": len(movies),
        "resume_active_position_items": active,
        "resume_repeated_opaque_item_ids_in_sample": repeated_item_ids,
        "resume_series_missing_reference": counts["episode_series_missing"],
        "resume_distinct_episode_series_references": len(series),
        "resume_series_metadata_valid": sum(kind == "series" for kind, _ in metadata),
        "resume_series_metadata_imdb_present": sum(
            kind == "series" and bool(spelling) for kind, spelling in metadata
        ),
        "verified_canonical_resume_cards": counts["canonical"],
        "verified_alias_resume_cards": counts["alias"],
        "verified_canonical_active_resume_cards": counts["canonical_active"],
        "verified_alias_active_resume_cards": counts["alias_active"],
        "both_verified_spellings_present": both,
        "all_resume_pages_proven_complete": False,
        "safe_to_deduplicate_active_resume": False,
        "gateway_resume_normalization_tested": False,
        "aio_login_post_requests": 1,
        "aio_get_requests": 1 + len(page_sizes) + len(series),
        "aio_get_handlers_may_refresh_server_cache": True,
        "aio_history_or_playback_write_requests": 0,
        "trakt_requests": 0,
        "docker_or_nginx_changes": 0,
        "raw_user_or_media_ids_history_or_credentials_printed": False,
        "session_token_saved": False,
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth",choices=("login","token"),default="login")
    parser.add_argument("--bridge-db",default=support.preview_module().DEFAULT_DB)
    parser.add_argument("--preflight-only",action="store_true")
    args=parser.parse_args()
    try:
        check = support.source_preflight(args.bridge_db)
        print(json.dumps(check,indent=2),flush=True)
        if args.preflight_only:
            print("R23B2B_SAFE_PREFLIGHT_PASS")
            return
        session = support.obtain_authenticated_session(args.auth)
        result = support.run_phase(
            "AIO_RESUME_SOURCE_AUDIT",
            lambda: inspect_session(session,args.bridge_db)
        )
        result["aio_login_post_requests"]=int(args.auth=="login")
        print(json.dumps(result,indent=2),flush=True)
        print("R23B2B_RESUME_READONLY_SOURCE_PROOF_COMPLETE")
    except support.StageStop as e:
        print(json.dumps({
            "audit":"R23B2B_SAFE_DIAGNOSTIC","status":"STOP",
            "phase":e.phase,"reason":e.code,
            "media_or_user_values_printed":False,
            "history_or_playback_write_requests":0
        }),file=sys.stderr)
        sys.exit(1)
    except (Exception,KeyboardInterrupt,EOFError):
        print(json.dumps({
            "audit":"R23B2B_SAFE_DIAGNOSTIC","status":"STOP",
            "phase":"LOCAL_PRECHECK_OR_RUNTIME",
            "reason":"FIXED_SANITIZED_ERROR",
            "media_or_user_values_printed":False,
            "history_or_playback_write_requests":0
        }),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
