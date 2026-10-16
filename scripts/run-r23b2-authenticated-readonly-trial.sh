#!/usr/bin/env bash
# R23B2 authenticated NextUp-only read-only B1V comparison. NOT production.
# Existing-token: all HTTP requests GET. Login: ONE AIO auth POST, then GETs.
set -euo pipefail
CONFIRM="R23B2_EPHEMERAL_AUTHENTICATED_READONLY_NEXTUP_ONLY"
if [[ $# -ne 4 || "$1" != "--mode" ||
   ( "$2" != "existing-token" && "$2" != "login" ) ||
   "$3" != "--confirm" || "$4" != "$CONFIRM" ]];then
  echo "R23B2_AUTH_STOP_EXPLICIT_MODE_AND_CONFIRM_REQUIRED" >&2;exit 3
fi
MODE="$2"
if [[ "$(id -u)" != 0 || ! -t 0 ]];then
  echo "R23B2_AUTH_STOP_SUDO_AND_INTERACTIVE_SSH_REQUIRED" >&2;exit 3
fi
for tool in docker python3 setpriv grep mktemp timeout getent cut;do
  command -v "$tool" >/dev/null || exit 3
done
if [[ ! "$(printenv SUDO_UID || true)" =~ ^[1-9][0-9]*$ ||
      ! "$(printenv SUDO_GID || true)" =~ ^[0-9]+$ ]];then
  echo "R23B2_AUTH_STOP_NONROOT_OPERATOR_REQUIRED" >&2;exit 3
fi
OP_UID="$SUDO_UID"
OP_GID="$SUDO_GID"
OP_HOME="$(getent passwd "$OP_UID" | cut -d: -f6)"
[[ "$OP_HOME" == /* && -d "$OP_HOME" ]] || {
  echo "R23B2_AUTH_STOP_OPERATOR_HOME_UNAVAILABLE" >&2;exit 3;
}
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LAUNCHER="$ROOT/scripts/host-trial-r23b2.py"
AUDIT="$ROOT/scripts/audit-r23b2-host-relay-prereqs.py"
B1V="$ROOT/scripts/audit-v140-r23d2b1v-real-client-readonly.py"
CANARY="homedocker-v140-r23b-canary"
SIDECAR="homedocker-v140-r23b2-unix-sidecar"
SOCKETDIR="/run/homedocker-r23b2-aio"
RECEIPTDIR="/var/lib/homedocker-r23b2-trial"
LOGDIR="$(mktemp -d /var/tmp/r23b2-auth-nextup.XXXXXXXX)"
chmod 0700 "$LOGDIR"
LOG="$LOGDIR/launcher.log"
JOB_PID=""
BEFORE=""
READY=0
COMPARE_PASS=0
STOP_REQUESTED=0
finish(){
  local rc=$? job_rc=0 after=""
  trap - EXIT
  if [[ -n "$JOB_PID" ]];then
    echo "=== R23B2 AUTH WAIT FOR EXACT CLEANUP ==="
    if wait "$JOB_PID";then job_rc=0;else job_rc=$?;fi
    if [[ "$job_rc" -ne 0 ]] &&
      ! ( [[ "$STOP_REQUESTED" -eq 1 && "$job_rc" -eq 130 ]] &&
           grep -qx R23B2_SIGNAL_CLEANUP_REQUESTED "$LOG" );then
      echo "R23B2_AUTH_STOP_UNEXPECTED_LAUNCHER_EXIT";rc=5
    fi
    echo "=== R23B2 AUTH SANITIZED PHASES ==="
    grep -E '^R23B2_(PHASE_|STOP_|SIGNAL_CLEANUP_REQUESTED|OWNED_RESOURCES_CLEANUP_PASS)' "$LOG" || true
    if [[ "$READY" -ne 1 || "$COMPARE_PASS" -ne 1 ]];then
      echo "R23B2_AUTH_STOP_GATEWAY_COMPARISON_NOT_PASS";rc=5
    fi
    if ! grep -qx R23B2_OWNED_RESOURCES_CLEANUP_PASS "$LOG";then
      echo "R23B2_AUTH_STOP_CLEANUP_MARKER_MISSING";rc=5
    fi
    if ! docker info >/dev/null 2>&1;then
      echo "R23B2_AUTH_STOP_DOCKER_INSPECTION_UNAVAILABLE";rc=5
    elif docker container inspect "$SIDECAR" >/dev/null 2>&1;then
      echo "R23B2_AUTH_STOP_SIDECAR_LEFTOVER";rc=5
    fi
    if [[ -e "$SOCKETDIR" || -L "$SOCKETDIR" ||
          -e "$RECEIPTDIR" || -L "$RECEIPTDIR" ]];then
      echo "R23B2_AUTH_STOP_SOCKET_OR_RECEIPT_LEFTOVER";rc=5
    fi
    after="$(docker inspect -f '{{.Id}}' "$CANARY" 2>/dev/null || true)"
    if [[ -z "$BEFORE" || "$BEFORE" != "$after" ]] ||
       [[ "$(docker inspect -f '{{.State.Running}}' "$CANARY" 2>/dev/null)" != true ]];then
      echo "R23B2_AUTH_STOP_ORIGINAL_CANARY_DRIFT";rc=5
    fi
    if python3 -B "$AUDIT" >/dev/null 2>&1;then
      echo "R23B2_AUTH_POST_PREFLIGHT_PASS"
    else
      echo "R23B2_AUTH_STOP_POST_PREFLIGHT_FAILED";rc=5
    fi
  fi
  if [[ "$rc" -eq 0 ]];then
    rm -rf -- "$LOGDIR"
    echo "R23B2_AUTHENTICATED_NEXTUP_COMPARE_AND_ROLLBACK_PASS"
  else
    echo "R23B2_AUTH_STOP_ROOT_ONLY_LAUNCHER_LOG_RETAINED"
    echo "R23B2_AUTH_LAUNCHER_LOG_LOCATION=$LOG"
    echo "R23B2_AUTH_NO_REPEAT_APPLY_UNTIL_REVIEW"
  fi
  exit "$rc"
}
trap finish EXIT

echo "=== R23B2 AUTH GUARDS: READONLY PREFLIGHT ==="
[[ -f "$B1V" ]] || { echo "R23B2_AUTH_STOP_B1V_MISSING" >&2;exit 3; }
python3 -B "$LAUNCHER"
BEFORE="$(docker inspect -f '{{.Id}}' "$CANARY")"

echo "=== R23B2 TEMPORARY READONLY RELAY TTL=360S ==="
python3 -u -B "$LAUNCHER" --apply \
  --confirm R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY \
  --ttl-seconds 360 >"$LOG" 2>&1 &
JOB_PID="$!"
for _ in $(seq 1 200);do
  if grep -qx R23B2_PHASE_READY "$LOG";then READY=1;break;fi
  if ! kill -0 "$JOB_PID" 2>/dev/null;then break;fi
  sleep .5
done
if [[ "$READY" -ne 1 ]];then
  echo "R23B2_AUTH_STOP_STARTUP_NOT_READY_WAIT_FOR_CLEANUP";exit 5
fi
echo "R23B2_AUTH_RELAY_READY"

echo "=== R23B2 B1V ACCOUNT-MATCHED NEXTUP COMPARISON ==="
echo "Enter the AIO credential only at the hidden interactive prompt."
args=(--compare)
if [[ "$MODE" == "existing-token" ]];then
  args+=(--existing-token --confirm LIVE_READONLY_GET_ONLY)
else
  args+=(--confirm LIVE_AUTH_LOGIN_GET_ONLY)
  echo "Login mode creates exactly one AIO auth session; no watch-state writes."
fi
# Never log B1V stdin or stdout to disk. TTY is inherited. 240s input+GET
# timeout is lower than the relay TTL. No token/password in argv or env.
if timeout --foreground --signal=TERM --kill-after=3s 240s \
  setpriv --reuid "$OP_UID" --regid "$OP_GID" --clear-groups \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$OP_HOME" \
  LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  python3 -B "$B1V" "${args[@]}";then
  COMPARE_PASS=1
  echo "R23B2_AUTH_B1V_COMPARISON_EXIT_ZERO"
else
  echo "R23B2_AUTH_STOP_B1V_INCONCLUSIVE_OR_FAILED"
fi

# After comparison, request graceful SIGTERM. The launcher catches TERM and
# runs exact cleanup in finally. Expected launcher exit=130, not SIGKILL.
if kill -0 "$JOB_PID" 2>/dev/null;then
  STOP_REQUESTED=1
  kill -TERM "$JOB_PID"
fi
if [[ "$COMPARE_PASS" -ne 1 ]];then exit 5;fi
echo "R23B2_AUTH_B1V_COMPARE_COMPLETED_WAIT_FOR_ROLLBACK"
