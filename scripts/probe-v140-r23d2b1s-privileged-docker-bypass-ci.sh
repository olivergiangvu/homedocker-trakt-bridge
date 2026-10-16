#!/usr/bin/env bash
# R23D-2B1S: PRIVILEGED DOCKER API BYPASS negative control, built atop B1R.
# R23D-2B1R: PERSISTENT systemd unit on a disposable GH runner ONLY.
# Writes a temporary owned unit under /etc/systemd/system and RESTARTS Docker.
# NEVER run on HomeDocker or any self-hosted runner. NOT a true VM reboot.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
  "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
  "${RUNNER_OS:-}" != "Linux" ||
  "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1R_STOP_NOT_DISPOSABLE_GITHUB_RUNNER" >&2
  exit 3
fi
for bin in systemctl docker python3 nft ip tc nsenter timeout; do
  command -v "$bin" >/dev/null || exit 3
done
systemctl is-active --quiet docker || exit 4
[[ "$(docker info --format '{{.LiveRestoreEnabled}}')" == "false" ]] || exit 4
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1R_STOP_OTHER_RUNNER_CONTAINERS" >&2; exit 4;
}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MON="$ROOT/scripts/probe-v140-r23d2b1r-persistent-systemd-monitor-ci.py"
FAKE="$ROOT/scripts/probe-v140-r23d2b1n-docker-fake-ci.py"
test -f "$MON" && test -f "$FAKE"
SUFFIX="$$"
NET="r23d2b1q-private-$SUFFIX"
CLIENT="r23d2b1q-client-$SUFFIX"
SERVER="r23d2b1q-server-$SUFFIX"
BRIDGE="br-r23d2b1q"
TABLE="r23d2b1q_lease"
CLIENT_IP="172.31.252.2"
SERVER_IP="172.31.252.3"
UNIT="r23d2b1r-monitor-$SUFFIX.service"
UNIT_FILE="/etc/systemd/system/$UNIT"
OWN_DOCKER=0
OWN_NFT=0
OWN_UNIT=0
PAUSED_PID=""
TMP="$(mktemp -d)"
chmod 700 "$TMP"
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$PAUSED_PID" ]]; then
    kill -CONT "$PAUSED_PID" 2>/dev/null || true
  fi
  if (( OWN_UNIT )); then
    systemctl stop "$UNIT" >/dev/null 2>&1 || true
    systemctl disable "$UNIT" >/dev/null 2>&1 || true
    rm -f -- "$UNIT_FILE"
    systemctl daemon-reload >/dev/null 2>&1 || rc=6
    if [[ -e "$UNIT_FILE" || -e "/etc/systemd/system/docker.service.wants/$UNIT" ]];then
      rc=6
    fi
  fi
  if (( OWN_DOCKER ));then
    if ! docker info >/dev/null 2>&1;then
      timeout 60 systemctl start docker >/dev/null 2>&1 || true
    fi
    docker rm -f "$CLIENT" "$SERVER" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    if docker container inspect "$CLIENT" >/dev/null 2>&1 ||
       docker container inspect "$SERVER" >/dev/null 2>&1 ||
       docker network inspect "$NET" >/dev/null 2>&1; then rc=6;fi
  fi
  if (( OWN_NFT ));then
    nft delete table bridge "$TABLE" >/dev/null 2>&1 || true
    if nft list table bridge "$TABLE" >/dev/null 2>&1;then rc=6;fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0));then
    echo "R23D2B1R_OWNED_SYSTEMD_DOCKER_NFT_CLEANUP_PASS"
  else
    echo "R23D2B1R_EPHEMERAL_LAB_STOP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -e "$UNIT_FILE" ]] ||
   ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   nft list table bridge "$TABLE" >/dev/null 2>&1 ||
   docker network inspect "$NET" >/dev/null 2>&1;then
  echo "R23D2B1R_RESOURCE_COLLISION_STOP" >&2;exit 4
fi
docker pull python:3.12-alpine >/dev/null
OWN_DOCKER=1
docker network create --driver bridge --internal \
  --subnet 172.31.252.0/28 --gateway 172.31.252.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=false" \
  "$NET" >/dev/null
base=(--read-only --cap-drop ALL --security-opt no-new-privileges
  --restart no --label r23d2b1q.lab=disposable --pids-limit 64
  --memory 96m --mount "type=bind,src=$FAKE,dst=/lab/probe.py,readonly")
docker create --name "$SERVER" "${base[@]}" --network none \
  python:3.12-alpine python3 -B /lab/probe.py --serve >/dev/null
docker create --name "$CLIENT" "${base[@]}" \
  --network "$NET" --ip "$CLIENT_IP" \
  python:3.12-alpine sleep 1200 >/dev/null
docker start "$CLIENT" >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "false"

# Exact, real installed systemd service. It cannot alter host services except
# the test-owned fake Docker peer. Environment guard is not an attestation.
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=R23D-2B1R disposable real-Docker admission monitor (CI ONLY)
After=docker.service
BindsTo=docker.service
PartOf=docker.service
ConditionPathExists=$MON

[Service]
Type=notify
NotifyAccess=main
ExecStart=/usr/bin/python3 -B $MON --suffix $SUFFIX
Environment=GITHUB_ACTIONS=true
Environment=CI=true
Environment=RUNNER_OS=Linux
Environment=RUNNER_ENVIRONMENT=github-hosted
Environment=R23D2B1R_EPHEMERAL_ONLY=true
Environment=R23D2B1Q_EPHEMERAL_ONLY=true
Restart=always
RestartSec=1
WatchdogSec=8
TimeoutStopSec=5
KillMode=control-group

[Install]
WantedBy=docker.service
EOF
OWN_UNIT=1
systemctl daemon-reload
systemd-analyze verify "$UNIT_FILE" >/dev/null
systemctl enable "$UNIT" >/dev/null
[[ "$(systemctl is-enabled "$UNIT")" == "enabled" ]]
systemctl start "$UNIT"
systemctl is-active --quiet "$UNIT"
test "$(docker inspect -f '{{.State.Running}}' "$SERVER")" = "false"
echo "R23D2B1R_INSTALLED_PERSISTENT_SYSTEMD_SERVICE_INITIAL_DEFAULT_HOLD_PASS"

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
idx=int(sys.argv[1]);br=sys.argv[2]
rows=[i for i in json.load(sys.stdin) if i.get("ifindex")==idx]
if len(rows)!=1:sys.exit(2)
v=rows[0];name=v["ifname"].split("@",1)[0]
if not name.startswith("veth") or v.get("master")!=br:sys.exit(3)
print(name)
' "$link" "$BRIDGE"
}
arm_tc() {
  local dev="$1"
  if tc qdisc show dev "$dev" | grep -q clsact;then
    echo "R23D2B1R_TC_ALREADY_ATTACHED_STOP" >&2;exit 5
  fi
  tc qdisc add dev "$dev" clsact
  tc filter add dev "$dev" ingress protocol arp pref 5 matchall action goto chain 1
  tc filter add dev "$dev" ingress protocol ip pref 10 u32 \
    match ip src "$CLIENT_IP"/32 match ip dst "$SERVER_IP"/32 \
    match ip protocol 6 0xff match ip dport 17420 0xffff action goto chain 1
  tc filter add dev "$dev" ingress protocol all pref 20 matchall action drop
  tc filter add dev "$dev" ingress chain 1 protocol all pref 5 matchall action pass
}
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
probe() {
  docker exec "$CLIENT" python3 -B /lab/probe.py --probe \
    --addr "$SERVER_IP" --port "$1" >/dev/null 2>&1
}
ready() {
  local port="$1"
  for _ in $(seq 1 45);do
    if probe "$port";then return 0;fi
    sleep 0.16
  done
  return 1
}
deny() {
  if probe "$1";then
    echo "R23D2B1R_FORBIDDEN_FAKE_TCP_REACHABLE_STOP" >&2;exit 5
  fi
}
detached() {
  docker inspect -f '{{json .NetworkSettings.Networks}}' "$SERVER" |
    python3 -B -c 'import json,sys
n=json.load(sys.stdin) or {}
assert set(n).issubset({"none"}), "FAKE_SERVER_STILL_ATTACHED"'
}
PEER="$(peer_dev)"
arm_tc "$PEER"
create_nft
ready 17420 || {
  systemctl status "$UNIT" --no-pager >&2 || true
  journalctl -u "$UNIT" -n 25 --no-pager >&2 || true
  echo "R23D2B1R_AUTOMATIC_SYSTEMD_ADMISSION_FAILED" >&2;exit 5
}
deny 17421
echo "R23D2B1R_SYSTEMD_DAEMON_AUTO_ADMITTED_AFTER_BOTH_FENCES_PASS"

# Remove nft table on the disposable runner ONLY: kernel policy disappears
# while Docker fake gateway remains online. Persistent unit must notice and
# disconnect exact fake server, without any test-side Docker disconnect.
nft delete table bridge "$TABLE"
OWN_NFT=0
start="$(date +%s%3N)"
CLOSED=0
for _ in $(seq 1 40);do
  if detached >/dev/null 2>&1;then CLOSED=1;break;fi
  sleep 0.12
done
elapsed="$(( $(date +%s%3N) - start ))"
if [[ "$CLOSED" != "1" || "$elapsed" -gt 3500 ]];then
  echo "R23D2B1R_AUTOMATIC_NFT_LOSS_DETACH_NOT_BOUNDED" >&2;exit 5
fi
deny 17420
echo "R23D2B1R_PERSISTENT_MONITOR_AUTO_DETACHED_AFTER_NFT_LOSS_PASS"

# With server disconnected, recreate nft policy; controller itself must
# re-admit only after both current fences are attested again.
create_nft
ready 17420 || {
  journalctl -u "$UNIT" -n 25 --no-pager >&2 || true
  echo "R23D2B1R_MONITOR_READMISSION_FAILED" >&2;exit 5
}
deny 17421
echo "R23D2B1R_PERSISTENT_MONITOR_SAFE_AUTO_READMISSION_PASS"

# Kill only the exact installed service MainPID; systemd must restart it.
OLD_PID="$(systemctl show -p MainPID --value "$UNIT")"
[[ "$OLD_PID" =~ ^[1-9][0-9]*$ ]]
kill -KILL "$OLD_PID"
RESTARTED=0
for _ in $(seq 1 70);do
  NEW_PID="$(systemctl show -p MainPID --value "$UNIT")"
  if [[ "$NEW_PID" =~ ^[1-9][0-9]*$ && "$NEW_PID" != "$OLD_PID" ]] &&
       systemctl is-active --quiet "$UNIT";then
    RESTARTED=1;break
  fi
  sleep 0.17
done
[[ "$RESTARTED" == "1" ]] || {
  echo "R23D2B1R_SYSTEMD_RESTART_AFTER_CRASH_STOP" >&2;exit 5
}
ready 17420
deny 17421
echo "R23D2B1R_PERSISTENT_UNIT_SIGKILL_SYSTEMD_RESTART_PASS"

# Make server disconnected again before Docker daemon restart; the monitor
# should keep the hold and not try to auto-admit without fresh fences.
nft delete table bridge "$TABLE"
OWN_NFT=0
for _ in $(seq 1 40);do
  if detached >/dev/null 2>&1;then break;fi
  sleep 0.15
done
detached
timeout 100 systemctl restart docker
for _ in $(seq 1 65);do
  if docker info >/dev/null 2>&1;then break;fi
  sleep 0.3
done
docker info >/dev/null
for name in "$CLIENT" "$SERVER";do
  [[ "$(docker inspect -f '{{.State.Running}}' "$name")" == "false" ]] || {
    echo "R23D2B1R_DOCKER_AUTO_STARTED_FAKE_PEER_STOP" >&2;exit 5;
  }
done
detached
echo "R23D2B1R_DOCKER_DAEMON_RESTART_NO_AUTO_ADMISSION_PASS"

docker start "$CLIENT" >/dev/null
PEER="$(peer_dev)"
if tc qdisc show dev "$PEER" | grep -q clsact;then
  echo "R23D2B1R_OLD_TC_UNEXPECTEDLY_PRESENT" >&2;exit 5
fi
# If this service was not automatically reactivated by docker.service,
# this is an incomplete startup lifecycle; fail rather than claiming boot.
if ! systemctl is-active --quiet "$UNIT";then
  echo "R23D2B1R_SYSTEMD_BINDING_NOT_AUTO_RESTARTED" >&2;exit 5
fi
arm_tc "$PEER"
create_nft
ready 17420 || {
  journalctl -u "$UNIT" -n 30 --no-pager >&2 || true
  echo "R23D2B1R_POST_DOCKER_DAEMON_AUTO_RECOVERY_FAILED" >&2;exit 5
}
deny 17421
echo "R23D2B1R_DOCKER_RESTART_REBUILT_FENCES_AUTO_READMISSION_PASS"

# R23D-2B1S PRIVILEGED-DOCKER NEGATIVE CONTROL (NEVER PRODUCTION):
# exact fake server is detached and stopped by controller. Pause only its
# exact systemd MainPID, remove only the owned nft table. A root/Docker API
# user can bypass systemd admission, directly attach and start the server.
# With tc's narrow permitted fake port still armed, synthetic TCP becomes
# reachable despite a missing kernel nft lease. This MUST be treated NO-GO.
nft delete table bridge "$TABLE"
OWN_NFT=0
for _ in $(seq 1 40); do
  if detached >/dev/null 2>&1;then break;fi
  sleep 0.12
done
detached
[[ "$(docker inspect -f '{{.State.Running}}' "$SERVER")" == "false" ]] || {
  echo "R23D2B1S_EXPECTED_STOPPED_FAKE_SERVER_STOP" >&2;exit 5;
}
PAUSED_PID="$(systemctl show -p MainPID --value "$UNIT")"
[[ "$PAUSED_PID" =~ ^[1-9][0-9]*$ ]] || exit 5
kill -STOP "$PAUSED_PID"
# DOCKER API privilege used only on exact disposable fake server.
docker network disconnect none "$SERVER" >/dev/null 2>&1 || true
docker network connect --ip "$SERVER_IP" "$NET" "$SERVER" >/dev/null
docker start "$SERVER" >/dev/null
ready 17420 || {
  echo "R23D2B1S_BYPASS_POSITIVE_TCP_UNREACHABLE_STOP" >&2;exit 5
}
deny 17421
echo "R23D2B1S_PRIVILEGED_DOCKER_BYPASS_EXPOSED_FAKE_TCP_NO_GO"
kill -CONT "$PAUSED_PID" || true
PAUSED_PID=""
for _ in $(seq 1 60);do
  if detached >/dev/null 2>&1;then break;fi
  sleep 0.15
done
detached
deny 17420
echo "R23D2B1S_MONITOR_EVENTUAL_RECOVERY_CUT_BYPASSED_PEER_PASS"
echo "R23D2B1S_PRIVILEGED_API_BYPASS_LAB_PASS_STABLE_RELEASE_HOLD"
echo "R23D2B1R_PERSISTENT_SYSTEMD_REAL_DOCKER_CI_PASS_TRUE_REBOOT_HOLD"
