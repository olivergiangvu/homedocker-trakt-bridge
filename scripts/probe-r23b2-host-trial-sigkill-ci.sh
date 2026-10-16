#!/usr/bin/env bash
# Real Docker/AF_UNIX/UID65534/receipt/SIGKILL lab. Disposable GitHub runner only.
set -euo pipefail
if [[ $# -ne 1 || "$1" != "--ci-ephemeral-only" ||
  "$(printenv GITHUB_ACTIONS || true)" != "true" ||
  "$(printenv CI || true)" != "true" ||
  "$(printenv RUNNER_OS || true)" != "Linux" ||
  "$(printenv RUNNER_ENVIRONMENT || true)" != "github-hosted" ||
  "$(id -u)" != "0" ]];then
  echo "R23B2_V3_STOP_NOT_DISPOSABLE_GITHUB_ROOT" >&2; exit 3
fi
for tool in docker python3 nsenter setpriv ss curl;do
  command -v "$tool" >/dev/null || exit 3
done
[[ -z "$(docker ps -aq --no-trunc)" ]] || { echo "R23B2_V3_RUNNER_NOT_EMPTY" >&2; exit 4; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LAUNCHER="$ROOT/scripts/host-trial-r23b2.py"
MOCK="$ROOT/scripts/probe-r23b2-host-aio-fake-ci.py"
C="homedocker-v140-r23b-canary"
S="homedocker-v140-r23b2-unix-sidecar"
NET="homedocker_v140_r23b_local"
SOCKET="/run/homedocker-r23b2-aio"
STATE="/var/lib/homedocker-r23b2-trial"
TMP="$(mktemp -d)"
chmod 0700 "$TMP"
MOCK_PID=""
PARENT_PID=""
OWN_NET=0
OWN_CANARY=0
cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if [[ "$PARENT_PID" =~ ^[0-9]+$ ]] && kill -0 "$PARENT_PID" 2>/dev/null;then
    kill "$PARENT_PID" 2>/dev/null || true
    wait "$PARENT_PID" 2>/dev/null || true
  fi
  if [[ "$MOCK_PID" =~ ^[0-9]+$ ]];then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
  # The job began with ZERO Docker containers. Exact lab names only.
  docker rm -f "$S" >/dev/null 2>&1 || true
  if ((OWN_CANARY));then docker rm -f "$C" >/dev/null 2>&1 || true;fi
  if ((OWN_NET));then docker network rm "$NET" >/dev/null 2>&1 || true;fi
  rm -rf "$TMP"
  if ((rc==0));then echo "R23B2_V3_CI_EXACT_DOCKER_CLEANUP_PASS";fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ ! -e "$SOCKET" && ! -e "$STATE" ]] || exit 4
[[ -z "$(ss -H -lnt '( sport = :3240 or sport = :17041 or sport = :17141 )')" ]] || exit 4
docker pull python:3.12-alpine >/dev/null
docker network create --driver bridge \
  --opt com.docker.network.bridge.enable_ip_masquerade=false "$NET" >/dev/null
OWN_NET=1
docker create --name "$C" --restart no --network "$NET" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 96m --label homedocker.phase=r23b1 \
  -p 127.0.0.1:17041:7000 -p 127.0.0.1:17141:7101 \
  -e JELLYFIN_GATEWAY_UPSTREAM_URL=http://127.0.0.1:9999/jellyfin \
  python:3.12-alpine python3 -c 'import time;time.sleep(500)' >/dev/null
OWN_CANARY=1
docker start "$C" >/dev/null
python3 -B "$MOCK" >"$TMP/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 70);do
  if curl --noproxy '*' -s --max-time 1 -o /dev/null \
    http://127.0.0.1:3240/jellyfin/Users/Me;then break;fi
  sleep .1
done
curl --noproxy '*' -s --max-time 2 -o /dev/null \
  http://127.0.0.1:3240/jellyfin/Users/Me
python3 -B "$LAUNCHER" >"$TMP/dry.log" 2>&1
grep -q R23B2_LAUNCHER_DRY_RUN_READY_NO_CHANGES "$TMP/dry.log"
echo "R23B2_V3_CI_PRECHECK_PASS"
python3 -u -B "$LAUNCHER" --apply \
  --confirm R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY --ttl-seconds 120 \
  >"$TMP/apply.log" 2>&1 &
PARENT_PID=$!
ready=0
for _ in $(seq 1 140);do
  if grep -q R23B2_TEMPORARY_RELAY_RUNNING "$TMP/apply.log" 2>/dev/null &&
    [[ -S "$SOCKET/readonly.sock" && -f "$STATE/receipt.json" ]] &&
    [[ "$(docker inspect -f '{{.State.Running}}' "$S" 2>/dev/null)" == true ]];then
    ready=1;break
  fi
  kill -0 "$PARENT_PID" 2>/dev/null || break
  sleep .15
done
((ready)) || { echo "R23B2_V3_LAUNCH_FAILED" >&2;exit 5; }
docker exec "$C" python3 -B -c '
import http.client
c=http.client.HTTPConnection("127.0.0.1",9999,timeout=5)
c.request("GET","/jellyfin/Shows/NextUp?Limit=20",
          headers={"X-Emby-Token":"R23B2-FAKE-CI-TOKEN-ONLY"})
r=c.getresponse()
assert r.status==200,r.status
body=r.read()
assert b"PlaybackPositionTicks" in body and b"ep-ci" in body
c.close()
'
echo "R23B2_V3_CI_REAL_UNIX_GET_PASS"
kill -9 "$PARENT_PID"
wait "$PARENT_PID" 2>/dev/null || true
PARENT_PID=""
[[ -f "$STATE/receipt.json" ]]
python3 -B "$LAUNCHER" --recover >"$TMP/recover.log" 2>&1
grep -q R23B2_RECOVERY_INSPECT_ONLY_RECEIPT_VALID_NO_CHANGES "$TMP/recover.log"
[[ -f "$STATE/receipt.json" ]]
python3 -B "$LAUNCHER" --recover \
  --confirm R23B2_EXACT_OWNER_RECOVERY_ONLY >"$TMP/recover.log" 2>&1 || {
  echo "R23B2_V3_REAL_RECOVERY_FAILED" >&2; exit 5;
}
grep -q R23B2_EXACT_OWNER_RECOVERY_COMPLETE "$TMP/recover.log"
[[ ! -e "$STATE" && ! -e "$SOCKET" ]]
if docker inspect "$S" >/dev/null 2>&1;then exit 5;fi
[[ "$(docker inspect -f '{{.State.Running}}' "$C")" == true ]]
python3 -B "$LAUNCHER" >"$TMP/after.log" 2>&1
grep -q R23B2_LAUNCHER_DRY_RUN_READY_NO_CHANGES "$TMP/after.log"
echo "R23B2_V3_CI_REAL_SIGKILL_EXACT_RECOVERY_PASS"
