#!/usr/bin/env bash
# R23D-2B1N: real Docker bridge/veth + daemon restart, GITHUB-HOSTED CI ONLY.
# WARNING: systemctl restart docker is intentional ONLY on an expendable runner!
# NEVER execute this script on HomeDocker or a self-hosted GitHub runner.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
      "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_OS:-}" != "Linux" ||
      "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "${CI:-}" != "true" ||
      "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1N_STOP_NOT_PRIVILEGED_GITHUB_HOSTED_EPHEMERAL" >&2
  exit 3
fi
for bin in docker ip tc nsenter python3 systemctl timeout; do
  command -v "$bin" >/dev/null || {
    echo "R23D2B1N_STOP_MISSING_CI_DEPENDENCY" >&2; exit 3;
  }
done
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
FAKE="$ROOT/scripts/probe-v140-r23d2b1n-docker-fake-ci.py"
test -f "$FAKE"
# Docker daemon restart is allowed ONLY if this whole runner is unused:
# never interrupt a concurrently running service even on a disposable host.
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1N_STOP_RUNNER_ALREADY_HAS_CONTAINERS" >&2; exit 4;
}
[[ "$(docker info --format '{{.LiveRestoreEnabled}}')" == "false" ]] || {
  echo "R23D2B1N_STOP_LIVE_RESTORE_ENABLED" >&2; exit 4;
}
systemctl is-active --quiet docker || {
  echo "R23D2B1N_STOP_DOCKER_SERVICE_NOT_SYSTEMD" >&2; exit 4;
}

NETWORK="r23d2b1n-private-$$"
SERVER="r23d2b1n-gw-$$"
CLIENT="r23d2b1n-peer-$$"
BRIDGE="br-r23d2b1n"
IMAGE="python:3.12-alpine"
PEER_IP="172.31.252.2"
SERVER_IP="172.31.252.3"
ORIGINAL_PEER_IF=""
NEW_PEER_IF=""
ORIGINAL_PEER_INDEX=""
MUTATED=0
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if (( MUTATED == 1 )); then
    if ! docker info >/dev/null 2>&1; then
      timeout 50 systemctl start docker >/dev/null 2>&1 || true
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
  if ((rc == 0)); then
    echo "R23D2B1N_OWNED_DOCKER_RESOURCES_CLEANUP_PASS"
  else
    echo "R23D2B1N_PRIVATE_CI_DOCKER_LAB_STOP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Fail closed if the reserved bridge interface already belongs to a different
# service. The private Docker network is INTERNAL and publishes zero host ports.
if ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   docker network inspect "$NETWORK" >/dev/null 2>&1 ||
   docker container inspect "$SERVER" "$CLIENT" >/dev/null 2>&1; then
  echo "R23D2B1N_STOP_RESOURCE_NAME_COLLISION" >&2; exit 4
fi
docker pull "$IMAGE" >/dev/null
MUTATED=1
docker network create --driver bridge --internal \
  --subnet 172.31.252.0/28 --gateway 172.31.252.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=false" \
  "$NETWORK" >/dev/null
test "$(docker network inspect -f '{{.Internal}}' "$NETWORK")" = "true"
test "$(docker network inspect -f '{{.Driver}}' "$NETWORK")" = "bridge"
test -e "/sys/class/net/$BRIDGE"
echo "R23D2B1N_INTERNAL_DOCKER_BRIDGE_OWNERSHIP_PASS"

# Both peers use synthetic python code mounted read-only; no tokens, DBs,
# host ports, privileged container caps or media networks.
base=(--read-only --cap-drop ALL --security-opt no-new-privileges
      --restart no --pids-limit 64 --memory 96m
      --network "$NETWORK"
      --mount "type=bind,src=$FAKE,dst=/lab/probe.py,readonly")
docker run -d --name "$SERVER" "${base[@]}" --ip "$SERVER_IP" \
  "$IMAGE" python3 -B /lab/probe.py --serve >/dev/null
docker run -d --name "$CLIENT" "${base[@]}" --ip "$PEER_IP" \
  "$IMAGE" sleep 600 >/dev/null

assert_owner() {
  local name="$1" expected="$2"
  docker inspect --format '{{json .}}' "$name" \
  | python3 -B -c '
import json,sys
c=json.load(sys.stdin)
name,network,expected=sys.argv[1:]
assert c["Name"]=="/"+name
assert c["State"]["Running"]
assert c["HostConfig"]["RestartPolicy"]["Name"]=="no"
assert c["HostConfig"]["Privileged"] is False
assert c["HostConfig"]["ReadonlyRootfs"] is True
assert c["HostConfig"]["PortBindings"] in ({},None)
assert set(c["NetworkSettings"]["Networks"])=={network}
assert c["NetworkSettings"]["Networks"][network]["IPAddress"]==expected
assert c["HostConfig"]["NetworkMode"]==network
' "$name" "$NETWORK" "$expected"
}
assert_owner "$SERVER" "$SERVER_IP"
assert_owner "$CLIENT" "$PEER_IP"
probe() {
  docker exec "$CLIENT" python3 -B /lab/probe.py \
    --probe --addr "$SERVER_IP" --port "$1" >/dev/null 2>&1
}
ready() {
  for _ in $(seq 1 35); do
    if probe "$1"; then return 0; fi
    sleep 0.15
  done
  return 1
}
deny() {
  if probe "$1"; then
    echo "R23D2B1N_FORBIDDEN_REAL_DOCKER_TCP_REACHABLE_STOP" >&2
    exit 5
  fi
}
ready 17420 && ready 17421 || {
  echo "R23D2B1N_PRE_FENCE_REAL_DOCKER_TCP_NOT_REACHABLE_STOP" >&2; exit 5
}
echo "R23D2B1N_UNFENCED_REAL_DOCKER_TCP_POSITIVE_CONTROL_PASS"

# Discover the EXACT peer veth using its container pid and kernel iflink.
# Before touching tc verify it is a veth *enslaved to our private bridge*.
peer_veth() {
  local pid iflink
  pid="$(docker inspect --format '{{.State.Pid}}' "$CLIENT")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  # nsenter -n does not remount /sys on some GitHub runner images. Resolve
  # kernel peer ifindex from the namespaced "eth0@ifNN" iproute2 output.
  iflink="$(nsenter -t "$pid" -n ip -o link show dev eth0 |
    python3 -B -c 'import re,sys
m=re.search(r"eth0@if([1-9][0-9]*):",sys.stdin.read())
if not m: sys.exit(2)
print(m.group(1))')" || return 1
  [[ "$iflink" =~ ^[1-9][0-9]*$ ]] || return 1
  ip -j link show | python3 -B -c '
import json,sys
index=int(sys.argv[1]); master=sys.argv[2]
matches=[i for i in json.load(sys.stdin) if i.get("ifindex")==index]
if len(matches)!=1: sys.exit(2)
row=matches[0]
name=row["ifname"].split("@",1)[0]
if not name.startswith("veth") or row.get("master")!=master: sys.exit(3)
print(name)
' "$iflink" "$BRIDGE"
}
tc_present() {
  tc qdisc show dev "$1" | grep -q 'clsact'
}
arm_tc() {
  local dev="$1"
  tc_present "$dev" && { echo "R23D2B1N_UNEXPECTED_EXISTING_TC_STOP" >&2; exit 5; }
  tc qdisc add dev "$dev" clsact
  tc filter add dev "$dev" ingress protocol arp pref 5 \
    matchall action goto chain 1
  tc filter add dev "$dev" ingress protocol ip pref 10 u32 \
    match ip src "$PEER_IP"/32 match ip dst "$SERVER_IP"/32 \
    match ip protocol 6 0xff match ip dport 17420 0xffff \
    action goto chain 1
  tc filter add dev "$dev" ingress protocol all pref 20 matchall action drop
  tc filter add dev "$dev" ingress chain 1 protocol all pref 5 matchall action pass
  tc filter show dev "$dev" ingress | grep -q 'pref 20'
}
ORIGINAL_PEER_IF="$(peer_veth)" || {
  echo "R23D2B1N_CANNOT_AUTHENTICATE_PRIVATE_VETH_STOP" >&2;exit 5
}
ORIGINAL_PEER_INDEX="$(cat "/sys/class/net/$ORIGINAL_PEER_IF/ifindex")"
arm_tc "$ORIGINAL_PEER_IF"
ready 17420 || { echo "R23D2B1N_ALLOWED_TCP_MISSING_STOP" >&2; exit 5; }
deny 17421
echo "R23D2B1N_REAL_DOCKER_VETH_TC_INGRESS_CONTROLS_PASS"

# Restart the ACTUAL ephemeral runner's Docker daemon. No other containers
# were present at entry. Both fake peers had restart=no, no live-restore.
timeout 100 systemctl restart docker
for _ in $(seq 1 80); do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 0.3
done
docker info >/dev/null
for peer in "$CLIENT" "$SERVER"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$peer")" != "false" ]]; then
    echo "R23D2B1N_DOCKER_DAEMON_RESTARTED_UNSAFE_PEER_STOP" >&2; exit 5
  fi
done
# Check both remain stopped after daemon recovery; important because restart=no
# must be an actual admission barrier, not just an observed instant.
sleep 2
for peer in "$CLIENT" "$SERVER"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$peer")" != "false" ]]; then
    echo "R23D2B1N_POST_DAEMON_AUTO_RESTART_STOP" >&2; exit 5
  fi
done
test "$(docker network inspect -f '{{.Internal}}' "$NETWORK")" = "true"
echo "R23D2B1N_ACTUAL_DOCKER_DAEMON_RESTART_PEERS_HELD_STOPPED_PASS"

# Controlled recovery protocol: start CLIENT first while fake SERVER stays
# DOWN. Docker recreates a veth which has no independent tc protection.
# Do not start the listener until fresh veth has been authenticated and armed.
docker start "$CLIENT" >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "false"
assert_owner "$CLIENT" "$PEER_IP"
NEW_PEER_IF="$(peer_veth)" || {
  echo "R23D2B1N_RECOVERED_VETH_OWNERSHIP_STOP" >&2;exit 5
}
NEW_PEER_INDEX="$(cat "/sys/class/net/$NEW_PEER_IF/ifindex")"
if [[ "$NEW_PEER_IF" == "$ORIGINAL_PEER_IF" && "$NEW_PEER_INDEX" == "$ORIGINAL_PEER_INDEX" ]]; then
  echo "R23D2B1N_VETH_NOT_RECREATED_STOP" >&2;exit 5
fi
if tc_present "$NEW_PEER_IF"; then
  echo "R23D2B1N_TC_UNEXPECTEDLY_PERSISTED_ON_RECREATED_VETH_STOP" >&2;exit 5
fi
echo "R23D2B1N_DOCKER_RECREATED_VETH_LOST_OLD_TC_PROVEN"

arm_tc "$NEW_PEER_IF"
docker start "$SERVER" >/dev/null
assert_owner "$SERVER" "$SERVER_IP"
ready 17420 || { echo "R23D2B1N_ALLOWED_TCP_MISSING_STOP" >&2; exit 5; }
deny 17421
echo "R23D2B1N_TC_REARMED_BEFORE_FAKE_GATEWAY_START_PASS"

# Proof of the bug if an unarmed veth were launched after Docker restart:
# on the private bridge ONLY, explicitly remove tc and verify forbidden TCP
# becomes reachable. Re-arm the fence and confirm denial returns.
tc qdisc del dev "$NEW_PEER_IF" clsact
ready 17421 || {
  echo "R23D2B1N_TC_REMOVAL_NEGATIVE_CONTROL_FAILED_STOP" >&2;exit 5
}
echo "R23D2B1N_REMOVING_TC_EXPOSES_INTERNAL_DOCKER_PEER_NEGATIVE_CONTROL"
arm_tc "$NEW_PEER_IF"
ready 17420 || { echo "R23D2B1N_ALLOWED_TCP_MISSING_STOP" >&2; exit 5; }
deny 17421
echo "R23D2B1N_REARMED_FILTER_FAIL_CLOSED_PASS"
echo "R23D2B1N_EPHEMERAL_REAL_DOCKER_RECOVERY_LAB_PASS_NO_HOMEDOCKER_APPLY"
