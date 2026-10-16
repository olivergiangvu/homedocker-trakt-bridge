#!/usr/bin/env bash
# R23D-2B1T: non-internal Docker bridge, actual SNAT and loopback published
# port, Docker socket permission boundary; disposable GitHub-hosted runner ONLY.
# NO HomeDocker/SSH/server apply, no real secrets, no external internet peer.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
  "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
  "${RUNNER_OS:-}" != "Linux" ||
  "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "$(id -u)" -ne 0 ]];then
  echo "R23D2B1T_STOP_NOT_DISPOSABLE_GITHUB_RUNNER" >&2;exit 3
fi
for bin in docker ip python3 setpriv nsenter ss timeout;do
  command -v "$bin" >/dev/null || { echo "R23D2B1T_DEPENDENCY_MISSING" >&2;exit 3; }
done
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23D2B1T_STOP_RUNNER_ALREADY_HAS_CONTAINERS" >&2;exit 4;
}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
FAKE="$ROOT/scripts/probe-v140-r23d2b1n-docker-fake-ci.py"
ECHO="$ROOT/scripts/probe-v140-r23d2b1t-external-echo-ci.py"
test -f "$FAKE" && test -f "$ECHO"
SUFFIX="$$"
NETWORK="r23d2b1t-nat-$SUFFIX"
SERVER="r23d2b1t-gw-$SUFFIX"
CLIENT="r23d2b1t-client-$SUFFIX"
BRIDGE="br-r23d2b1t"
NS="r23d2b1t-ext-$SUFFIX"
HOST_VETH="b1th$SUFFIX"
EXT_VETH="b1te$SUFFIX"
TMP="$(mktemp -d)"
chmod 700 "$TMP"
OWN_DOCKER=0
OWN_NS=0
ECHO_PID=""
cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$ECHO_PID" ]];then
    kill "$ECHO_PID" 2>/dev/null || true
    wait "$ECHO_PID" 2>/dev/null || true
  fi
  if ((OWN_DOCKER));then
    docker rm -f "$CLIENT" "$SERVER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    if docker container inspect "$CLIENT" >/dev/null 2>&1 ||
       docker container inspect "$SERVER" >/dev/null 2>&1 ||
       docker network inspect "$NETWORK" >/dev/null 2>&1;then rc=6;fi
  fi
  if ((OWN_NS));then
    ip netns del "$NS" >/dev/null 2>&1 || rc=6
    ip link del "$HOST_VETH" >/dev/null 2>&1 || true
    if ip netns list | awk '{print $1}' | grep -Fxq "$NS";then rc=6;fi
    if ip link show dev "$HOST_VETH" >/dev/null 2>&1;then rc=6;fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0));then echo "R23D2B1T_EXACT_DOCKER_NAMESPACE_CLEANUP_PASS"
  else echo "R23D2B1T_EPHEMERAL_LAB_STOP" >&2;fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ip link show dev "$BRIDGE" >/dev/null 2>&1 ||
   ip link show dev "$HOST_VETH" >/dev/null 2>&1 ||
   ip netns list | awk '{print $1}' | grep -Fxq "$NS" ||
   docker network inspect "$NETWORK" >/dev/null 2>&1;then
  echo "R23D2B1T_RESOURCE_COLLISION_STOP" >&2;exit 4
fi
# The external peer is a fake namespace on this ephemeral runner, NOT
# a public IP or a remote service. Only the owned namespace is mutated.
ip netns add "$NS"
OWN_NS=1
ip link add "$HOST_VETH" type veth peer name "$EXT_VETH"
ip link set "$EXT_VETH" netns "$NS"
ip addr add 172.31.249.1/30 dev "$HOST_VETH"
ip link set "$HOST_VETH" up
ip -n "$NS" addr add 172.31.249.2/30 dev "$EXT_VETH"
ip -n "$NS" link set "$EXT_VETH" up
ip -n "$NS" link set lo up
ip netns exec "$NS" python3 -B "$ECHO" --serve >"$TMP/echo.log" 2>&1 &
ECHO_PID="$!"
for _ in $(seq 1 40);do
  if grep -q "R23D2B1T_EXTERNAL_NAMESPACE_ECHO_LISTENING" "$TMP/echo.log";then
    break
  fi
  sleep 0.1
done
grep -q "R23D2B1T_EXTERNAL_NAMESPACE_ECHO_LISTENING" "$TMP/echo.log" || {
  echo "R23D2B1T_EXTERNAL_ECHO_FAILED_TO_START" >&2;exit 5;
}

docker pull python:3.12-alpine >/dev/null
OWN_DOCKER=1
docker network create --driver bridge \
  --subnet 172.31.250.0/28 --gateway 172.31.250.1 \
  --opt "com.docker.network.bridge.name=$BRIDGE" \
  --opt "com.docker.network.bridge.enable_ip_masquerade=true" \
  "$NETWORK" >/dev/null
docker network inspect "$NETWORK" | python3 -B -c '
import json,sys
x=json.load(sys.stdin)
assert len(x)==1
n=x[0]
assert n["Driver"]=="bridge" and n["Internal"] is False
assert n["Options"].get("com.docker.network.bridge.enable_ip_masquerade")=="true"
assert n["IPAM"]["Config"][0]["Subnet"]=="172.31.250.0/28"
'
echo "R23D2B1T_NONINTERNAL_PRODUCTION_SHAPED_BRIDGE_PASS"

base=(--read-only --cap-drop ALL --security-opt no-new-privileges
      --label r23d2b1t.lab=ephemeral --restart no --pids-limit 64
      --memory 96m
      --mount "type=bind,src=$FAKE,dst=/lab/fake.py,readonly"
      --mount "type=bind,src=$ECHO,dst=/lab/upstream.py,readonly")
# Fake gateway initially has no network even if an outside caller starts it.
# Publish solely on HOST LOOPBACK with an ephemeral assigned TCP port.
docker create --name "$SERVER" "${base[@]}" --network none \
  --publish 127.0.0.1::17420 \
  python:3.12-alpine python3 -B /lab/fake.py --serve >/dev/null
docker create --name "$CLIENT" "${base[@]}" \
  --network "$NETWORK" --ip 172.31.250.2 \
  python:3.12-alpine sleep 600 >/dev/null
docker start "$CLIENT" >/dev/null
docker start "$SERVER" >/dev/null
if docker exec "$CLIENT" python3 -B /lab/fake.py --probe \
  --addr 172.31.252.3 --port 17420 >/dev/null 2>&1;then
  echo "R23D2B1T_UNEXPECTED_DETACHED_FAKE_TCP_REACHABLE" >&2;exit 5
fi
docker stop --time 1 "$SERVER" >/dev/null
echo "R23D2B1T_UNADMITTED_SERVER_NO_NETWORK_PASS"

# Explicit least-privilege check against the standard non-root user. This
# does NOT protect against root, or users with Docker socket/group access.
python3 -B - <<'PY'
import os,stat,socket
sock="/var/run/docker.sock"
assert stat.S_ISSOCK(os.stat(sock).st_mode)
assert not (os.stat(sock).st_mode & stat.S_IWOTH),"WORLD_WRITABLE_DOCKER_SOCKET"
PY
if setpriv --reuid 65534 --regid 65534 --clear-groups \
     docker --host unix:///var/run/docker.sock ps -q \
     >"$TMP/unprivileged.out" 2>&1;then
  echo "R23D2B1T_UNPRIVILEGED_DOCKER_SOCKET_BYPASS_STOP" >&2;exit 5
fi
docker --host unix:///var/run/docker.sock ps -q >/dev/null
echo "R23D2B1T_UNPRIVILEGED_DOCKER_SOCKET_ACCESS_DENIED_PASS"

# Controlled test-only admission attaches the fake gateway on the
# NON-INTERNAL bridge; this test is NOT a production guardian/boot gate.
docker network disconnect none "$SERVER"
docker network connect --ip 172.31.250.3 "$NETWORK" "$SERVER"
docker start "$SERVER" >/dev/null
docker inspect "$SERVER" | python3 -B -c '
import json,sys
c=json.load(sys.stdin)[0]
assert c["State"]["Running"]
assert set(c["NetworkSettings"]["Networks"])=={sys.argv[1]}
p=c["NetworkSettings"]["Ports"]
assert "17421/tcp" not in p or not p["17421/tcp"]
pub=p["17420/tcp"]
assert len(pub)==1 and pub[0]["HostIp"]=="127.0.0.1"
assert 0<int(pub[0]["HostPort"])<65536
assert c["HostConfig"]["Privileged"] is False
assert c["HostConfig"]["RestartPolicy"]["Name"]=="no"
' "$NETWORK"
HOSTPORT="$(docker port "$SERVER" 17420/tcp)"
[[ "$HOSTPORT" =~ ^127\.0\.0\.1:[1-9][0-9]*$ ]] || {
  echo "R23D2B1T_LOOPBACK_PUBLISH_SCOPE_INVALID" >&2;exit 5
}
PUBLISHED_PORT="${HOSTPORT##*:}"
python3 -B - "$PUBLISHED_PORT" <<'PY'
import socket,sys,time
p=int(sys.argv[1])
def good(addr,port):
    try:
        with socket.create_connection((addr,port),timeout=0.8) as s:
            s.settimeout(0.8)
            s.sendall(b"r23d2b1n-fake")
            return s.recv(2)==b"ok"
    except (OSError,TimeoutError):
        return False
# First prove the fake gateway is actually listening in its namespace;
# a published-port test must not pass or fail because of startup races.
for _ in range(45):
    if good("172.31.250.3",17420):
        break
    time.sleep(0.15)
else:
    raise SystemExit("R23D2B1T_FAKE_DOCKER_SERVER_NOT_READY_STOP")
print("R23D2B1T_REAL_FAKE_CONTAINER_TCP_READY_PASS")
for _ in range(45):
    if good("127.0.0.1",p):
        break
    time.sleep(0.15)
else:
    raise SystemExit("R23D2B1T_LOOPBACK_PUBLISH_NOT_REACHABLE_STOP")
print("R23D2B1T_REAL_LOOPBACK_HOST_PORT_PUBLISH_PASS")
PY
# Do not claim external exposure is fully excluded solely by loopback
# binding: this is a 127.0.0.1 binding/scope test, not remote-LAN probing.
echo "R23D2B1T_UNPUBLISHED_FAKE_PORT_ABSENT_PASS"

# Test REAL Docker source NAT against isolated external namespace, not
# a public host. The remote peer must observe only the host veth IP.
docker exec "$CLIENT" python3 -B /lab/upstream.py --probe
echo "R23D2B1T_NONINTERNAL_REAL_SNAT_PATH_PASS"

docker network disconnect -f "$NETWORK" "$SERVER"
if python3 -B - "$PUBLISHED_PORT" <<'PY'
import socket,sys
try:
    with socket.create_connection(("127.0.0.1",int(sys.argv[1])),timeout=0.7) as s:
        s.sendall(b"r23d2b1n-fake")
        if s.recv(2)==b"ok":
            raise SystemExit(0)
except OSError:
    pass
raise SystemExit(1)
PY
then
  echo "R23D2B1T_PORT_STILL_REACHABLE_AFTER_DETACH_STOP" >&2;exit 5
fi
echo "R23D2B1T_NETWORK_DISCONNECT_CUTS_PUBLISHED_PORT_PASS"
echo "R23D2B1T_REAL_DOCKER_NAT_AND_SOCKET_BOUNDARY_LAB_PASS_RELEASE_HOLD"
