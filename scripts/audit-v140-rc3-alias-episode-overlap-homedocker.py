#!/usr/bin/env python3
"""RC3-F: verify overlap and conflicting playback flags across known IMDb aliases.

SQLite read-only and profile/sink scoped. Produces strictly numeric aggregate
statistics. NEVER exports show IDs, IMDb IDs, episodes, timestamps, titles or
individual progress; never writes watch_state, Trakt, Bridge or Jellyfin.

The output is NOT a replacement NextUp shelf and does NOT qualify client routing.
"""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3

CORE_PATH = Path(__file__).with_name(
    "audit-v140-rc3-alias-timeline-coverage-homedocker.py"
)
spec = importlib.util.spec_from_file_location("rc3e_coverage", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

MAX_GROUPS = 200
MAX_ROWS_PER_GROUP = 5000


def assert_int(value, minimum=0):
    return type(value) is int and value >= minimum


def compare_timeline_group(rows, spellings):
    """Pure comparison on validated, explicitly verified spelling groups."""
    result = {
        "raw_episode_rows": len(rows),
        "invalid_coordinate_rows": 0,
        "repeated_coordinate_rows": 0,
        "canonical_unique_episodes": 0,
        "alias_unique_episodes": 0,
        "overlap_episodes": 0,
        "canonical_only_episodes": 0,
        "alias_only_episodes": 0,
        "overlap_played_disagreements": 0,
        "overlap_with_any_active_resume": 0,
        "overlap_with_both_active_resumes": 0,
    }
    by_spelling = {s: {} for s in spellings}
    for key, season, episode, played, position in rows:
        if key not in by_spelling:
            raise core.AuditStop("SERIES_KEY_OUT_OF_SCOPE")
        if not assert_int(season) or not assert_int(episode) \
                or type(played) not in (int, bool) or played not in (0, 1) \
                or position is None or type(position) not in (int, float) or position < 0:
            result["invalid_coordinate_rows"] += 1
            continue
        coordinate = (season, episode)
        target = by_spelling[key]
        # If a source provides two rows for the same episode under the same
        # spelling (different editions etc), do not silently merge them.
        if coordinate in target:
            result["repeated_coordinate_rows"] += 1
            continue
        target[coordinate] = (bool(played), position > 0)

    if result["repeated_coordinate_rows"]:
        # An ambiguous local coordinate can make overlap counts misleading.
        # STOP rather than treating the show as automatically repairable.
        raise core.AuditStop("AMBIGUOUS_EPISODE_COORDINATES")

    canon = by_spelling[spellings[0]]
    alias = {}
    for spelling in spellings[1:]:
        for coordinate, state in by_spelling[spelling].items():
            if coordinate in alias:
                raise core.AuditStop("ALIAS_EPISODE_COORDINATE_CONFLICT")
            alias[coordinate] = state
    cs, als = set(canon), set(alias)
    both = cs & als
    result["canonical_unique_episodes"] = len(cs)
    result["alias_unique_episodes"] = len(als)
    result["overlap_episodes"] = len(both)
    result["canonical_only_episodes"] = len(cs - als)
    result["alias_only_episodes"] = len(als - cs)
    for coordinate in both:
        p, q = canon[coordinate], alias[coordinate]
        if p[0] != q[0]:
            result["overlap_played_disagreements"] += 1
        first_active = not p[0] and p[1]
        second_active = not q[0] and q[1]
        if first_active or second_active:
            result["overlap_with_any_active_resume"] += 1
        if first_active and second_active:
            result["overlap_with_both_active_resumes"] += 1
    return result


def aggregate_overlap(aio, bridge):
    # Scope and graph are verified by the already tested RC3-E read-only module.
    profiles = bridge.execute(
        "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
    ).fetchall()
    if len(profiles) != 1:
        raise core.AuditStop("CONNECTED_PROFILE_NOT_UNIQUE")
    sinks = aio.execute(
        "SELECT uuid, persona FROM watch_sinks "
        "WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL",
        (core.SINK_NAME, core.SINK_INSTANCE),
    ).fetchall()
    if len(sinks) != 1:
        raise core.AuditStop("WATCH_SINK_NOT_UNIQUE")
    uuid, persona = sinks[0]
    if not isinstance(uuid, str) or not isinstance(persona, str):
        raise core.AuditStop("WATCH_SINK_SCOPE_INVALID")

    entry = bridge.execute(
        "SELECT payload_json FROM media_cache "
        "WHERE cache_key=? AND expires_at > CAST(strftime('%s','now') AS INTEGER)",
        ("identity-graph:v1:" + profiles[0][0],),
    ).fetchone()
    if not entry:
        raise core.AuditStop("VERIFIED_GRAPH_NOT_AVAILABLE")
    try:
        import json
        raw = json.loads(entry[0])
    except (ValueError, TypeError):
        raise core.AuditStop("GRAPH_JSON_INVALID") from None
    _, groups = core.verified_alias_groups(raw)
    if len(groups) > MAX_GROUPS:
        raise core.AuditStop("TOO_MANY_ALIAS_GROUPS")
    counts = {
        "verified_alias_groups": len(groups),
        "groups_with_exact_episode_overlap": 0,
        "groups_without_exact_episode_overlap": 0,
        "raw_episode_rows": 0,
        "invalid_coordinate_rows": 0,
        "repeated_coordinate_rows": 0,
        "canonical_unique_episodes": 0,
        "alias_unique_episodes": 0,
        "overlap_episodes": 0,
        "canonical_only_episodes": 0,
        "alias_only_episodes": 0,
        "overlap_played_disagreements": 0,
        "overlap_with_any_active_resume": 0,
        "overlap_with_both_active_resumes": 0,
    }
    for spellings in groups:
        lookup = ["s|" + s for s in spellings]
        holders = ",".join("?" for _ in lookup)
        records = aio.execute(
            "SELECT series_key, season, episode, played, position_ms "
            "FROM watch_state "
            "WHERE uuid=? AND persona=? AND kind='episode' "
            "AND series_key IN (" + holders + ") "
            "LIMIT ?", (uuid, persona, *lookup, MAX_ROWS_PER_GROUP+1),
        ).fetchall()
        if len(records) > MAX_ROWS_PER_GROUP:
            raise core.AuditStop("EPISODE_ROWS_OVER_BUDGET")
        summary = compare_timeline_group(records, lookup)
        if summary["overlap_episodes"]:
            counts["groups_with_exact_episode_overlap"] += 1
        else:
            counts["groups_without_exact_episode_overlap"] += 1
        for k, value in summary.items():
            counts[k] += value
    return counts


def main():
    with closing(core.ro(core.AIO_DB)) as aio, \
            closing(core.ro(core.BRIDGE_DB)) as bridge:
        result = aggregate_overlap(aio, bridge)
    print("RC3F_AUDIT=EPISODE_COORDINATE_OVERLAP")
    for k, v in result.items():
        print("RC3F_" + k.upper() + "=" + str(v))
    print("RC3F_SOURCE=VERIFIED_BRIDGE_GRAPH_AND_AIO_WATCH_STATE")
    print("RC3F_MODE=READ_ONLY")
    print("RC3F_NEXTUP_PRESENCE_PROVEN=NO")
    print("RC3F_CLIENT_VIEW_TRANSFORMED=NO")
    print("RC3F_HTTP_REQUESTS=0")
    print("RC3F_DATABASE_WRITES=0")
    print("RC3F_SECRETS_PRINTED=NO")


if __name__ == "__main__":
    try:
        main()
    except (core.AuditStop, sqlite3.Error, OSError, ValueError, TypeError):
        print("RC3F_RESULT=STOP_UNQUALIFIED_SOURCE_OR_ROWS")
        print("RC3F_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
