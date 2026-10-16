#!/usr/bin/env bash
# R23D-2B1O real Docker, two independent OS processes, ephemeral GitHub VM ONLY.
# Never execute this on HomeDocker or self-hosted CI: restarts Docker daemon.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
      "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" ||
      "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1O_STOP_NOT_PRIVILEGED_EPHEMERAL_GITHUB_RUNNER" >&2
  exit 3
fi
for binary in docker ip tc nsenter systemctl timeout python3; do
  command -v "$binary" >/dev/null || exit 3
done
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1O_STOP_RUNNER_CONTAINS_OTHER_CONTAINERS" >&2; exit 4;
}
[[ "$(docker info --format '{{.LiveRestoreEnabled}}')" == "false" ]] || exit 4
systemctl is-active --quiet docker || exit 4

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GUARD="$ROOT/scripts/probe-v140-r23d2b1o-admission-guardian-ci.py"
FAKE="$ROOT/scripts/probe-v140-r23d2b1n-docker-fake-ci.py"
test -f "$GUARD" && test -f "$FAKE"
NETWORK="r23d2b1o-private-$$"
SERVER="r23d2b1o-gw-$$"
CLIENT="r23d2b1o-peer-$$"
BRIDGE="br-r23d2b1o"
SERVER_IP="172.31.252.3"
CLIENT_IP="172.31.252.2"
TMP="$(mktemp -d)"
chmod 700 "$TMP"
MUTATED=0
WATCHDOG_PID=""
GUARDIAN_PID=""
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  for pid in "$GUARDIAN_PID" "$WATCHDOG_PID"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  if (( MUTATED )); then
    if ! docker info >/dev/null 2>&1; then
      timeout 60 systemctl start docker >/dev/null 2>&1 || true
    fi
    docker rm -f "$CLIENT" >/dev/null 2>&1 || true
    docker rm -f "$SERVER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    if docker container inspect "$CLIENT" >/dev/null 2>&1 ||
       docker container inspect "$SERVER" >/dev/null 2>&1 ||
       docker network inspect "$NETWORK" >/dev/null 2>&1; then
      rc=6
    fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0)); then
    echo "R23D2B1O_OWNED_DOCKER_RESOURCE_CLEANUP_PASS"
  else
    echo "R23D2B1O_PRIVATE_CI_LAB_FAILED" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "R23D2B1O_STOP_RESOURCE_COLLISION" >&2; exit 4
fi
docker pull python:3.12-alpine >/dev/null
MUTATED=1
docker network create --driver bridge --internal \
  --subnet 172.31.252.0/28 --gateway 172.31.252.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=false" \
  "$NETWORK" >/dev/null
[[ "$(docker network inspect -f '{{.Internal}}' "$NETWORK")" == "true" ]]
[[ "$(docker network inspect -f '{{.Driver}}' "$NETWORK")" == "bridge" ]]
base=(--read-only --cap-drop ALL --security-opt no-new-privileges
  --label r23d2b1o.lab=ephemeral --restart no --pids-limit 64 --memory 96m
  --network "$NETWORK" --mount "type=bind,src=$FAKE,dst=/lab/probe.py,readonly")
docker create --name "$SERVER" "${base[@]}" --ip "$SERVER_IP" \
  python:3.12-alpine python3 -B /lab/probe.py --serve >/dev/null
docker create --name "$CLIENT" "${base[@]}" --ip "$CLIENT_IP" \
  python:3.12-alpine sleep 1200 >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "false"
test "$(docker inspect -f '{{.State.Running}}' "$CLIENT")" = "false"
echo "R23D2B1O_STOPPED_REAL_DOCKER_PEERS_BEFORE_ADMISSION_PASS"

common=(--server "$SERVER" --client "$CLIENT" --network "$NETWORK" --bridge "$BRIDGE"
        --server-ip "$SERVER_IP" --client-ip "$CLIENT_IP"
        --guard-heartbeat "$TMP/guardian.hb" --watchdog-heartbeat "$TMP/watchdog.hb"
        --guard-pidfile "$TMP/guardian.pid")
ready() {
  local port="$1"
  for _ in $(seq 1 65); do
    if docker exec "$CLIENT" python3 -B /lab/probe.py \
        --probe --addr "$SERVER_IP" --port "$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}
deny() {
  if docker exec "$CLIENT" python3 -B /lab/probe.py \
      --probe --addr "$SERVER_IP" --port "$1" >/dev/null 2>&1; then
    echo "R23D2B1O_UNGUARDED_FAKE_TCP_DETECTED_STOP" >&2
    exit 5
  fi
}
server_stopped() {
  for _ in $(seq 1 50); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$SERVER" 2>/dev/null || true)" == "false" ]]; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}
start_pair() {
  rm -f "$TMP/guardian.pid" "$TMP/guardian.hb" "$TMP/watchdog.hb"
  R23D2B1O_EPHEMERAL_ONLY=true python3 -B "$GUARD" \
    --role watchdog "${common[@]}" > "$TMP/watchdog.log" 2>&1 &
  WATCHDOG_PID="$!"
  for _ in $(seq 1 30); do
    [[ -s "$TMP/watchdog.hb" ]] && break
    sleep 0.1
  done
  [[ -s "$TMP/watchdog.hb" ]] || { echo "WATCHDOG_NOT_READY" >&2; exit 5; }
  R23D2B1O_EPHEMERAL_ONLY=true python3 -B "$GUARD" \
    --role guardian --watchdog-pid "$WATCHDOG_PID" "${common[@]}" \
    > "$TMP/guardian.log" 2>&1 &
  GUARDIAN_PID="$!"
  echo "$GUARDIAN_PID" > "$TMP/guardian.pid"
  ready 17420 || {
    echo "R23D2B1O_ADMISSION_NOT_READY_STOP" >&2
    cat "$TMP/guardian.log" "$TMP/watchdog.log" >&2
    exit 5
  }
  deny 17421
}
# First independent crash: watchdog must stop the serving fake gateway.
start_pair
echo "R23D2B1O_GUARDIAN_INDEPENDENT_WATCHDOG_ADMITTED_FAKE_PEER_PASS"
kill -KILL "$GUARDIAN_PID"
wait "$GUARDIAN_PID" 2>/dev/null || true
GUARDIAN_PID=""
server_stopped || {
  echo "R23D2B1O_WATCHDOG_DID_NOT_STOP_GATEWAY" >&2; exit 5
}
grep -q "R23D2B1O_INDEPENDENT_WATCHDOG_STOPPED_FAKE_GATEWAY" "$TMP/watchdog.log"
echo "R23D2B1O_SIGKILL_GUARDIAN_INDEPENDENT_WATCHDOG_KILL_PASS"
wait "$WATCHDOG_PID" 2>/dev/null || true
WATCHDOG_PID=""

# Second independent crash: guardian must stop the gateway when the
# watchdog process is SIGKILLed (mutual liveness checks).
start_pair
kill -KILL "$WATCHDOG_PID"
wait "$WATCHDOG_PID" 2>/dev/null || true
WATCHDOG_PID=""
server_stopped || { echo "R23D2B1O_GUARDIAN_DID_NOT_STOP_GATEWAY" >&2; exit 5; }
grep -q "R23D2B1O_GUARDIAN_DETECTED_WATCHDOG_LOSS_STOPPED_FAKE_GATEWAY" "$TMP/guardian.log"
echo "R23D2B1O_SIGKILL_WATCHDOG_GUARDIAN_KILL_PASS"
wait "$GUARDIAN_PID" 2>/dev/null || true
GUARDIAN_PID=""

# Reestablish two-party process presence, then restart the ephemeral
# runner Docker daemon. Docker --restart=no means both fake containers
# stay stopped. Recovery is manually bootstrapped here, not a boot gate.
start_pair
timeout 100 systemctl restart docker
for _ in $(seq 1 75); do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 0.3
done
docker info >/dev/null
for peer in "$CLIENT" "$SERVER"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$peer")" == "true" ]]; then
    echo "R23D2B1O_DAEMON_UNEXPECTED_AUTO_START_STOP" >&2;exit 5
  fi
done
echo "R23D2B1O_REAL_DOCKER_DAEMON_RESTART_ADMISSION_CLOSED_PASS"
# Any surviving guardian may recover on its own only after rechecking
# live Docker ownership and re-arming the new host veth.
if ! ready 17420; then
  # Both processes may have exited during daemon outage, so restart them.
  for pid in "$GUARDIAN_PID" "$WATCHDOG_PID"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  GUARDIAN_PID=""
  WATCHDOG_PID=""
  start_pair
fi
ready 17420
deny 17421
echo "R23D2B1O_POST_DAEMON_RECOVERY_REARMED_BEFORE_ADMISSION_PASS"

# Critical NEGATIVE CONTROL: simultaneous loss of both userspace
# processes cannot guarantee real-time fail closed while tc still permits
# the legitimate fake relay. This remains a production release blocker.
kill -KILL "$GUARDIAN_PID" "$WATCHDOG_PID" 2>/dev/null || true
wait "$GUARDIAN_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true
GUARDIAN_PID=""
WATCHDOG_PID=""
ready 17420 || {
  echo "R23D2B1O_DUAL_LOSS_NOT_REPRODUCIBLE_STOP" >&2;exit 5;
}
deny 17421
echo "R23D2B1O_DUAL_GUARDIAN_WATCHDOG_LOSS_EXPOSURE_RELEASE_BLOCKER_PROVEN"
docker stop --time 1 "$SERVER" >/dev/null
server_stopped
echo "R23D2B1O_EPHEMERAL_MUTUAL_FAILURE_LAB_PASS_STABLE_RELEASE_HOLD"
