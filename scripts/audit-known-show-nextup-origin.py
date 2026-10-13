#!/usr/bin/env python3
"""RC7 B8: read-only, one-scope, single-show AIO NextUp provenance audit.

No network, no Trakt requests, no SQLite writes. The two IMDb IDs are the
existing documented fixture, not a generic title/ID resolver.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3

AIO_DB = "/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite"
SINK_NAME = "homedocker-trakt-bridge"
SINK_INSTANCE = "e3fe3b0"
TRAKT_ID = "tt44051354"
AIO_ID = "tt44094505"
KNOWN_IDS = (TRAKT_ID, AIO_ID, "tmdb:276470", "tvdb:480791")
COLUMNS = ("item_key", "match_key", "base_id", "series_key", "video_id",
           "season", "episode", "played", "position_ms", "sort_at", "origin")
SAFE_KEY = re.compile(r"^(?:e|s|se|m)\|(?:tt\d{7,12}|tmdb:\d+|tvdb:\d+)(?::\d+){0,2}$")


def stop(reason):
    raise SystemExit("STOP: " + reason)


def short_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def safe_key(value):
    """Only print public, strictly validated provider addresses."""
    if value is None:
        return "-"
    value = str(value)
    if SAFE_KEY.fullmatch(value):
        return value
    return "opaque:" + short_hash(value)


def connect(path):
    db_path = Path(path)
    if not db_path.is_file() or db_path.is_symlink():
        stop("AIO DB missing or symlink")
    # SQLite URI path may include spaces; as_uri gives correct percent encoding.
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def audit(db, sink_name=SINK_NAME, sink_instance=SINK_INSTANCE):
    scope = db.execute(
        """SELECT uuid, persona FROM watch_sinks
           WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL""",
        (sink_name, sink_instance),
    ).fetchall()
    if len(scope) != 1:
        stop("exactly one active Bridge sink is required")
    uuid, persona = scope[0]["uuid"], scope[0]["persona"]
    if not isinstance(uuid, str) or not isinstance(persona, str):
        stop("invalid sink scope")

    actual = {r["name"] for r in db.execute("PRAGMA table_info(watch_state)")}
    missing = set(COLUMNS) - actual
    if missing:
        stop("watch_state schema lacks required columns: " + ",".join(sorted(missing)))

    # Match exact documented provider-address fragments across potential
    # item, canonical match, series and video keys; do not match title text.
    fields = ("item_key", "match_key", "base_id", "series_key", "video_id")
    where = " OR ".join("instr(COALESCE(" + f + ", ''), ?) > 0"
                        for f in fields for _ in KNOWN_IDS)
    sql = (
        "SELECT " + ",".join(COLUMNS) + " FROM watch_state "
        "WHERE uuid=? AND persona=? AND (" + where + ") "
        "ORDER BY sort_at DESC, item_key LIMIT 201"
    )
    params = [uuid, persona]
    for f in fields:
        params.extend(KNOWN_IDS)
    rows = db.execute(sql, params).fetchall()
    if len(rows) > 200:
        stop("more than 200 candidate rows: bounded diagnostic halted")

    scope_hash = short_hash(uuid + "\x1f" + persona)
    print("HOMEDOCKER_RC7_B8_KNOWN_SHOW_ORIGIN")
    print("MODE=SQLITE_READ_ONLY")
    print("AIO_SINK=CONNECTED_EXACT")
    print("SCOPE_HASH=" + scope_hash)
    print("TRAKT_SHOW_ID=263102")
    print("TRAKT_IMDB=" + TRAKT_ID)
    print("AIO_IMDB=" + AIO_ID)
    print("CANDIDATE_ROWS=" + str(len(rows)))
    by_series = defaultdict(list)
    for row in rows:
        by_series[str(row["series_key"] or "-")].append(row)
    print("SERIES_KEY_GROUP_COUNT=" + str(len(by_series)))
    for key, candidates in sorted(by_series.items()):
        by_key = [r for r in candidates if str(r["series_key"] or "-") == key]
        max_row = max(by_key, key=lambda r: int(r["sort_at"] or 0))
        print("SERIES_GROUP KEY=%s ROWS=%d WATCHED=%d UNFINISHED=%d ANCHOR_ITEM=%s ANCHOR_EP=%s" % (
            safe_key(key if key != "-" else None), len(by_key),
            sum(int(r["played"] or 0) == 1 for r in by_key),
            sum(int(r["played"] or 0) == 0 and int(r["position_ms"] or 0) > 0
                for r in by_key),
            safe_key(max_row["item_key"]),
            f'S{int(max_row["season"] or 0):02d}E{int(max_row["episode"] or 0):02d}',
        ))
        # Preserve raw provenance distinctions while keeping output bounded.
        for r in sorted(by_key, key=lambda r: (
            int(r["season"] or 0), int(r["episode"] or 0)
        ))[-20:]:
            print(
                "ROW KEY=%s MATCH=%s BASE=%s SERIES=%s VIDEO=%s EP=S%02dE%02d "
                "PLAYED=%d POS_MS=%d ORIGIN=%s" % (
                    safe_key(r["item_key"]), safe_key(r["match_key"]),
                    safe_key(r["base_id"]), safe_key(r["series_key"]),
                    safe_key(r["video_id"]), int(r["season"] or 0),
                    int(r["episode"] or 0), int(r["played"] or 0),
                    int(r["position_ms"] or 0),
                    r["origin"] if r["origin"] in ("local", "import") else "unknown",
                )
            )
    print("RESULT=READ_ONLY_COMPLETE")
    print("NOTE=Only exact connected watch-sink scope. Missing rows can still arise from metadata ID projection.")
    print("NOTE=Two different series keys can generate two NextUp candidates despite one visible show.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aio-db", default=AIO_DB)
    ap.add_argument("--sink-name", default=SINK_NAME)
    ap.add_argument("--sink-instance", default=SINK_INSTANCE)
    args = ap.parse_args()
    with closing(connect(args.aio_db)) as db:
        audit(db, args.sink_name, args.sink_instance)


if __name__ == "__main__":
    main()
