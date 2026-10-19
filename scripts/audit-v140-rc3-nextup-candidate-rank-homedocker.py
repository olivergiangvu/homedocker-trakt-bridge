#!/usr/bin/env python3
"""RC3-G: shadow-only candidate-ranking diagnosis for AIO NextUp.

Read only: computes which verified IMDb spelling timelines are eligible for
AIO recentSeries *candidate* ranking by MAX(sort_at). This is not an exact
simulation of /Shows/NextUp: recentSeries also resolves match_key, drop times
and the NextUp route checks episode metadata and caught-up status. No calls
to Trakt/Jellyfin, no database mutations, no user/show/item IDs in output.
"""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

CORE_PATH = Path(__file__).with_name(
    "audit-v140-rc3-alias-timeline-coverage-homedocker.py"
)
spec = importlib.util.spec_from_file_location("rc3g_scope_core", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

MAX_SERIES_CANDIDATES = 10000
MAX_ALIAS_GROUPS = 200
AIO_NEXTUP_CANDIDATE_SCAN_LIMIT = 200


def rank_band(rank):
    if rank is None:
        return "NO_WATCHED_CANDIDATE"
    if rank <= 50:
        return "1_TO_50"
    if rank <= 200:
        return "51_TO_200"
    return "BEYOND_200"


def collect_rankings(aio, scope):
    # From AIOStreams watch-state repository recentSeries():
    #  uuid/persona, episode nonnull, (played=1 OR position_ms>0),
    #  GROUP BY series_key, ORDER BY MAX(sort_at) DESC, series_key.
    rows = aio.execute(
        """SELECT series_key, MAX(sort_at) AS at
           FROM watch_state
           WHERE uuid=? AND persona=?
             AND series_key IS NOT NULL
             AND episode IS NOT NULL
             AND (played=1 OR position_ms>0)
           GROUP BY series_key
           ORDER BY at DESC, series_key
           LIMIT ?""",
        (*scope, MAX_SERIES_CANDIDATES+1),
    ).fetchall()
    if len(rows) > MAX_SERIES_CANDIDATES:
        raise core.AuditStop("SERIES_CANDIDATE_BUDGET")
    ranks = {}
    for i, (key, at) in enumerate(rows, 1):
        if not isinstance(key, str) or at is None \
                or not isinstance(at, (int, float)):
            raise core.AuditStop("INVALID_CANDIDATE_SORT")
        ranks[key] = (i, at)
    return ranks


def effective_drop_flags(aio, scope, keys, ranked):
    # The upstream dropTimes() predicate is dropped=1 and either item_key
    # OR match_key equals series key. The most recent drop wins.
    if not keys:
        return {}
    marks = ",".join("?" for _ in keys)
    sql = (
        "SELECT item_key, match_key, dropped_at FROM watch_state "
        "WHERE uuid=? AND persona=? AND dropped=1 AND "
        "(item_key IN (" + marks + ") OR match_key IN (" + marks + "))"
    )
    rows = aio.execute(sql, (*scope, *keys, *keys)).fetchall()
    latest = {key: 0 for key in keys}
    for item_key, match_key, drop_at in rows:
        if drop_at is None:
            continue
        if type(drop_at) not in (int, float) or drop_at < 0:
            raise core.AuditStop("INVALID_DROP_TIME")
        for key in (item_key, match_key):
            if key in latest:
                latest[key] = max(latest[key], drop_at)
    return {
        key: bool(key in ranked and latest[key] > ranked[key][1])
        for key in keys
    }


def aggregate_candidate_rank(aio, bridge):
    profile_rows = bridge.execute(
        "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
    ).fetchall()
    if len(profile_rows) != 1:
        raise core.AuditStop("CONNECTED_PROFILE_NOT_UNIQUE")
    sinks = aio.execute(
        "SELECT uuid, persona FROM watch_sinks WHERE addon_name=? "
        "AND addon_instance_id=? AND retired_at IS NULL",
        (core.SINK_NAME, core.SINK_INSTANCE),
    ).fetchall()
    if len(sinks) != 1:
        raise core.AuditStop("WATCH_SINK_NOT_UNIQUE")
    scope = sinks[0]
    if not all(isinstance(x, str) for x in scope):
        raise core.AuditStop("WATCH_SINK_SCOPE_INVALID")
    record = bridge.execute(
        "SELECT payload_json FROM media_cache WHERE cache_key=? "
        "AND expires_at > CAST(strftime('%s','now') AS INTEGER)",
        ("identity-graph:v1:" + profile_rows[0][0],),
    ).fetchone()
    if not record:
        raise core.AuditStop("GRAPH_MISSING")
    try:
        graph = json.loads(record[0])
    except (TypeError, ValueError):
        raise core.AuditStop("GRAPH_CORRUPT") from None
    _, groups = core.verified_alias_groups(graph)
    if len(groups) > MAX_ALIAS_GROUPS:
        raise core.AuditStop("VERIFIED_GROUP_BUDGET")
    rank = collect_rankings(aio, scope)
    result = {
        "verified_alias_groups": len(groups),
        "ranked_candidate_series": len(rank),
        "canonical_candidates_in_rank_1_to_50": 0,
        "canonical_candidates_in_rank_51_to_200": 0,
        "canonical_candidates_beyond_200": 0,
        "canonical_candidates_absent": 0,
        "alias_candidates_in_rank_1_to_50": 0,
        "alias_candidates_in_rank_51_to_200": 0,
        "alias_candidates_beyond_200": 0,
        "alias_candidates_absent": 0,
        "groups_with_both_candidate_spellings": 0,
        "groups_with_only_one_candidate_spelling": 0,
        "groups_with_no_candidate_spelling": 0,
        "groups_with_both_ranked_within_200": 0,
        "groups_with_any_effective_series_drop": 0,
        "groups_with_both_effective_series_drops": 0,
    }
    individual_band = []
    for spellings in groups:
        keys = ["s|" + spelling for spelling in spellings]
        flags = effective_drop_flags(aio, scope, keys, rank)
        bands = [rank_band(rank[key][0]) if key in rank else "NO_WATCHED_CANDIDATE"
                 for key in keys]
        key_names = ("in_rank_1_to_50", "in_rank_51_to_200",
                     "beyond_200", "absent")
        def accumulate(prefix, value):
            token = {"1_TO_50":key_names[0],
                     "51_TO_200":key_names[1],
                     "BEYOND_200":key_names[2],
                     "NO_WATCHED_CANDIDATE":key_names[3]}[value]
            result[prefix + token] += 1
        accumulate("canonical_candidates_", bands[0])
        for b in bands[1:]:
            accumulate("alias_candidates_", b)
        present = sum(key in rank for key in keys)
        if present >= 2:
            result["groups_with_both_candidate_spellings"] += 1
        elif present == 1:
            result["groups_with_only_one_candidate_spelling"] += 1
        else:
            result["groups_with_no_candidate_spelling"] += 1
        if sum(key in rank and rank[key][0] <= AIO_NEXTUP_CANDIDATE_SCAN_LIMIT
               for key in keys) >= 2:
            result["groups_with_both_ranked_within_200"] += 1
        drops = sum(bool(flags.get(key)) for key in keys)
        if drops:
            result["groups_with_any_effective_series_drop"] += 1
        if drops == len(keys):
            result["groups_with_both_effective_series_drops"] += 1
        individual_band.append((bands[0], bands[1:], drops,
                                bool(flags.get(keys[0])),
                                any(bool(flags.get(k)) for k in keys[1:])))
    # One group exists in HomeDocker; output a *coarse band*, never series
    # key, episode ID, IMDb, rank timestamp or other identifying data.
    if len(individual_band) == 1 and len(individual_band[0][1]) == 1:
        result["single_group_canonical_band"] = individual_band[0][0]
        result["single_group_alias_band"] = individual_band[0][1][0]
        result["single_group_effective_drop_count"] = individual_band[0][2]
        result["single_group_canonical_direct_drop_effective"] = individual_band[0][3]
        result["single_group_alias_direct_drop_effective"] = individual_band[0][4]
    return result


def main():
    with closing(core.ro(core.AIO_DB)) as aio, \
         closing(core.ro(core.BRIDGE_DB)) as bridge:
        result = aggregate_candidate_rank(aio, bridge)
    print("RC3G_AUDIT=NEXTUP_CANDIDATE_RANK_DIAGNOSTIC")
    print("RC3G_MODE=SQLITE_READ_ONLY")
    for key, val in result.items():
        print("RC3G_" + key.upper() + "=" + str(val))
    print("RC3G_PREVIEW_CARD_PRESENCE_PROVEN=NO")
    print("RC3G_SOURCE_SORTING_IS_OBSERVATIONAL=YES")
    print("RC3G_HTTP_REQUESTS=0")
    print("RC3G_DATABASE_WRITES=0")
    print("RC3G_GATEWAY_CHANGED=NO")
    print("RC3G_SECRETS_PRINTED=NO")


if __name__ == "__main__":
    try:
        main()
    except (core.AuditStop, sqlite3.Error, OSError, TypeError, ValueError):
        print("RC3G_RESULT=STOP_UNQUALIFIED_SOURCE")
        print("RC3G_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
