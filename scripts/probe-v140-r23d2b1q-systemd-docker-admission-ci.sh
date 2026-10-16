#!/usr/bin/env bash
# R23D-2B1Q: real systemd transient interlock + real Docker outage.
# DESTRUCTIVE ONLY on a clean, dedicated, disposable GitHub-hosted runner.
# Not a real reboot. NEVER run on HomeDocker or any self-hosted runner.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
      "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" ||
      "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1Q_STOP_NOT_DISPOSABLE_GITHUB_RUNNER" >&2
  exit 3
fi
for tool in systemd-run systemctl docker nft ip nsenter tc python3 timeout; do
  command -v "$tool" >/dev/null || { echo "R23D2B1Q_STOP_MISSING_CI_TOOL" >&2; exit 3; }
done
systemctl is-active --quiet docker || exit 4
[[ "$(docker info --format '{{.LiveRestoreEnabled}}')" == "false" ]] || exit 4
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1Q_STOP_OTHER_RUNNER_CONTAINERS" >&2;exit 4;
}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ADMIT="$ROOT/scripts/probe-v140-r23d2b1q-systemd-admission-ci.py"
FAKE="$ROOT/scripts/probe-v140-r23d2b1n-docker-fake-ci.py"
test -f "$ADMIT" && test -f "$FAKE"
SUFFIX="$$"
NET="r23d2b1q-private-$SUFFIX"
CLIENT="r23d2b1q-client-$SUFFIX"
SERVER="r23d2b1q-server-$SUFFIX"
BRIDGE="br-r23d2b1q"
TABLE="r23d2b1q_lease"
CLIENT_IP="172.31.252.2"
SERVER_IP="172.31.252.3"
OWN_DOCKER=0
OWN_NFT=0
TMP="$(mktemp -d)"
chmod 700 "$TMP"
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if (( OWN_DOCKER )); then
    if ! docker info >/dev/null 2>&1; then
      timeout 60 systemctl start docker >/dev/null 2>&1 || true
    fi
    docker rm -f "$CLIENT" "$SERVER" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    if docker container inspect "$CLIENT" >/dev/null 2>&1 ||
       docker container inspect "$SERVER" >/dev/null 2>&1 ||
       docker network inspect "$NET" >/dev/null 2>&1; then
      rc=6
    fi
  fi
  if (( OWN_NFT )); then
    nft delete table bridge "$TABLE" >/dev/null 2>&1 || true
    if nft list table bridge "$TABLE" >/dev/null 2>&1; then rc=6; fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0)); then
    echo "R23D2B1Q_EXACT_DISPOSABLE_DOCKER_NFT_CLEANUP_PASS"
  else
    echo "R23D2B1Q_LAB_FAILED" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if nft list table bridge "$TABLE" >/dev/null 2>&1 ||
   ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   docker network inspect "$NET" >/dev/null 2>&1; then
  echo "R23D2B1Q_RESOURCE_NAME_COLLISION_STOP" >&2;exit 4
fi
docker pull python:3.12-alpine >/dev/null
OWN_DOCKER=1
docker network create --driver bridge --internal \
  --subnet 172.31.252.0/28 --gateway 172.31.252.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=false" \
  "$NET" >/dev/null
[[ "$(docker network inspect -f '{{.Internal}}' "$NET")" == "true" ]]
common=(--read-only --cap-drop ALL --security-opt no-new-privileges
  --restart no --label r23d2b1q.lab=disposable
  --pids-limit 64 --memory 96m
  --mount "type=bind,src=$FAKE,dst=/lab/probe.py,readonly")
# server has NO network while not admitted. Docker auto-start cannot
# accidentally create a path to the fake client before a network connect.
docker create --name "$SERVER" "${common[@]}" --network none \
   python:3.12-alpine python3 -B /lab/probe.py --serve >/dev/null
docker create --name "$CLIENT" "${common[@]}" \
  --network "$NET" --ip "$CLIENT_IP" \
  python:3.12-alpine sleep 900 >/dev/null
docker start "$CLIENT" >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "false"
echo "R23D2B1Q_NETWORK_DETACHED_DEFAULT_DENY_INITIAL_PASS"

# Find exact host veth on owned bridge. Docker can recreate this after
# daemon restart. Do not trust an old veth name/ifindex.
peer_dev() {
  local pid link
  pid="$(docker inspect -f '{{.State.Pid}}' "$CLIENT")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  link="$(nsenter -t "$pid" -n ip -o link show dev eth0 |
    python3 -B -c 'import re,sys
m=re.search(r"eth0@if([1-9][0-9]*):",sys.stdin.read())
if not m:sys.exit(2)
print(m.group(1))')" || return 1
  ip -j link show | python3 -B -c '
import json,sys
i=int(sys.argv[1]);br=sys.argv[2]
rows=[r for r in json.load(sys.stdin) if r.get("ifindex")==i]
if len(rows)!=1:sys.exit(2)
row=rows[0];name=row["ifname"].split("@",1)[0]
if not name.startswith("veth") or row.get("master")!=br:sys.exit(3)
print(name)
' "$link" "$BRIDGE"
}
arm_tc() {
  local dev="$1"
  if tc qdisc show dev "$dev" | grep -q "clsact"; then
    echo "R23D2B1Q_UNEXPECTED_TC_ALREADY_EXISTS" >&2;exit 5
  fi
  tc qdisc add dev "$dev" clsact
  tc filter add dev "$dev" ingress protocol arp pref 5 \
    matchall action goto chain 1
  tc filter add dev "$dev" ingress protocol ip pref 10 u32 \
    match ip src "$CLIENT_IP"/32 match ip dst "$SERVER_IP"/32 \
    match ip protocol 6 0xff match ip dport 17420 0xffff \
    action goto chain 1
  tc filter add dev "$dev" ingress protocol all pref 20 matchall action drop
  tc filter add dev "$dev" ingress chain 1 protocol all pref 5 matchall action pass
}
PEER="$(peer_dev)" || { echo "R23D2B1Q_PEER_NOT_OWNED_STOP" >&2;exit 5; }
arm_tc "$PEER"
# The precise bridge-family filter is a SECOND independent kernel barrier.
create_nft() {
  nft add table bridge "$TABLE"
  OWN_NFT=1
  nft "add set bridge $TABLE authorized { type ipv4_addr; flags timeout; timeout 20s; }"
  nft "add chain bridge $TABLE permit { type filter hook forward priority -250; policy accept; }"
  nft add rule bridge "$TABLE" permit iifname "$PEER" ether type arp accept
  nft add rule bridge "$TABLE" permit oifname "$PEER" ether type arp accept
  nft add rule bridge "$TABLE" permit iifname "$PEER" ether type ip \
    ip saddr "$CLIENT_IP" ip daddr "$SERVER_IP" tcp dport 17420 \
    ip saddr @authorized accept
  nft add rule bridge "$TABLE" permit oifname "$PEER" ether type ip \
    ip saddr "$SERVER_IP" ip daddr "$CLIENT_IP" tcp sport 17420 \
    ip daddr @authorized accept
  nft add rule bridge "$TABLE" permit iifname "$PEER" drop
  nft add rule bridge "$TABLE" permit oifname "$PEER" drop
}
run_gate() {
  local label="$1"
  # systemd-run launches an actual transient oneshot unit. It does not
  # install or edit a systemd unit under /etc or assert a real host boot.
  R23D2B1Q_EPHEMERAL_ONLY=true systemd-run --quiet --pipe --wait --collect \
     --unit "r23d2b1q-$label-$SUFFIX" -p Type=oneshot \
     --setenv=GITHUB_ACTIONS=true --setenv=CI=true \
     --setenv=RUNNER_OS=Linux --setenv=RUNNER_ENVIRONMENT=github-hosted \
     --setenv=R23D2B1Q_EPHEMERAL_ONLY=true \
     /usr/bin/python3 -B "$ADMIT" --suffix "$SUFFIX" --admit
}
probe() {
  docker exec "$CLIENT" python3 -B /lab/probe.py --probe \
    --addr "$SERVER_IP" --port "$1" >/dev/null 2>&1
}
permit() {
  local port="$1"
  for _ in $(seq 1 45); do
    if probe "$port";then return 0;fi
    sleep 0.15
  done
  return 1
}
deny() {
  if probe "$1";then echo "R23D2B1Q_UNEXPECTED_FAKE_TCP_EXPOSURE" >&2;exit 5;fi
}
detached() {
  docker inspect --format '{{json .NetworkSettings.Networks}}' "$SERVER" |
  python3 -B -c 'import json,sys
n=json.load(sys.stdin) or {}
assert set(n).issubset({"none"}), "SERVER_CONNECTED_TO_REAL_BRIDGE"'
}
# SAFETY CONTROL: even if Docker starts the fake gateway without a
# qualifying systemd admission unit, it has no network path.
docker start "$SERVER" >/dev/null
deny 17420
detached
docker stop --time 1 "$SERVER" >/dev/null
echo "R23D2B1Q_UNEXPECTED_DOCKER_START_CANNOT_REACH_DETACHED_SERVER_PASS"

# With tc armed but the B1Q nft table missing, the real transient systemd
# admission must FAIL. It must not attach the stopped fake gateway.
if run_gate no-policy >"$TMP/no-policy.log" 2>&1;then
  echo "R23D2B1Q_MISSING_NFT_INTERLOCK_BYPASSED" >&2;exit 5
fi
detached
deny 17420
echo "R23D2B1Q_SYSTEMD_ONESHOT_REJECTS_MISSING_NFT_PASS"

create_nft
run_gate initial >"$TMP/initial.log" 2>&1 || {
  cat "$TMP/initial.log" >&2
  echo "R23D2B1Q_VALID_SYSTEMD_ADMISSION_FAILED" >&2;exit 5
}
permit 17420 || { echo "R23D2B1Q_ALLOWED_REAL_DOCKER_TCP_MISSING" >&2;exit 5; }
deny 17421
echo "R23D2B1Q_SYSTEMD_TC_NFT_ATTESTED_ADMISSION_PASS"

# Expose exact failure of nft alone: with server ATTACHED and alive,
# deleting the only B1Q nft policy does NOT stop permitted traffic through
# the independent tc allowlist. Explicit evidence of unsafe total loss.
nft delete table bridge "$TABLE"
OWN_NFT=0
permit 17420 || { echo "R23D2B1Q_NFT_LOSS_CONTROL_NOT_LIVE" >&2;exit 5; }
deny 17421
echo "R23D2B1Q_NFT_LOSS_WHILE_ATTACHED_EXPOSES_ALLOWED_TCP_RELEASE_BLOCKER"

# Independent network disconnect is stronger than firewall-only cleanup:
# it must cut the still-live fake container off even with nft absent.
docker network disconnect -f "$NET" "$SERVER" >/dev/null
detached
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "true"
deny 17420
echo "R23D2B1Q_NETWORK_DETACH_CUTS_LIVE_GATEWAY_WHILE_NFT_MISSING_PASS"
docker stop --time 1 "$SERVER" >/dev/null

# Restart ACTUAL Docker service on dedicated runner only. All fake peers
# have restart=no. Detached fake gateway must stay disconnected afterward.
timeout 100 systemctl restart docker
for _ in $(seq 1 70);do
  if docker info >/dev/null 2>&1;then break;fi
  sleep 0.3
done
docker info >/dev/null
for name in "$SERVER" "$CLIENT"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$name")" != "false" ]];then
    echo "R23D2B1Q_DOCKER_UNEXPECTED_AUTO_RESTART" >&2;exit 5
  fi
done
detached
echo "R23D2B1Q_REAL_DOCKER_RESTART_PRESERVES_DISCONNECTED_ADMISSION_HOLD_PASS"

docker start "$CLIENT" >/dev/null
NEW_PEER="$(peer_dev)" || exit 5
if tc qdisc show dev "$NEW_PEER" | grep -q clsact;then
  echo "R23D2B1Q_STALE_TC_SURVIVED_RECREATED_VETH" >&2;exit 5
fi
# Both barriers absent; real transient systemd service still refuses.
if run_gate both-missing >"$TMP/both-missing.log" 2>&1;then
  echo "R23D2B1Q_BOTH_FENCES_MISSING_BYPASS" >&2;exit 5
fi
detached
deny 17420
echo "R23D2B1Q_SYSTEMD_REJECTS_DAEMON_RESTART_WITHOUT_BOTH_FENCES_PASS"

PEER="$NEW_PEER"
arm_tc "$PEER"
create_nft
run_gate recovered >"$TMP/recovered.log" 2>&1 || {
  cat "$TMP/recovered.log" >&2
  echo "R23D2B1Q_REPAIRED_SYSTEMD_ADMISSION_FAILED" >&2;exit 5
}
permit 17420 || { echo "R23D2B1Q_RECOVERED_REAL_TCP_MISSING" >&2;exit 5; }
deny 17421
echo "R23D2B1Q_REATTEST_BOTH_KERNEL_FENCES_BEFORE_DOCKER_READMISSION_PASS"
echo "R23D2B1Q_EPHEMERAL_INTERLOCK_LAB_PASS_BOOT_REBOOT_HOLD"
