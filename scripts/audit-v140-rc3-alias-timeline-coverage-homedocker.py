#!/usr/bin/env python3
"""RC3-E: read-only AIO watched-state / Bridge verified IMDb alias coverage.

Purpose: Determine whether independently VERIFIED alias groups actually have
progress under two IMDb spellings in the exact connected AIO sink. Does NOT
claim the groups appear on Jellyfin NextUp, nor attempt to manufacture cards.

No HTTP, token/session, Trakt, database writes, Docker changes or raw IDs,
show titles or playback timestamps printed. It only emits aggregate counts.
"""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import stat

AIO_DB = Path("/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite")
BRIDGE_DB = Path("/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db")
SINK_NAME = "homedocker-trakt-bridge"
SINK_INSTANCE = "e3fe3b0"
IMDB = re.compile(r"tt[0-9]{7,12}\Z")
MAX_GRAPH_SHOWS = 10000
MAX_ALIASES_PER_SHOW = 12


class AuditStop(Exception):
    pass


def ro(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise AuditStop("DATABASE_PATH_UNSAFE")
        db = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        db.execute("PRAGMA query_only=ON")
        return db
    except (OSError, sqlite3.Error):
        raise AuditStop("DATABASE_UNAVAILABLE") from None


def verified_alias_groups(raw):
    if not isinstance(raw, dict):
        raise AuditStop("GRAPH_NOT_OBJECT")
    shows = raw.get("shows")
    conflicts = raw.get("conflicts", [])
    if not isinstance(shows, dict) or len(shows) > MAX_GRAPH_SHOWS \
            or not isinstance(conflicts, list) or len(conflicts) > 100:
        raise AuditStop("GRAPH_UNTRUSTED")
    quarantined = {
        row.get("id") for row in conflicts
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    groups = []
    seen = set()
    for trakt_id, value in shows.items():
        if not trakt_id.isdigit() or not isinstance(value, dict):
            continue
        main = value.get("canonicalImdb")
        aliases = value.get("aliases")
        if not isinstance(main, str) or not IMDB.fullmatch(main) \
                or not isinstance(aliases, list) \
                or len(aliases) > MAX_ALIASES_PER_SHOW:
            continue
        if any(not isinstance(a, str) or not IMDB.fullmatch(a)
               or a == main for a in aliases):
            continue
        spellings = [main, *dict.fromkeys(aliases)]
        if any(s in quarantined or s in seen for s in spellings):
            # Fail entire audit on conflicts: never convert ambiguity to a
            # claim that two watch-state timelines are the same show.
            raise AuditStop("IDENTITY_GRAPH_CONFLICT")
        seen.update(spellings)
        if len(spellings) >= 2:
            groups.append(spellings)
    return len(shows), groups


def aggregate_from_db(aio, bridge):
    profiles = bridge.execute(
        "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
    ).fetchall()
    if len(profiles) != 1:
        raise AuditStop("CONNECTED_PROFILE_NOT_UNIQUE")
    profile_id = profiles[0][0]
    sinks = aio.execute(
        "SELECT uuid, persona FROM watch_sinks "
        "WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL",
        (SINK_NAME, SINK_INSTANCE)
    ).fetchall()
    if len(sinks) != 1:
        raise AuditStop("WATCH_SINK_NOT_UNIQUE")
    uuid, persona = sinks[0]
    if not isinstance(uuid, str) or not isinstance(persona, str):
        raise AuditStop("WATCH_SINK_SCOPE_INVALID")
    row = bridge.execute(
        "SELECT payload_json FROM media_cache "
        "WHERE cache_key=? AND expires_at > CAST(strftime('%s','now') AS INTEGER)",
        ("identity-graph:v1:" + profile_id,)
    ).fetchone()
    if not row:
        raise AuditStop("VERIFIED_GRAPH_NOT_AVAILABLE")
    try:
        graph = json.loads(row[0])
    except (ValueError, TypeError):
        raise AuditStop("GRAPH_JSON_INVALID") from None
    show_count, groups = verified_alias_groups(graph)
    counts = {
        "graph_shows": show_count,
        "verified_alias_groups": len(groups),
        "alias_groups_with_both_timelines": 0,
        "alias_groups_canonical_timeline_only": 0,
        "alias_groups_alias_timeline_only": 0,
        "alias_groups_with_no_episode_rows": 0,
        "alias_groups_with_dual_active_resumes": 0,
        "canonical_episode_rows": 0,
        "alias_episode_rows": 0,
    }
    # Bounded to <=12 spellings/group, <=10K groups; SQL values always bound.
    for spellings in groups:
        marks = ",".join("?" for _ in spellings)
        observed = aio.execute(
            "SELECT series_key, COUNT(*), "
            "SUM(CASE WHEN played=0 AND position_ms>0 THEN 1 ELSE 0 END) "
            "FROM watch_state "
            "WHERE uuid=? AND persona=? AND kind='episode' "
            "AND series_key IN (" + marks + ") GROUP BY series_key",
            (uuid, persona, *["s|" + s for s in spellings]),
        ).fetchall()
        records = {key: (int(n or 0), int(active or 0))
                   for key, n, active in observed}
        canon_rows, canon_resume = records.get("s|" + spellings[0], (0, 0))
        alias_data = [records.get("s|" + x, (0, 0))
                      for x in spellings[1:]]
        alias_rows = sum(x[0] for x in alias_data)
        alias_resume = sum(x[1] for x in alias_data)
        counts["canonical_episode_rows"] += canon_rows
        counts["alias_episode_rows"] += alias_rows
        if canon_rows and alias_rows:
            counts["alias_groups_with_both_timelines"] += 1
        elif canon_rows:
            counts["alias_groups_canonical_timeline_only"] += 1
        elif alias_rows:
            counts["alias_groups_alias_timeline_only"] += 1
        else:
            counts["alias_groups_with_no_episode_rows"] += 1
        if canon_resume and alias_resume:
            counts["alias_groups_with_dual_active_resumes"] += 1
    return counts


def main():
    with closing(ro(AIO_DB)) as aio, closing(ro(BRIDGE_DB)) as bridge:
        counts = aggregate_from_db(aio, bridge)
    print("RC3E_AUDIT=VERIFIED_ALIAS_TIMELINE_COVERAGE")
    print("RC3E_DATABASES=SQLITE_READ_ONLY")
    for key, value in counts.items():
        print("RC3E_" + key.upper() + "=" + str(value))
    print("RC3E_NEXTUP_PRESENCE_PROVEN=NO")
    print("RC3E_HTTP_REQUESTS=0")
    print("RC3E_WATCH_HISTORY_WRITES=0")
    print("RC3E_SECRETS_PRINTED=NO")
    print("RC3E_GATEWAY_CHANGED=NO")


if __name__ == "__main__":
    try:
        main()
    except (AuditStop, sqlite3.Error, OSError, ValueError):
        print("RC3E_RESULT=STOP_OR_SOURCE_UNAVAILABLE")
        print("RC3E_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
