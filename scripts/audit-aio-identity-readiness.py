#!/usr/bin/env python3
"""Read-only HomeDocker evidence for whether AIO NextUp can match two IMDb IDs.

No AIO API credentials required, no Docker mutations, no Trakt HTTP, no DB writes.
Prints only configuration booleans and known fixture aggregate counts, never uuid,
persona, tokens, event bodies or unbounded row dumps.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess

AIO_DB = "/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite"
AIO_SINK = "homedocker-trakt-bridge"
AIO_INSTANCE = "e3fe3b0"
ALIASES = ("s|tt44094505", "s|tt44051354")


class AuditFailure(Exception):
    pass


def read_only(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise AuditFailure("AIO SQLite path missing or symlink")
    db = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def audit_db(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(watch_state)")}
    required = {"uuid", "persona", "series_key", "item_key", "match_key",
                "kind", "played", "position_ms"}
    if not required <= cols:
        raise AuditFailure("AIO watch_state match-key schema changed")
    sinks = db.execute(
        """SELECT uuid,persona FROM watch_sinks WHERE addon_name=?
           AND addon_instance_id=? AND retired_at IS NULL""",
        (AIO_SINK, AIO_INSTANCE),
    ).fetchall()
    if len(sinks) != 1:
        raise AuditFailure("Expected exactly one active Bridge sink")
    user, persona = sinks[0]["uuid"], sinks[0]["persona"]
    out = {}
    for alias in ALIASES:
        rows = db.execute(
            """SELECT COUNT(*) AS count,
                      SUM(CASE WHEN match_key IS NULL THEN 1 ELSE 0 END) AS unmatched,
                      SUM(CASE WHEN played = 0 AND position_ms > 0 THEN 1 ELSE 0 END) AS resumes
                 FROM watch_state
                WHERE uuid=? AND persona=?
                  AND kind='episode' AND series_key=?""",
            (user, persona, alias),
        ).fetchone()
        out[alias[2:]] = {
            "episodes": rows["count"],
            "null_match_keys": rows["unmatched"] or 0,
            "active_resume_rows": rows["resumes"] or 0,
        }
    return out


def flags_from_container(info):
    if not isinstance(info, list) or len(info) != 1:
        raise AuditFailure("AIO container unavailable")
    item = info[0]
    cfg = item.get("Config") or {}
    env = dict(v.split("=", 1) for v in cfg.get("Env", [])
               if "=" in v and v.split("=", 1)[0] in (
                   "ID_MAPPINGS_ENABLED",
                   "WATCH_STATE_PULL_ENABLED",
                   "WATCH_STATE_REPORT_ENABLED",
               ))
    def flag(key):
        val = env.get(key)
        if val is None:
            return "default_enabled_per_upstream_source"
        if val.lower() in ("true", "1", "yes"):
            return "enabled"
        if val.lower() in ("false", "0", "no"):
            return "disabled"
        return "invalid_or_nonboolean"
    return {
        "image_id_prefix": str(item.get("Image", ""))[:19],
        "id_mappings": flag("ID_MAPPINGS_ENABLED"),
        "watch_state_pull": flag("WATCH_STATE_PULL_ENABLED"),
        "watch_state_report": flag("WATCH_STATE_REPORT_ENABLED"),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aio-db", default=AIO_DB)
    p.add_argument("--db-only", action="store_true", help="skip Docker introspection")
    args = p.parse_args()
    try:
        with closing(read_only(args.aio_db)) as db:
            groups = audit_db(db)
        docker_info = None
        if not args.db_only:
            cp = subprocess.run(
                ["docker", "container", "inspect", "aiostreams"],
                text=True, capture_output=True, check=False,
            )
            if cp.returncode:
                raise AuditFailure("Cannot inspect local AIO container")
            docker_info = flags_from_container(json.loads(cp.stdout))
        print(json.dumps({
            "monitor": "homedocker-aio-verified-identity-readiness-v1",
            "db_mode": "READ_ONLY",
            "script_db_writes": 0,
            "http_requests": 0,
            "docker_mutations": 0,
            "known_verified_pair": groups,
            "aio": docker_info,
            "known_upstream_limitation": "raw_imdb_series_ids_do_not_automatically_merge",
            "decision": "OBSERVATION_ONLY_NOT_PERMANENT_NORMALIZATION",
        }, sort_keys=True))
    except (AuditFailure, sqlite3.Error, OSError, ValueError, TypeError) as ex:
        raise SystemExit("STOP: " + str(ex)) from None


if __name__ == "__main__":
    main()
