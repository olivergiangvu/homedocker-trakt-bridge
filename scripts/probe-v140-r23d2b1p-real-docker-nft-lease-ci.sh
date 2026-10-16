#!/usr/bin/env bash
# Real Docker bridge + nft bridge timed lease, dual userspace SIGKILL.
# ONLY on a dedicated disposable GITHUB-HOSTED runner. No HomeDocker apply.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
      "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" ||
      "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1P_STOP_NOT_PRIVILEGED_GITHUB_EPHEMERAL" >&2
  exit 3
fi
for binary in nft docker ip tc nsenter python3; do
  command -v "$binary" >/dev/null || { echo "R23D2B1P_DEPENDENCY_MISSING" >&2; exit 3; }
done
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1P_STOP_EXISTING_RUNNER_CONTAINERS" >&2; exit 4;
}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
FAKE="$ROOT/scripts/probe-v140-r23d2b1p-fake-tcp-session-ci.py"
GUARD="$ROOT/scripts/probe-v140-r23d2b1o-admission-guardian-ci.py"
test -f "$FAKE" && test -f "$GUARD"
NETWORK="r23d2b1o-private-$$"
CLIENT="r23d2b1o-peer-$$"
SERVER="r23d2b1o-gw-$$"
BRIDGE="br-r23d2b1o"
TABLE="r23d2b1p_lease"
CLIENT_IP="172.31.252.2"
SERVER_IP="172.31.252.3"
TMP="$(mktemp -d)"
chmod 700 "$TMP"
OWNED_DOCKER=0
OWNED_NFT=0
GUARDIAN_PID=""
WATCHDOG_PID=""
SESSION_PID=""
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  for pid in "$GUARDIAN_PID" "$WATCHDOG_PID" "$SESSION_PID"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  if ((OWNED_DOCKER)); then
    docker rm -f "$CLIENT" "$SERVER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    if docker container inspect "$CLIENT" >/dev/null 2>&1 ||
       docker container inspect "$SERVER" >/dev/null 2>&1 ||
       docker network inspect "$NETWORK" >/dev/null 2>&1; then rc=6; fi
  fi
  if ((OWNED_NFT)); then
    nft delete table bridge "$TABLE" >/dev/null 2>&1 || true
    if nft list table bridge "$TABLE" >/dev/null 2>&1; then rc=6; fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0)); then
    echo "R23D2B1P_OWNED_DOCKER_AND_NFT_CLEANUP_PASS"
  else
    echo "R23D2B1P_EPHEMERAL_LAB_STOP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if nft list table bridge "$TABLE" >/dev/null 2>&1 ||
   ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "R23D2B1P_STOP_RESOURCE_COLLISION" >&2;exit 4
fi

docker pull python:3.12-alpine >/dev/null
OWNED_DOCKER=1
docker network create --driver bridge --internal \
  --subnet 172.31.252.0/28 --gateway 172.31.252.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=false" \
  "$NETWORK" >/dev/null
[[ "$(docker network inspect -f '{{.Internal}}' "$NETWORK")" == "true" ]]
base=(--read-only --cap-drop ALL --security-opt no-new-privileges
      --label r23d2b1o.lab=ephemeral --restart no
      --pids-limit 64 --memory 96m --network "$NETWORK"
      --mount "type=bind,src=$FAKE,dst=/lab/probe.py,readonly")
docker create --name "$SERVER" "${base[@]}" --ip "$SERVER_IP" \
  python:3.12-alpine python3 -B /lab/probe.py --serve >/dev/null
docker create --name "$CLIENT" "${base[@]}" --ip "$CLIENT_IP" \
  python:3.12-alpine sleep 600 >/dev/null
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "false" ]]
docker start "$CLIENT" >/dev/null
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "false" ]]
# Authenticate exact newly allocated peer veth/bridge before ANY nft/tc
# change, while the fake gateway is STILL STOPPED.
PID="$(docker inspect -f '{{.State.Pid}}' "$CLIENT")"
[[ "$PID" =~ ^[1-9][0-9]*$ ]]
IFLINK="$(nsenter -t "$PID" -n ip -o link show dev eth0 |
  python3 -B -c 'import re,sys
m=re.search(r"eth0@if([1-9][0-9]*):",sys.stdin.read())
if not m:sys.exit(2)
print(m.group(1))')"
PEER_DEV="$(ip -j link show | python3 -B -c '
import json,sys
idx=int(sys.argv[1]);bridge=sys.argv[2]
matches=[i for i in json.load(sys.stdin) if i.get("ifindex")==idx]
if len(matches)!=1:sys.exit(2)
r=matches[0];name=r["ifname"].split("@",1)[0]
if not name.startswith("veth") or r.get("master")!=bridge:sys.exit(3)
print(name)
' "$IFLINK" "$BRIDGE")"
[[ "$PEER_DEV" == veth* ]] || exit 5

# The independent TC filter is deliberately identical to 2B1N/O. It
# permits legitimate fake 17420 indefinitely if the nft table disappears;
# B1P kernel lease supplies the missing time-bounded second fence.
tc qdisc add dev "$PEER_DEV" clsact
tc filter add dev "$PEER_DEV" ingress protocol arp pref 5 \
    matchall action goto chain 1
tc filter add dev "$PEER_DEV" ingress protocol ip pref 10 u32 \
    match ip src "$CLIENT_IP"/32 match ip dst "$SERVER_IP"/32 \
    match ip protocol 6 0xff match ip dport 17420 0xffff \
    action goto chain 1
tc filter add dev "$PEER_DEV" ingress protocol all pref 20 matchall action drop
tc filter add dev "$PEER_DEV" ingress chain 1 protocol all pref 5 matchall action pass

# Bridge-family nftables hooks on actual Docker bridge forward path.
# No policy drop for other runner bridges; filter ONLY our owned veth.
# Initial set is EMPTY => no fake TCP to gateway before live lease issue.
create_table() {
  nft add table bridge "$TABLE"
  nft "add set bridge $TABLE authorized { type ipv4_addr; flags timeout; timeout 3s; }"
  nft "add chain bridge $TABLE permit { type filter hook forward priority -250; policy accept; }"
  nft add rule bridge "$TABLE" permit iifname "$PEER_DEV" ether type arp accept
  nft add rule bridge "$TABLE" permit oifname "$PEER_DEV" ether type arp accept
  nft add rule bridge "$TABLE" permit iifname "$PEER_DEV" ether type ip \
    ip saddr "$CLIENT_IP" ip daddr "$SERVER_IP" tcp dport 17420 \
    ip saddr @authorized accept
  nft add rule bridge "$TABLE" permit oifname "$PEER_DEV" ether type ip \
    ip saddr "$SERVER_IP" ip daddr "$CLIENT_IP" tcp sport 17420 \
    ip daddr @authorized accept
  nft add rule bridge "$TABLE" permit iifname "$PEER_DEV" drop
  nft add rule bridge "$TABLE" permit oifname "$PEER_DEV" drop
}
OWNED_NFT=1
create_table
nft list set bridge "$TABLE" authorized > "$TMP/initial-set"
if grep -q "172.31.252.2" "$TMP/initial-set"; then
  echo "R23D2B1P_STOP_NONEMPTY_INITIAL_GRANT" >&2; exit 5
fi
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "false" ]]
echo "R23D2B1P_REAL_DOCKER_BRIDGE_DEFAULT_DENY_BEFORE_ADMISSION_PASS"

common=(--server "$SERVER" --client "$CLIENT" --network "$NETWORK"
        --bridge "$BRIDGE" --server-ip "$SERVER_IP" --client-ip "$CLIENT_IP"
        --guard-heartbeat "$TMP/guardian.hb" --watchdog-heartbeat "$TMP/watchdog.hb"
        --guard-pidfile "$TMP/guardian.pid")
probe() {
  docker exec "$CLIENT" python3 -B /lab/probe.py --probe \
    --addr "$SERVER_IP" --port "$1" >/dev/null 2>&1
}
ready() {
  for _ in $(seq 1 48);do
    if probe "$1";then return 0;fi
    sleep 0.15
  done
  return 1
}
denied() {
  if probe "$1";then
    echo "R23D2B1P_FORBIDDEN_TCP_REACHABLE_STOP" >&2; exit 5
  fi
}
# Start the real B1O watchdog + guardian, with the guardian alone permitted
# to refresh its exact 3s kernel lease after verifying both processes live.
R23D2B1O_EPHEMERAL_ONLY=true python3 -B "$GUARD" --role watchdog \
  "${common[@]}" >"$TMP/wd.log" 2>&1 &
WATCHDOG_PID="$!"
for _ in $(seq 1 40);do
  [[ -s "$TMP/watchdog.hb" ]] && break
  sleep 0.1
done
[[ -s "$TMP/watchdog.hb" ]] || { echo "R23D2B1P_WATCHDOG_NO_HEARTBEAT" >&2;exit 5; }
R23D2B1O_EPHEMERAL_ONLY=true python3 -B "$GUARD" --role guardian \
  --watchdog-pid "$WATCHDOG_PID" --kernel-lease \
  "${common[@]}" >"$TMP/guard.log" 2>&1 &
GUARDIAN_PID="$!"
echo "$GUARDIAN_PID" > "$TMP/guardian.pid"
if ! ready 17420;then
  echo "R23D2B1P_ADMISSION_NOT_READY_STOP" >&2
  cat "$TMP/guard.log" "$TMP/wd.log" >&2
  exit 5
fi
denied 17421
echo "R23D2B1P_CONTINUOUS_MUTUAL_LIVENESS_KERNEL_LEASE_ADMISSION_PASS"

# Prove renewal really works LONGER than the nominal 3s timeout.
sleep 4
ready 17420 || {
  echo "R23D2B1P_LEASE_NOT_REFRESHED_STOP" >&2
  # Exact synthetic lab diagnostics ONLY; do not print real host rules or
  # credentials. Keep failure strict; never lengthen or force the lease.
  tail -n 18 "$TMP/guard.log" "$TMP/wd.log" >&2 || true
  nft list set bridge "$TABLE" authorized >&2 || true
  docker inspect --format 'server_state={{.State.Status}} client_state=lab' "$SERVER" >&2 || true
  exit 5
}
denied 17421
echo "R23D2B1P_ACTIVE_3S_KERNEL_LEASE_REFRESH_PASS"

# A long-lived ESTABLISHED TCP session is started before simultaneous kill.
docker exec "$CLIENT" python3 -B /lab/probe.py --session \
    --addr "$SERVER_IP" --port 17420 >"$TMP/session.log" 2>&1 &
SESSION_PID="$!"
for _ in $(seq 1 35);do
  if grep -q "R23D2B1P_EXISTING_TCP_SESSION_ESTABLISHED" "$TMP/session.log";then break;fi
  sleep 0.1
done
grep -q "R23D2B1P_EXISTING_TCP_SESSION_ESTABLISHED" "$TMP/session.log" || {
  echo "R23D2B1P_EXISTING_TCP_SESSION_NOT_ESTABLISHED_STOP" >&2;exit 5;
}
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "true" ]]
# Kill BOTH guardian and watchdog and verify only KERNEL timer closes access,
# with no process doing a post-kill nft revoke or Docker stop.
kill -KILL "$GUARDIAN_PID" "$WATCHDOG_PID"
wait "$GUARDIAN_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true
GUARDIAN_PID=""
WATCHDOG_PID=""
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "true" ]]
start_ms="$(date +%s%3N)"
DENY_OBSERVED=0
for _ in $(seq 1 28);do
  if ! probe 17420;then DENY_OBSERVED=1;break;fi
  sleep 0.20
done
elapsed_ms="$(( $(date +%s%3N) - start_ms ))"
if [[ "$DENY_OBSERVED" != "1" || "$elapsed_ms" -gt 5500 ]];then
  echo "R23D2B1P_LEASE_DID_NOT_EXPIRE_IN_BOUND_STOP" >&2;exit 5
fi
denied 17420
denied 17421
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "true" ]]
echo "R23D2B1P_DUAL_SIGKILL_KERNEL_LEASE_FAIL_CLOSED_WITHOUT_USERS_PASSES"

# An ESTABLISHED connection must fail after the same lease expires:
wait "$SESSION_PID" || {
  echo "R23D2B1P_EXISTING_TCP_SESSION_SCRIPT_FAILED_STOP" >&2;exit 5;
}
SESSION_PID=""
grep -q "R23D2B1P_ESTABLISHED_TCP_NO_LONGER_REACHABLE" "$TMP/session.log" || {
  cat "$TMP/session.log" >&2;exit 5;
}
echo "R23D2B1P_DUAL_SIGKILL_EXISTING_TCP_SESSION_CUTOFF_PASS"

# Negative control: deliberately remove just the owned nft bridge table,
# leaving tc permissive for the allowed fake port. It MUST reopen TCP.
# This proves nft was the actual kernel barrier (not a dead server).
nft delete table bridge "$TABLE"
OWNED_NFT=0
ready 17420 || {
  echo "R23D2B1P_NFT_REMOVAL_CONTROL_DID_NOT_REOPEN_STOP" >&2;exit 5;
}
denied 17421
echo "R23D2B1P_DELETING_LAST_KERNEL_LEASE_FENCE_REOPENS_TCP_CONTROL"

# Recover the exact empty nft policy. Without two live processes, it must
# deny, even though the fake Docker gateway remains running.
OWNED_NFT=1
create_table
denied 17420
denied 17421
echo "R23D2B1P_EMPTY_KERNEL_LEASE_RECREATED_DENY_PASS"
echo "R23D2B1P_REAL_DOCKER_KERNEL_EXPIRING_ADMISSION_LAB_PASS_STABLE_HOLD"
