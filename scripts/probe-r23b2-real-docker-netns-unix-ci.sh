#!/usr/bin/env bash
# Real Docker shared-netns + AF_UNIX B1R23B2 integration; DISPOSABLE GH CI ONLY.
set -euo pipefail
if [[ "$#" -ne 1 || "$1" != "--ci-ephemeral-only" ||
  "$(printenv GITHUB_ACTIONS || true)" != "true" ||
  "$(printenv CI || true)" != "true" ||
  "$(printenv RUNNER_OS || true)" != "Linux" ||
  "$(printenv RUNNER_ENVIRONMENT || true)" != "github-hosted" ||
  "$(id -u)" != "0" ]];then
  echo "R23B2_STOP_NOT_DISPOSABLE_GITHUB_RUNNER" >&2;exit 3
fi
for tool in docker python3 ss ip setpriv timeout;do
  command -v "$tool" >/dev/null || exit 3
done
[[ -z "$(docker ps -aq --no-trunc)" ]] || {
  echo "R23B2_STOP_OTHER_DOCKER_CONTAINERS_ON_RUNNER" >&2;exit 4
}
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SOURCE="$ROOT/scripts/r23b2-experimental-aio-unix-get-relay.py"
MOCK="$ROOT/scripts/probe-r23b2-host-aio-fake-ci.py"
C="r23b2-ci-fake-canary-$$"
S="r23b2-ci-sidecar-$$"
NET="r23b2-ci-local-$$"
SOCKETDIR="/run/homedocker-r23b2-aio"
TMP="$(mktemp -d)"
chmod 0700 "$TMP"
HOST_PID=""
MOCK_PID=""
OWN_DIR=0
OWN_DOCKER=0
cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if ((OWN_DOCKER));then
    docker rm -f "$S" "$C" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    if docker container inspect "$C" >/dev/null 2>&1 ||
       docker container inspect "$S" >/dev/null 2>&1 ||
       docker network inspect "$NET" >/dev/null 2>&1;then rc=6;fi
  fi
  for pid in "$HOST_PID" "$MOCK_PID";do
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]];then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  if ((OWN_DIR));then
    if [[ "$(stat -c '%u:%a' "$SOCKETDIR" 2>/dev/null)" != "65534:700" ]];then
      echo "R23B2_PRIVATE_DIR_OWNER_DRIFT" >&2;rc=6
    else
      rm -f -- "$SOCKETDIR/readonly.sock" "$SOCKETDIR/relay.py"
      rmdir -- "$SOCKETDIR" || rc=6
    fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0));then
    echo "R23B2_REAL_DOCKER_NETNS_OWNED_RESOURCES_CLEANUP_PASS"
  else
    echo "R23B2_REAL_DOCKER_NETNS_CI_FAILURE" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ ! -e "$SOCKETDIR" ]] || { echo "R23B2_SOCKET_DIR_COLLISION_STOP" >&2;exit 4; }
[[ -z "$(ss -H -lnt '( sport = :3240 )')" ]] || {
  echo "R23B2_HOST_FAKE_AIO_PORT_COLLISION_STOP" >&2;exit 4
}
docker pull python:3.12-alpine >/dev/null
OWN_DOCKER=1
docker network create --driver bridge \
  --opt com.docker.network.bridge.enable_ip_masquerade=false "$NET" >/dev/null
docker create --name "$C" --network "$NET" --restart no \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 96m --label r23b2.lab=disposable \
  python:3.12-alpine sleep 500 >/dev/null
docker start "$C" >/dev/null
[[ "$(docker inspect -f '{{.State.Running}}' "$C")" == "true" ]]
mkdir -m 0700 "$SOCKETDIR"
OWN_DIR=1
chown 65534:65534 "$SOCKETDIR"
install -m 0600 -o 65534 -g 65534 "$SOURCE" "$SOCKETDIR/relay.py"
python3 -B "$MOCK" >"$TMP/mock.log" 2>&1 &
MOCK_PID="$!"
for _ in $(seq 1 45);do
  if python3 -B -c '
import socket
s=socket.create_connection(("127.0.0.1",3240),timeout=.3)
s.close()
' >/dev/null 2>&1;then break;fi
  sleep 0.1
done
python3 -B -c '
import socket
s=socket.create_connection(("127.0.0.1",3240),timeout=1)
s.close()
'
setpriv --reuid 65534 --regid 65534 --clear-groups \
  python3 -B "$SOCKETDIR/relay.py" --role host >"$TMP/host.log" 2>&1 &
HOST_PID="$!"
for _ in $(seq 1 40);do
  [[ -S "$SOCKETDIR/readonly.sock" ]] && break
  sleep 0.1
done
[[ -S "$SOCKETDIR/readonly.sock" ]] || {
  echo "R23B2_HOST_UNIX_SOCKET_NOT_READY" >&2;exit 5
}
[[ "$(stat -c '%u:%a' "$SOCKETDIR/readonly.sock")" == "65534:600" ]]
echo "R23B2_PRIVATE_HOST_AIO_UNIX_SOCKET_PASS"

docker run -d --name "$S" --restart no --pull never \
  --network "container:$C" --read-only \
  --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 96m --label r23b2.lab=disposable \
  --mount "type=bind,src=$SOCKETDIR,dst=$SOCKETDIR,readonly" \
  python:3.12-alpine python3 -B "$SOCKETDIR/relay.py" --role sidecar >/dev/null

docker inspect "$S" | python3 -B -c '
import json,sys
s=json.load(sys.stdin)[0]
h=s["HostConfig"]
assert s["State"]["Running"]
assert h["NetworkMode"].startswith("container:")
assert h["Privileged"] is False
assert h["RestartPolicy"]["Name"]=="no"
assert h["ReadonlyRootfs"] is True
assert h["UsernsMode"] in ("",None)
assert s["Config"]["User"]=="65534:65534"
assert len(s["Mounts"])==1 and s["Mounts"][0]["RW"] is False
'
for _ in $(seq 1 45);do
  if docker exec "$C" python3 -B -c '
import socket
s=socket.create_connection(("127.0.0.1",9999),timeout=.3)
s.close()
' >/dev/null 2>&1;then break;fi
  sleep 0.1
done
docker exec "$C" python3 -B -c '
import http.client
c=http.client.HTTPConnection("127.0.0.1",9999,timeout=4)
c.request("GET","/jellyfin/Shows/NextUp?Limit=20",
          headers={"X-Emby-Token":"R23B2-FAKE-CI-TOKEN-ONLY"})
r=c.getresponse()
assert r.status==200,r.status
b=r.read()
assert b"PlaybackPositionTicks" in b and b"ep-ci" in b
c.close()
'
echo "R23B2_REAL_DOCKER_CANARY_NAMESPACE_GET_TO_HOST_AIO_PASS"
docker exec "$C" python3 -B -c '
import http.client
c=http.client.HTTPConnection("127.0.0.1",9999,timeout=4)
c.request("POST","/jellyfin/Users/AuthenticateByName",
          headers={"X-Emby-Token":"R23B2-FAKE-CI-TOKEN-ONLY"})
r=c.getresponse()
assert r.status==405,r.status
c.close()
'
echo "R23B2_REAL_DOCKER_NAMESPACE_LOGIN_WRITE_REJECT_PASS"
docker exec "$C" python3 -B -c '
import http.client
c=http.client.HTTPConnection("127.0.0.1",9999,timeout=4)
c.request("GET","/jellyfin/UserItems/Resume?Limit=20",
          headers={"X-Emby-Token":"R23B2-FAKE-CI-TOKEN-ONLY"})
r=c.getresponse()
assert r.status==404,r.status
c.close()
'
echo "R23B2_REAL_DOCKER_NAMESPACE_RESUME_DEFAULT_OFF_PASS"
docker exec "$C" python3 -B -c '
import socket
try:
 s=socket.create_connection(("127.0.0.1",3240),timeout=.7)
 s.close()
 raise SystemExit("R23B2_HOST_LOOPBACK_UNEXPECTEDLY_SHARED")
except (ConnectionRefusedError,TimeoutError,OSError):
 pass
'
echo "R23B2_REAL_DOCKER_HOST_LOOPBACK_NOT_EXPOSED_PASS"
docker stop --time 2 "$S" >/dev/null
if docker exec "$C" python3 -B -c '
import socket
s=socket.create_connection(("127.0.0.1",9999),timeout=.5)
s.close()
' >/dev/null 2>&1;then
  echo "R23B2_SIDECAR_ROLLBACK_PORT_STILL_EXPOSED_STOP" >&2;exit 5
fi
echo "R23B2_SIDECAR_STOP_RESTORES_SENTINEL_IN_REAL_DOCKER_NETNS_PASS"
