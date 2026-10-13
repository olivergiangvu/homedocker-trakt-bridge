#!/usr/bin/env python3
"""Read-only identity-drift monitor for HomeDocker Trakt Bridge/AIOStreams.

Checks whether known, verified IMDb aliases create two independent AIO
series progress timelines. Never calls Trakt/Jellyfin, writes DB, or
changes AIOStreams. Reports local evidence only, not actual client UI.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

AIO_DB = "/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite"
BRIDGE_DB = "/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db"
SINK_NAME = "homedocker-trakt-bridge"
SINK_INSTANCE = "e3fe3b0"
FIXTURE = ("263102", "tt44094505", "tt44051354", "documented-fixture")
IMDB = re.compile(r"^tt\d{7,12}$")
MAX_ALIAS_PAIRS = 200

class AuditError(Exception):
    pass

def fail(reason):
    raise AuditError(reason)

def ro(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        fail("SQLite file missing or symlink")
    db = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db

def scope_and_profile(aio, bridge, sink_name, sink_instance):
    profile_rows = bridge.execute(
        "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
    ).fetchall()
    if len(profile_rows) != 1:
        fail("expected one connected Bridge profile")
    sinks = aio.execute(
        """SELECT uuid, persona FROM watch_sinks
           WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL""",
        (sink_name, sink_instance)
    ).fetchall()
    if len(sinks) != 1:
        fail("expected one active exact AIO sink")
    scope = (sinks[0]["uuid"], sinks[0]["persona"])
    if not all(isinstance(x, str) for x in scope):
        fail("invalid AIO scope")
    return str(profile_rows[0]["id"]), scope

def verified_alias_pairs(bridge, profile_id, include_fixture=True):
    """Bridge-learned exact show-id mapping; no title or fuzzy matching."""
    row = bridge.execute(
        """SELECT payload_json FROM media_cache
           WHERE cache_key=? AND expires_at > CAST(strftime('%s','now') AS INTEGER)""",
        ("identity-alias:v1:" + profile_id,)
    ).fetchone()
    try:
        data = json.loads(row["payload_json"]) if row else {}
    except (ValueError, TypeError):
        fail("malformed Bridge alias cache")
    if not isinstance(data, dict) or not isinstance(data.get("shows", {}), dict):
        fail("Bridge alias cache shape invalid")
    pairs = {}
    for show, value in data.get("shows", {}).items():
        if not str(show).isdigit() or not isinstance(value, dict):
            continue
        a = str(value.get("preferredMetaId") or "").strip().lower()
        b = str(value.get("traktImdb") or "").strip().lower()
        if IMDB.fullmatch(a) and IMDB.fullmatch(b) and a != b:
            pairs[str(show)] = (str(show), a, b, "learned-bridge")
    if include_fixture:
        old = pairs.get(FIXTURE[0])
        if old and old[1:3] != FIXTURE[1:3]:
            fail("known fixture conflicts with learned pair; review identity")
        pairs.setdefault(FIXTURE[0], FIXTURE)
    if len(pairs) > MAX_ALIAS_PAIRS:
        fail("too many alias pairs for bounded audit")
    return [pairs[x] for x in sorted(pairs, key=lambda k: int(k))]

def episode_summary(aio, scope, alias1, alias2):
    data = aio.execute(
        """SELECT series_key,
                  COUNT(*) rows,
                  SUM(CASE WHEN played=1 THEN 1 ELSE 0 END) watched,
                  SUM(CASE WHEN played=0 AND position_ms>0 THEN 1 ELSE 0 END) resume,
                  SUM(CASE WHEN match_key IS NOT NULL THEN 1 ELSE 0 END) mapped,
                  MAX(CASE WHEN played=1 OR position_ms>0
                           THEN COALESCE(sort_at,0) ELSE 0 END) latest_at
           FROM watch_state
           WHERE uuid=? AND persona=? AND kind='episode'
             AND series_key IN (?,?)
           GROUP BY series_key""",
        (*scope, "s|" + alias1, "s|" + alias2)
    ).fetchall()
    by_key = {
        str(r["series_key"]): {
            "rows": int(r["rows"] or 0),
            "watched": int(r["watched"] or 0),
            "resumes": int(r["resume"] or 0),
            "mapped": int(r["mapped"] or 0),
            "latest_sort_at": int(r["latest_at"] or 0),
        } for r in data
    }
    zero = dict(rows=0, watched=0, resumes=0, mapped=0, latest_sort_at=0)
    return [by_key.get("s|" + alias1, zero.copy()),
            by_key.get("s|" + alias2, zero.copy())]

def drop_preference(aio, scope, alias):
    rows = aio.execute(
        """SELECT dropped, dropped_at FROM watch_state
           WHERE uuid=? AND persona=? AND item_key=?""",
        (*scope, "s|" + alias)
    ).fetchall()
    if len(rows) > 1:
        fail("ambiguous series drop row")
    if not rows:
        return {"dropped": False, "at": 0}
    return {"dropped": bool(int(rows[0]["dropped"] or 0)),
            "at": int(rows[0]["dropped_at"] or 0)}

def decide(one, two, stale_drop):
    """Observe both histories and whether the *stale* series is truly hidden.

    AIO recentSeries filters a series only when dropped_at > row.sort_at.
    A drop older than new playback is therefore a recurrence risk.
    """
    if not one["rows"] and not two["rows"]:
        return "NO_EPISODE_EVIDENCE"
    if not one["rows"] or not two["rows"]:
        return "SINGLE_TIMELINE"
    if one["resumes"] and two["resumes"]:
        return "DUAL_RESUME_REVIEW"
    if stale_drop["dropped"] and stale_drop["at"] > one["latest_sort_at"]:
        return "MITIGATED_BY_DROP"
    return "SPLIT_TIMELINE_RISK"

def audit(aio, bridge, sink_name=SINK_NAME, sink_instance=SINK_INSTANCE, include_fixture=True):
    profile, scope = scope_and_profile(aio, bridge, sink_name, sink_instance)
    pairs = verified_alias_pairs(bridge, profile, include_fixture)
    result = {
        "monitor": "homedocker-rc7-identity-drift-v1",
        "mode": "SQLITE_READ_ONLY",
        "scope_hash": hashlib.sha256((scope[0] + "\x1f" + scope[1]).encode()).hexdigest()[:12],
        "pair_count": len(pairs),
        "pairs": [],
        "attention": 0,
        "no_http_calls": True,
        "database_writes": 0,
    }
    for show_id, preferred, canonical, origin in pairs:
        a, b = episode_summary(aio, scope, preferred, canonical)
        stale_drop = drop_preference(aio, scope, preferred)
        status = decide(a, b, stale_drop)
        # Risk counts are observations of separated timelines, not proof of cards.
        attention = status in ("SPLIT_TIMELINE_RISK", "DUAL_RESUME_REVIEW")
        result["attention"] += int(attention)
        result["pairs"].append({
            "trakt_show_id": show_id,
            "alias_imdb": preferred,
            "canonical_imdb": canonical,
            "source": origin,
            "alias_episode_rows": a["rows"],
            "alias_watched": a["watched"],
            "alias_resumes": a["resumes"],
            "canonical_episode_rows": b["rows"],
            "canonical_watched": b["watched"],
            "canonical_resumes": b["resumes"],
            "mapped_rows": a["mapped"] + b["mapped"],
            "alias_drop_active": stale_drop["dropped"],
            "alias_drop_effective": (
                stale_drop["dropped"] and stale_drop["at"] > a["latest_sort_at"]
            ),
            "status": status,
        })
    return result

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aio-db", default=AIO_DB)
    ap.add_argument("--bridge-db", default=BRIDGE_DB)
    ap.add_argument("--sink-name", default=SINK_NAME)
    ap.add_argument("--sink-instance", default=SINK_INSTANCE)
    ap.add_argument("--no-known-fixture", action="store_true")
    ap.add_argument("--json", action="store_true", help="structured redacted output")
    ap.add_argument("--fail-on-drift", action="store_true",
                    help="exit 3 for risky split timelines (monitoring only)")
    args = ap.parse_args()
    try:
        with closing(ro(args.aio_db)) as aio, closing(ro(args.bridge_db)) as bridge:
            report = audit(aio, bridge, args.sink_name, args.sink_instance,
                           not args.no_known_fixture)
    except (AuditError, sqlite3.Error, OSError) as e:
        raise SystemExit("STOP: identity drift audit unavailable: " + str(e)) from None
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("HOMEDOCKER_RC7_IDENTITY_DRIFT_MONITOR")
        print("MODE=SQLITE_READ_ONLY")
        print("SCOPE_HASH=" + report["scope_hash"])
        print("ALIAS_PAIRS=" + str(report["pair_count"]))
        for p in report["pairs"]:
            print("SHOW=%s SOURCE=%s ALIAS=%s CANONICAL=%s" %
                  (p["trakt_show_id"], p["source"], p["alias_imdb"],
                   p["canonical_imdb"]))
            print("  ALIAS_ROWS=%d WATCHED=%d RESUMES=%d DROP_EFFECTIVE=%s" %
                  (p["alias_episode_rows"], p["alias_watched"],
                   p["alias_resumes"], str(p["alias_drop_effective"]).lower()))
            print("  CANONICAL_ROWS=%d WATCHED=%d RESUMES=%d MATCHED_ROWS=%d" %
                  (p["canonical_episode_rows"], p["canonical_watched"],
                   p["canonical_resumes"], p["mapped_rows"]))
            print("  STATUS=" + p["status"])
        print("ATTENTION=" + str(report["attention"]))
        print("NO_TRAKT_OR_AIO_HTTP_CALLS=true")
        print("DATABASE_WRITES=0")
    if args.fail_on_drift and report["attention"]:
        raise SystemExit(3)

if __name__ == "__main__":
    main()
