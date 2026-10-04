#!/usr/bin/env bash
set -euo pipefail

# Runtime qualification for the detect-only AIO reconciler.
#
# Proves that one pending AIO resume candidate:
#   1. exists before a Bridge container restart;
#   2. survives that restart with the same firstSeenAt;
#   3. may coalesce newer same-item AIO rows without losing firstSeenAt;
#   4. settles exactly once after the configured quiet window; and
#   5. remains detect-only (writesTrakt=false).
#
# This script never writes either SQLite database and never calls Trakt.
# Its only intentional mutation is "docker restart" after pending state is
# observed and persisted in the Bridge database.

ITEM="${1:-${ITEM:-}}"

if [[ -z "$ITEM" ]]; then
  cat >&2 <<'USAGE'
Usage:
  sudo scripts/qualify-aio-restart-persistence.sh 'e|tt12345678:1:2'

Optional environment overrides:
  AIO_DB=/path/to/aiostreams/db.sqlite
  BRIDGE_DB=/path/to/bridge.db
  CONTAINER=running-container-name
  POLL_SECONDS=2
  FRESH_TIMEOUT_SECONDS=600
  PENDING_TIMEOUT_SECONDS=180
  POST_RESTART_TIMEOUT_SECONDS=30
  SETTLE_TIMEOUT_SECONDS=480
USAGE
  exit 2
fi

AIO_DB="${AIO_DB:-/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite}"
BRIDGE_DB="${BRIDGE_DB:-/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db}"
POLL_SECONDS="${POLL_SECONDS:-2}"
FRESH_TIMEOUT_SECONDS="${FRESH_TIMEOUT_SECONDS:-600}"
PENDING_TIMEOUT_SECONDS="${PENDING_TIMEOUT_SECONDS:-180}"
POST_RESTART_TIMEOUT_SECONDS="${POST_RESTART_TIMEOUT_SECONDS:-30}"
SETTLE_TIMEOUT_SECONDS="${SETTLE_TIMEOUT_SECONDS:-480}"

for path in "$AIO_DB" "$BRIDGE_DB"; do
  if [[ ! -r "$path" ]]; then
    echo "ERROR: database is not readable: $path" >&2
    exit 2
  fi
done

if [[ -z "${CONTAINER:-}" ]]; then
  mapfile -t matches < <(
    docker ps --format '{{.Names}} {{.Image}}' \
      | awk '$2 ~ /(^|\/)homedocker-trakt-bridge(@|:|$)/ {print $1}'
  )

  if (( ${#matches[@]} != 1 )); then
    echo "ERROR: expected exactly one running HomeDocker Trakt Bridge container." >&2
    printf 'MATCH=%s\n' "${matches[@]-}" >&2
    echo "Set CONTAINER explicitly if auto-discovery is ambiguous." >&2
    exit 2
  fi

  CONTAINER="${matches[0]}"
fi

docker inspect "$CONTAINER" >/dev/null

quiet_from_container="$(
  docker inspect "$CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | awk -F= '$1=="AIO_RECONCILE_QUIET_SECONDS" {print $2; exit}'
)"

QUIET_SECONDS="${QUIET_SECONDS:-${quiet_from_container:-300}}"

case "$QUIET_SECONDS" in
  ''|*[!0-9]*)
    echo "ERROR: QUIET_SECONDS must be an integer." >&2
    exit 2
    ;;
esac

mode="$(
  docker inspect "$CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | awk -F= '$1=="AIO_RECONCILER_MODE" {print $2; exit}'
)"

if [[ "$mode" != "detect" ]]; then
  echo "ERROR: AIO_RECONCILER_MODE must be detect; got '${mode:-unset}'." >&2
  exit 2
fi

echo "============================================================"
echo " AIO RECONCILER — RESTART-PERSISTENCE QUALIFICATION"
echo "============================================================"
echo "ITEM=$ITEM"
echo "CONTAINER=$CONTAINER"
echo "QUIET_SECONDS=$QUIET_SECONDS"
docker inspect "$CONTAINER" \
  --format 'IMAGE={{.Config.Image}} STATUS={{.State.Status}} STARTED={{.State.StartedAt}}'

python3 - \
  "$AIO_DB" \
  "$BRIDGE_DB" \
  "$ITEM" \
  "$CONTAINER" \
  "$QUIET_SECONDS" \
  "$POLL_SECONDS" \
  "$FRESH_TIMEOUT_SECONDS" \
  "$PENDING_TIMEOUT_SECONDS" \
  "$POST_RESTART_TIMEOUT_SECONDS" \
  "$SETTLE_TIMEOUT_SECONDS" <<'PY'
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

(
    aio_db,
    bridge_db,
    item,
    container,
    quiet_seconds,
    poll_seconds,
    fresh_timeout_seconds,
    pending_timeout_seconds,
    post_restart_timeout_seconds,
    settle_timeout_seconds,
) = sys.argv[1:]

quiet_seconds = int(quiet_seconds)
poll_seconds = int(poll_seconds)
fresh_timeout_seconds = int(fresh_timeout_seconds)
pending_timeout_seconds = int(pending_timeout_seconds)
post_restart_timeout_seconds = int(post_restart_timeout_seconds)
settle_timeout_seconds = int(settle_timeout_seconds)


def now_ms():
    return int(time.time() * 1000)


def clock(position_ms):
    seconds = int(position_ms) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def docker_started_iso():
    return subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.State.StartedAt}}"],
        text=True,
    ).strip()


def iso_to_ms(value):
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    # Docker can emit nanoseconds while datetime.fromisoformat supports
    # microseconds. Truncate only the fractional component.
    if "." in text:
        head, tail = text.split(".", 1)
        positions = [p for p in (tail.find("+"), tail.find("-")) if p >= 0]
        if positions:
            p = min(positions)
            text = f"{head}.{tail[:p][:6]}{tail[p:]}"
        else:
            text = f"{head}.{tail[:6]}"

    return int(datetime.fromisoformat(text).timestamp() * 1000)


def read_aio():
    con = sqlite3.connect(f"file:{aio_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT
                uuid, persona, item_key, kind, media_type, base_id,
                season, episode, video_id, position_ms, duration_ms,
                played, origin, updated_at, last_played_at
            FROM watch_state
            WHERE item_key=?
            """,
            (item,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def read_target_pending():
    con = sqlite3.connect(f"file:{bridge_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT payload_json
            FROM media_cache
            WHERE cache_key='aio-reconcile:v2:pending'
            """
        ).fetchone()

        if not row or not row["payload_json"]:
            return []

        payload = json.loads(row["payload_json"])
        return [
            value
            for value in payload.values()
            if isinstance(value, dict) and value.get("itemKey") == item
        ]
    finally:
        con.close()


def settlements_for_first_seen(first_seen_at):
    con = sqlite3.connect(f"file:{bridge_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, event_id, status, detail, created_at
            FROM event_log
            WHERE event='reconcile'
              AND detail LIKE ?
            ORDER BY id ASC
            """,
            (f'%"itemKey":"{item}"%',),
        ).fetchall()

        found = []
        for row in rows:
            try:
                detail = json.loads(row["detail"])
            except Exception:
                continue

            if detail.get("itemKey") != item:
                continue
            if int(detail.get("firstSeenAt") or 0) != int(first_seen_at):
                continue

            found.append((dict(row), detail))

        return found
    finally:
        con.close()


def is_resume_row(row):
    return bool(
        row
        and row.get("origin") == "local"
        and int(row.get("played") or 0) == 0
        and int(row.get("position_ms") or 0) > 0
        and int(row.get("duration_ms") or 0) > 0
        and row.get("kind") in ("movie", "episode")
    )


script_started_ms = now_ms()
baseline = read_aio()

if not baseline:
    raise SystemExit("FAIL: target AIO watch_state row is missing")

if read_target_pending():
    raise SystemExit(
        "FAIL: target already has pending state before qualification; "
        "allow it to settle first"
    )

baseline_updated = int(baseline["updated_at"])

print()
print("=== 1. BASELINE ===")
print(f"POSITION_MS={baseline['position_ms']}")
print(f"POSITION_CLOCK={clock(baseline['position_ms'])}")
print(f"UPDATED_AT={baseline_updated}")

print()
print("ACTION_REQUIRED=Generate one fresh local resume-state update for this item,")
print("then leave playback idle. Do not restart the Bridge manually.")
print("WAITING_FOR_FRESH_AIO_UPDATE=YES")

deadline = time.time() + fresh_timeout_seconds
fresh = None

while time.time() < deadline:
    current = read_aio()
    if (
        current
        and int(current["updated_at"]) > baseline_updated
        and is_resume_row(current)
    ):
        fresh = current
        break
    time.sleep(poll_seconds)

if not fresh:
    raise SystemExit("FAIL: no fresh local AIO resume row within timeout")

print()
print("FRESH_AIO_UPDATE=PASS")
print(f"POSITION_MS={fresh['position_ms']}")
print(f"POSITION_CLOCK={clock(fresh['position_ms'])}")
print(f"UPDATED_AT={fresh['updated_at']}")

print()
print("=== 2. WAIT FOR PERSISTED PENDING ===")

deadline = time.time() + pending_timeout_seconds
captured = None

while time.time() < deadline:
    current = read_aio()
    if not current:
        raise SystemExit("FAIL: target AIO row disappeared")

    pending_rows = read_target_pending()
    if len(pending_rows) > 1:
        raise SystemExit(
            f"FAIL: expected at most one target pending row; got {len(pending_rows)}"
        )

    if pending_rows:
        pending = pending_rows[0]
        first_seen = int(pending.get("firstSeenAt") or 0)
        if first_seen >= script_started_ms:
            captured = (current, pending)
            break

    time.sleep(poll_seconds)

if not captured:
    raise SystemExit("FAIL: fresh AIO row never entered persisted pending state")

current, pending = captured
first_seen = int(pending.get("firstSeenAt") or 0)

if first_seen <= 0:
    raise SystemExit("FAIL: pending firstSeenAt is invalid")

if settlements_for_first_seen(first_seen):
    raise SystemExit("FAIL: pending chain settled before restart")

print("PENDING_CAPTURED=PASS")
print(f"FIRST_SEEN_AT_MS={first_seen}")
print(f"PENDING_UPDATED_AT={pending.get('updatedAt')}")
print(f"PENDING_POSITION_MS={pending.get('positionMs')}")

print()
print("=== 3. RESTART WHILE PENDING ===")
restart_requested_ms = now_ms()
print(f"RESTART_REQUEST_AT_MS={restart_requested_ms}")

subprocess.run(
    ["docker", "restart", container],
    check=True,
    stdout=subprocess.DEVNULL,
)

restart_iso = docker_started_iso()
restart_ms = iso_to_ms(restart_iso)

print(f"POST_RESTART_STARTED_AT={restart_iso}")
print(f"POST_RESTART_STARTED_MS={restart_ms}")

if restart_ms <= first_seen:
    raise SystemExit(
        "FAIL: restarted container timestamp is not after pending firstSeenAt"
    )

print("RESTART_AFTER_PENDING=PASS")

print()
print("=== 4. VERIFY SAME PENDING CHAIN SURVIVES ===")

deadline = time.time() + post_restart_timeout_seconds
survived = None

while time.time() < deadline:
    pending_rows = read_target_pending()

    if len(pending_rows) > 1:
        raise SystemExit(
            f"FAIL: expected at most one target pending row; got {len(pending_rows)}"
        )

    if pending_rows:
        candidate = pending_rows[0]
        if int(candidate.get("firstSeenAt") or 0) == first_seen:
            survived = candidate
            break

    if settlements_for_first_seen(first_seen):
        raise SystemExit(
            "FAIL: pending chain settled before post-restart persistence "
            "could be verified"
        )

    time.sleep(1)

if not survived:
    raise SystemExit("FAIL: same pending chain did not survive restart")

print("PENDING_SURVIVED_RESTART=PASS")
print(f"PRE_FIRST_SEEN_AT={first_seen}")
print(f"POST_FIRST_SEEN_AT={survived.get('firstSeenAt')}")

print()
print("=== 5. FOLLOW COALESCED CHAIN TO SETTLEMENT ===")

deadline = time.time() + settle_timeout_seconds
last_pending_updated = None
last_aio_updated = None
observed_refresh = False
final_rows = None

while time.time() < deadline:
    current = read_aio()
    if not current:
        raise SystemExit("FAIL: target AIO row disappeared")

    aio_updated = int(current["updated_at"])
    if aio_updated != last_aio_updated:
        print(
            "AIO_STATE "
            f"updatedAt={aio_updated} "
            f"position={current['position_ms']} "
            f"clock={clock(current['position_ms'])} "
            f"played={current['played']} "
            f"origin={current['origin']}"
        )
        if last_aio_updated is not None and aio_updated > last_aio_updated:
            observed_refresh = True
        last_aio_updated = aio_updated

    pending_rows = read_target_pending()

    if len(pending_rows) > 1:
        raise SystemExit(
            f"FAIL: expected at most one target pending row; got {len(pending_rows)}"
        )

    if pending_rows:
        pending = pending_rows[0]
        pending_first_seen = int(pending.get("firstSeenAt") or 0)
        pending_updated = int(pending.get("updatedAt") or 0)

        if pending_first_seen != first_seen:
            raise SystemExit(
                "FAIL: firstSeenAt changed across same-item pending chain: "
                f"{first_seen} -> {pending_first_seen}"
            )

        if pending_updated != last_pending_updated:
            print(
                "PENDING_STATE "
                f"updatedAt={pending_updated} "
                f"position={pending.get('positionMs')} "
                f"firstSeenAt={pending_first_seen}"
            )
            if last_pending_updated is not None and pending_updated > last_pending_updated:
                observed_refresh = True
            last_pending_updated = pending_updated

        if settlements_for_first_seen(first_seen):
            raise SystemExit(
                "FAIL: terminal settlement exists while the same pending chain "
                "is still present"
            )

    else:
        settlements = settlements_for_first_seen(first_seen)
        if settlements:
            final_rows = settlements
            break

        if not is_resume_row(current):
            raise SystemExit(
                "FAIL: pending chain disappeared without settlement because "
                "the current AIO row became non-resumable"
            )

    time.sleep(poll_seconds)

if final_rows is None:
    final_rows = settlements_for_first_seen(first_seen)

if not final_rows:
    raise SystemExit("FAIL: restart-surviving pending chain did not settle")

if len(final_rows) != 1:
    raise SystemExit(
        "FAIL: expected exactly one terminal settlement for the pending chain; "
        f"got {len(final_rows)}"
    )

row, detail = final_rows[0]
settled_ms = int(row["created_at"]) * 1000
final_updated_at = int(detail.get("updatedAt") or 0)
detail_first_seen = int(detail.get("firstSeenAt") or 0)
decision = detail.get("decision")
candidate = detail.get("candidate")
writes_trakt = detail.get("writesTrakt")

print()
print("=== 6. FINAL QUALIFICATION ===")

results = []


def check(name, condition, extra=None):
    condition = bool(condition)
    results.append(condition)
    line = f"{name}={'PASS' if condition else 'FAIL'}"
    if extra:
        line += f" — {extra}"
    print(line)


check(
    "PENDING_EXISTED_BEFORE_RESTART",
    first_seen < restart_ms,
    f"{first_seen} < {restart_ms}",
)
check(
    "PENDING_SURVIVED_RESTART",
    detail_first_seen == first_seen,
    f"firstSeenAt={detail_first_seen}",
)
check(
    "FIRST_SEEN_PRESERVED",
    detail_first_seen == first_seen,
)
check(
    "RESTART_BEFORE_SETTLEMENT",
    restart_ms < settled_ms,
    f"{restart_ms} < {settled_ms}",
)
check(
    "PENDING_CLEARED",
    len(read_target_pending()) == 0,
)
check(
    "SETTLED_EXACTLY_ONCE",
    len(final_rows) == 1,
)
check(
    "QUIET_WINDOW_RESPECTED",
    settled_ms + 999 >= final_updated_at + (quiet_seconds * 1000),
    (
        f"settled={settled_ms}, finalUpdated={final_updated_at}, "
        f"quietSeconds={quiet_seconds}"
    ),
)
check(
    "DETECT_ONLY",
    writes_trakt is False,
)

semantic_ok = (
    (
        decision == "covered_by_homedocker_playback_delivery"
        and candidate is False
    )
    or
    (
        decision == "settled_missing_homedocker_playback_delivery"
        and candidate is True
    )
)

check(
    "TERMINAL_SEMANTICS",
    semantic_ok,
    f"decision={decision}, candidate={candidate}",
)

print()
print(f"FINAL_EVENT_ID={row['event_id']}")
print(f"FINAL_UPDATED_AT={final_updated_at}")
print(f"SETTLED_AT_MS={settled_ms}")
print(f"FINAL_DECISION={decision}")
print(f"FINAL_CANDIDATE={candidate}")
print(f"WRITES_TRAKT={writes_trakt}")
print(f"COALESCED_NEWER_STATE={'YES' if observed_refresh else 'NO'}")
print()

if not all(results):
    raise SystemExit("AIO_RESTART_PERSISTENCE_QUALIFICATION=FAIL")

print("============================================================")
print(" AIO_RESTART_PERSISTENCE_QUALIFICATION=PASS")
print(" PENDING_PERSISTENCE=PASS")
print(" SINGLE_SETTLEMENT=PASS")
print(" DETECT_ONLY=PASS")
print("============================================================")

if decision == "covered_by_homedocker_playback_delivery":
    print("FINAL_RESULT=COVERED")
else:
    print("FINAL_RESULT=MISSING_CANDIDATE")

print(
    "NOTE=Coverage outcome is independent from restart persistence; "
    "newer same-item AIO rows may coalesce while preserving firstSeenAt."
)
PY
