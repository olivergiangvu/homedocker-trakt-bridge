#!/usr/bin/env bash
# R23B2 no-token HomeDocker trial observation. REQUIRES explicit operator consent.
# This script is NOT the v1.4 Gateway release/deployment path.
set -euo pipefail
if [[ "$#" -ne 3 || "$1" != "--apply" || "$2" != "--confirm" ||
      "$3" != "R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY" ]]; then
  echo "R23B2_OBSERVER_STOP_EXPLICIT_CONFIRMATION_REQUIRED" >&2
  exit 3
fi
if [[ "$(id -u)" != 0 ]]; then
  echo "R23B2_OBSERVER_STOP_ROOT_REQUIRED" >&2
  exit 3
fi
for tool in docker nsenter python3 grep mktemp; do
  command -v "$tool" >/dev/null || exit 3
done
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LAUNCHER="$ROOT/scripts/host-trial-r23b2.py"
AUDIT="$ROOT/scripts/audit-r23b2-host-relay-prereqs.py"
CANARY="homedocker-v140-r23b-canary"
SIDECAR="homedocker-v140-r23b2-unix-sidecar"
SOCKETDIR="/run/homedocker-r23b2-aio"
RECEIPTDIR="/var/lib/homedocker-r23b2-trial"
LOGDIR="$(mktemp -d /var/tmp/r23b2-observe.XXXXXXXX)"
chmod 0700 "$LOGDIR"
LOG="$LOGDIR/launcher.log"
JOB_PID=""
BEFORE=""
READY=0

finish(){
  local rc=$?
  trap - EXIT
  if [[ -n "$JOB_PID" ]]; then
    # Never kill a still-in-progress launcher in this wrapper's cleanup.
    # The launcher owns its own finally block and TTL.
    echo "=== R23B2 WAITING FOR GUARDED LAUNCHER TO EXIT ==="
    if ! wait "$JOB_PID"; then rc=5; fi
    echo "=== R23B2 SANITIZED PHASE TRACE ==="
    grep -E '^R23B2_(PHASE_|STOP_|OWNED_RESOURCES_CLEANUP_PASS)' "$LOG" || true
    if [[ "$READY" -ne 1 ]]; then
      echo "R23B2_OBSERVER_STOP_READY_NOT_SEEN"
      rc=5
    fi
    if ! grep -qx 'R23B2_OWNED_RESOURCES_CLEANUP_PASS' "$LOG"; then
      echo "R23B2_OBSERVER_STOP_CLEANUP_MARKER_MISSING"
      rc=5
    fi
    if ! docker info >/dev/null 2>&1;then
      echo "R23B2_OBSERVER_STOP_DOCKER_INSPECTION_UNAVAILABLE"
      rc=5
    elif docker container inspect "$SIDECAR" >/dev/null 2>&1;then
      echo "R23B2_OBSERVER_STOP_SIDECAR_LEFTOVER"
      rc=5
    fi
    if [[ -e "$SOCKETDIR" || -L "$SOCKETDIR" ||
          -e "$RECEIPTDIR" || -L "$RECEIPTDIR" ]];then
      echo "R23B2_OBSERVER_STOP_SOCKET_OR_RECEIPT_LEFTOVER"
      rc=5
    fi
    local after
    after="$(docker inspect -f '{{.Id}}' "$CANARY" 2>/dev/null || true)"
    if [[ -z "$BEFORE" || "$BEFORE" != "$after" ]];then
      echo "R23B2_OBSERVER_STOP_CANARY_ID_DRIFT"
      rc=5
    elif [[ "$(docker inspect -f '{{.State.Running}}' "$CANARY" 2>/dev/null)" != true ]];then
      echo "R23B2_OBSERVER_STOP_CANARY_NOT_RUNNING"
      rc=5
    fi
    if python3 -B "$AUDIT" >/dev/null 2>&1;then
      echo "R23B2_OBSERVER_POST_PREFLIGHT_PASS"
    else
      echo "R23B2_OBSERVER_STOP_POST_PREFLIGHT_FAILED"
      rc=5
    fi
  fi
  if [[ "$rc" -eq 0 ]];then
    rm -rf -- "$LOGDIR"
    echo "R23B2_OBSERVER_NO_TOKEN_TRIAL_AND_CLEANUP_PASS"
  else
    # The only retained diagnostic is the private root-owned launcher log.
    # Do not paste the raw file into a public ticket or chat.
    echo "R23B2_OBSERVER_STOP_ROOT_ONLY_LOG_RETAINED"
    echo "R23B2_OBSERVER_LOG_LOCATION=$LOG"
  fi
  exit "$rc"
}
trap finish EXIT

echo "=== R23B2 PINNED SOURCE PREFLIGHT ==="
python3 -B "$LAUNCHER"
BEFORE="$(docker inspect -f '{{.Id}}' "$CANARY")"

echo "=== R23B2 START GUARDED TTL=120S (NO AIO CREDENTIALS) ==="
python3 -u -B "$LAUNCHER" \
  --apply --confirm R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY \
  --ttl-seconds 120 >"$LOG" 2>&1 &
JOB_PID="$!"

# The previous one-shot wrapper used only 30 seconds, which cannot reliably
# distinguish slow Docker startup from an exception. Give 100 seconds.
for _ in $(seq 1 200);do
  if grep -qx 'R23B2_PHASE_READY' "$LOG";then
    READY=1
    break
  fi
  if ! kill -0 "$JOB_PID" 2>/dev/null;then break;fi
  sleep .5
done
if [[ "$READY" -ne 1 ]];then
  echo "R23B2_OBSERVER_READY_NOT_SEEN_WAIT_FOR_CLEANUP"
  exit 5
fi
echo "R23B2_OBSERVER_READY_PASS"

pid="$(docker inspect -f '{{.State.Pid}}' "$CANARY")"
[[ "$pid" =~ ^[1-9][0-9]+$ ]] || exit 5
echo "=== R23B2 STRICT NO-TOKEN HTTP CONTRACT ==="
nsenter -t "$pid" -n python3 -B - <<'PY'
import http.client
checks = (
    ("GET", "/jellyfin/Users/Me", 401),
    ("GET", "/jellyfin/Shows/NextUp?Limit=20", 401),
    ("GET", "/jellyfin/UserItems/Resume?Limit=20", 404),
    ("POST", "/jellyfin/Users/AuthenticateByName", 405),
)
for method, route, expected in checks:
    c = http.client.HTTPConnection("127.0.0.1", 9999, timeout=5)
    try:
        c.request(method, route, headers={"Accept": "application/json"})
        response = c.getresponse()
        observed = response.status
        response.read(1024)
    finally:
        c.close()
    if observed != expected:
        raise SystemExit("R23B2_OBSERVER_HTTP_CONTRACT_MISMATCH")
    print(f"R23B2_OBSERVER_NO_TOKEN_{method}_{observed}_PASS")
PY
echo "R23B2_OBSERVER_NO_TOKEN_CONTRACT_PASS"
