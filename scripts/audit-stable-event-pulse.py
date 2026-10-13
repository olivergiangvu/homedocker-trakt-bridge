#!/usr/bin/env python3
"""Read-only evidence pulse for v1.3 RC-to-stable gating.

Uses only Bridge's locally retained event_log. Counts observed evidence;
never infers network-wide 429, native-Trakt client events, or a 72h soak.
No authentication token, event ID, raw event payload, or profile ID printed.
"""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

DB_PATH = "/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db"
MAX_EVENTS = 1000
TERMINAL_REASONS = {
    "terminal_pause_requires_completion",
    "terminal_stop_not_confirmed_played",
}
COMPARE_DECISIONS = {
    "trakt_same_or_newer", "trakt_stale_candidate",
    "trakt_playback_missing_candidate", "trakt_compare_ambiguous",
}


class PulseError(Exception):
    pass


def database(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise PulseError("Bridge SQLite file missing or symlink")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def safe_detail(raw):
    try:
        obj = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def classify_events(rows, *, min_at, max_at, retained_count):
    stats = Counter()
    decisions = Counter()
    for row in rows:
        event = str(row["event"] or "")
        status = str(row["status"] or "")
        detail = safe_detail(row["detail"])
        if event == "pull":
            if status in ("ok", "cached", "stale", "error"):
                stats["pull_" + status] += 1
        if status == "error":
            stats["event_errors"] += 1
        if event in ("start", "pause", "stop", "played", "unplayed"):
            stats["playback_events"] += 1
            if status == "error":
                stats["playback_errors"] += 1
        if detail.get("ignored") in TERMINAL_REASONS and status == "ignored":
            stats["terminal_ignored"] += 1
        if event in ("pause", "stop") and status == "error" and (
            detail.get("upstreamStatus") == 422
        ):
            stats["explicit_terminal_422_errors"] += 1
        if status in ("error", "stale") and (
            detail.get("upstreamStatus") == 429
            or str(detail.get("upstreamError") or "").lower() in (
                "rate_limited", "rate_limit", "trakt_rate_limited", "http_429"
            )
        ):
            stats["explicit_rate_limit_signals"] += 1
        if event == "canonical:verification":
            stats["canonical_verifications"] += 1
            if status == "error":
                stats["canonical_verification_errors"] += 1
        if event == "reconcile" and detail.get("action") == "aio-reconcile:compare-settled":
            stats["compare_terminal"] += 1
            decision = detail.get("decision")
            if decision in COMPARE_DECISIONS:
                decisions[decision] += 1
            else:
                stats["compare_unknown_decision"] += 1
            if detail.get("writesTrakt") is not False:
                stats["compare_write_safety_warning"] += 1

    return {
        "monitor": "homedocker-stable-event-pulse-v1",
        "mode": "SQLITE_READ_ONLY",
        "window_hours": int((max_at - min_at) / 3600),
        "window_start_unix": min_at,
        "window_end_unix": max_at,
        "retained_events_total": retained_count,
        "window_events": len(rows),
        "retention_cap": MAX_EVENTS,
        "event_log_may_be_truncated": retained_count >= MAX_EVENTS,
        "counts": dict(sorted(stats.items())),
        "compare_decisions": dict(sorted(decisions.items())),
        "data_limitations": [
            "counts only locally retained Bridge events",
            "cannot measure native-Trakt client writes",
            "explicit 429/422 counts may undercount undocumented error shapes",
            "absence of observed errors does not prove soak or stable eligibility",
        ],
        "trakt_http_calls": 0,
        "database_writes": 0,
    }


def audit(db, *, now, hours):
    profiles = db.execute(
        "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
    ).fetchall()
    if len(profiles) != 1:
        raise PulseError("exactly one connected Bridge profile required")
    start = int(now - hours * 3600)
    rows = db.execute(
        """SELECT event,status,detail,created_at FROM event_log
           WHERE profile_id=? AND created_at BETWEEN ? AND ?
           ORDER BY id DESC LIMIT ?""",
        (profiles[0]["id"], start, int(now), MAX_EVENTS + 1),
    ).fetchall()
    if len(rows) > MAX_EVENTS:
        raise PulseError("more than retained budget; refuse incomplete window read")
    retained = db.execute(
        "SELECT COUNT(*) n FROM event_log WHERE profile_id=?",
        (profiles[0]["id"],),
    ).fetchone()
    return classify_events(rows, min_at=start, max_at=int(now),
                           retained_count=int(retained["n"] or 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bridge-db", default=DB_PATH)
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()
    if not 1 <= args.hours <= 168:
        ap.error("--hours must be 1..168")
    try:
        with closing(database(args.bridge_db)) as db:
            result = audit(db, now=time.time(), hours=args.hours)
    except (sqlite3.Error, OSError, PulseError) as e:
        raise SystemExit("STOP: stable pulse unavailable: " + str(e)) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
