#!/usr/bin/env python3
"""R19: READ-ONLY preview of RC6 legacy -> v1.4 verified Identity Graph migration.

Uses Bridge's existing SQLite database through mode=ro + PRAGMA query_only.
Replicates the safety predicates of src/identity-legacy-import.mjs, does NOT
create or update a graph, read OAuth secret values, open AIO DB, or call HTTP.
Never prints profile identifiers, IMDb IDs, raw aliases, cache keys or tokens.

Actual import must use the tested Bridge-owned JS migration with explicit
authorization and a pre-import backup, NOT this audit script.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

DEFAULT_DB = (
    "/var/lib/docker/volumes/"
    "homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db"
)
PROFILE = re.compile(r"^[a-f0-9]{24}$")
IMDB = re.compile(r"^tt[0-9]{7,12}$")
MAX_SHOWS = 10000

class PreviewFailure(Exception):
    pass

def valid_imdb(value):
    return (isinstance(value, str)
            and bool(IMDB.fullmatch(value.lower())))

def proposed_graph(legacy):
    if not isinstance(legacy, dict) or not isinstance(legacy.get("shows"), dict):
        return "NO_VALID_LEGACY_DATA", 0, 0
    shows = legacy["shows"]
    if not 1 <= len(shows) <= MAX_SHOWS:
        return "EMPTY_OR_UNBOUNDED_LEGACY", 0, 0
    claims = {}
    aliased = 0
    for key, row in shows.items():
        if not isinstance(key, str) or not key.isdecimal():
            return "INVALID_OR_UNVERIFIED_LEGACY_ROW", 0, 0
        n = int(key)
        if n < 1 or n > 9007199254740991 or not isinstance(row, dict):
            return "INVALID_OR_UNVERIFIED_LEGACY_ROW", 0, 0
        canonical = row.get("traktImdb")
        alias = row.get("preferredMetaId")
        updated = row.get("updatedAt")
        if not (valid_imdb(canonical) and valid_imdb(alias)
                and isinstance(updated, (int, float))
                and not isinstance(updated, bool)
                and float(updated).is_integer()
                and 0 < updated <= 9007199254740991):
            return "INVALID_OR_UNVERIFIED_LEGACY_ROW", 0, 0
        canon = canonical.lower()
        spelling = alias.lower()
        ids = [canon] if canon == spelling else [canon, spelling]
        for name in ids:
            owner = claims.get(name)
            if owner is not None and owner != n:
                return "CROSS_SHOW_IDENTITY_CONFLICT", 0, 0
            claims[name] = n
        if canon != spelling:
            aliased += 1
    if not aliased:
        return "NO_DIFFERING_ALIASES", len(shows), 0
    return "DRY_RUN_READY", len(shows), aliased

def preview(db_path=DEFAULT_DB, now=None):
    now = int(time.time() if now is None else now)
    p = Path(db_path)
    if p.is_symlink() or not p.is_file():
        raise PreviewFailure("Bridge database path is missing or symlinked")
    try:
        with closing(sqlite3.connect(
            p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )) as db:
            db.execute("PRAGMA query_only=ON")
            # Fetch only legacy/graph cache keys, not other application data.
            rows = db.execute(
                "SELECT cache_key, payload_json, expires_at "
                "FROM media_cache WHERE cache_key LIKE 'identity-alias:v1:%' "
                "OR cache_key LIKE 'identity-graph:v1:%' LIMIT 1001"
            ).fetchall()
            if len(rows) >= 1001:
                raise PreviewFailure("Identity cache inventory exceeded bound")
            old = [r for r in rows if r[0].startswith("identity-alias:v1:")]
            graphs = [r for r in rows if r[0].startswith("identity-graph:v1:")]
            if len(old) != 1:
                return _result("LEGACY_PROFILE_COUNT_NOT_ONE",
                               len(old), len(graphs), 0, 0)
            name, serialized, expires = old[0]
            profile_id = name.removeprefix("identity-alias:v1:")
            if not PROFILE.fullmatch(profile_id):
                return _result("INVALID_PROFILE_ID", 1, len(graphs), 0, 0)
            linked = db.execute(
                "SELECT 1 FROM profiles WHERE id=? "
                "AND access_token_enc IS NOT NULL LIMIT 1", (profile_id,)
            ).fetchone()
            if not linked:
                return _result("PROFILE_NOT_CONNECTED", 1, len(graphs), 0, 0)
            matching_graph = [
                row for row in graphs
                if row[0] == "identity-graph:v1:" + profile_id
            ]
            if matching_graph:
                return _result("GRAPH_ALREADY_PRESENT_DO_NOT_OVERWRITE",
                               1, len(graphs), 0, 0)
            if expires is None or int(expires) < now:
                return _result("LEGACY_CACHE_EXPIRED", 1, len(graphs), 0, 0)
            try:
                legacy = json.loads(serialized)
            except (TypeError, ValueError):
                return _result("LEGACY_JSON_INVALID", 1, len(graphs), 0, 0)
            status, shows, aliases = proposed_graph(legacy)
            return _result(status, 1, len(graphs), shows, aliases)
    except sqlite3.Error as exc:
        raise PreviewFailure("Read-only Bridge SQLite audit failed") from exc

def _result(status, legacy_records, graph_records, shows, aliases):
    return {
        "audit": "R19_RC6_LEGACY_GRAPH_IMPORT_PREVIEW",
        "status": status,
        "safe_to_consider_controlled_migration": status == "DRY_RUN_READY",
        "connected_legacy_records": legacy_records,
        "existing_identity_graph_records": graph_records,
        "verified_show_candidates": shows,
        "alias_group_candidates": aliases,
        "db_mode": "SQLITE_MODE_RO_QUERY_ONLY",
        "script_db_writes": 0,
        "aio_db_reads_or_writes": 0,
        "http_or_trakt_calls": 0,
        "profile_ids_imdb_ids_tokens_or_history_printed": False,
        "migration_applied": False,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-db", default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        print(json.dumps(preview(args.bridge_db), indent=2))
    except (PreviewFailure, OSError, ValueError):
        print("STOP: R19 read-only preview unavailable; no migration made", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
