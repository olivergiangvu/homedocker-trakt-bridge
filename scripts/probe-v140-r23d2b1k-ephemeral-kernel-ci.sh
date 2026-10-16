#!/usr/bin/env bash
# R23D-2B1K: ONLY on a disposable GitHub-hosted Linux CI runner.
# Root actions are scoped to three NEW network namespaces, never HomeDocker.
set -euo pipefail
if [[ "${1:-}" != "--ci-ephemeral-only" || "${GITHUB_ACTIONS:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" || "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "${CI:-}" != "true" ]]; then
  echo "R23D2B1K_STOP_NOT_EPHEMERAL_GITHUB_CI" >&2
  exit 3
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1K_STOP_REQUIRES_EPHEMERAL_RUNNER_ROOT" >&2
  exit 3
fi
for executable in ip nft python3 sysctl; do
  command -v "$executable" >/dev/null || { echo "R23D2B1K_MISSING_DEPENDENCY" >&2; exit 3; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROBE="$ROOT/scripts/probe-v140-r23d2b1k-ci.py"
RENDER="$ROOT/scripts/render-v140-r23d-nft-policy-offline.py"
ATTEST="$ROOT/scripts/attest-v140-r23d-persistent-guard.py"
test -f "$PROBE" && test -f "$RENDER" && test -f "$ATTEST"
# Namespaces and process IDs exclusively owned by THIS disposable runner job.
NS_S="r23d2b1k-s-$$"
NS_C="r23d2b1k-c-$$"
NS_E="r23d2b1k-e-$$"
OWNED=()
PIDS=()
TEMP="$(mktemp -d)"
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  for ((idx=${#OWNED[@]}-1;idx>=0;idx--)); do
    ip netns del "${OWNED[$idx]}" 2>/dev/null || true
  done
  rm -rf -- "$TEMP"
  if ((rc != 0)); then echo "R23D2B1K_ISOLATED_KERNEL_LAB_STOP" >&2; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for n in "$NS_S" "$NS_C" "$NS_E"; do
  if ip netns list | awk '{print $1}' | grep -Fxq "$n"; then
    echo "R23D2B1K_NAMESPACE_COLLISION_STOP" >&2
    exit 4
  fi
  ip netns add "$n"
  OWNED+=("$n")
  ip -n "$n" link set lo up
done

# Private L3 Docker-like bridge in the isolated server namespace.
ip -n "$NS_S" link add br-r23dgate type bridge
ip -n "$NS_S" addr add 172.31.254.1/28 dev br-r23dgate
ip -n "$NS_S" -6 addr add fd42:23d:1::1/64 dev br-r23dgate nodad
ip -n "$NS_S" link set br-r23dgate up

ip -n "$NS_S" link add r23ks0 type veth peer name r23kc0
ip -n "$NS_S" link set r23kc0 netns "$NS_C"
ip -n "$NS_S" link set r23ks0 master br-r23dgate
ip -n "$NS_S" link set r23ks0 up
ip -n "$NS_C" addr add 172.31.254.2/28 dev r23kc0
ip -n "$NS_C" -6 addr add fd42:23d:1::2/64 dev r23kc0 nodad
ip -n "$NS_C" link set r23kc0 up

# Forwarded destination is another namespace; no production network touched.
ip -n "$NS_S" link add r23ks1 type veth peer name r23ke1
ip -n "$NS_S" link set r23ke1 netns "$NS_E"
ip -n "$NS_S" addr add 172.31.253.1/29 dev r23ks1
ip -n "$NS_E" addr add 172.31.253.2/29 dev r23ke1
ip -n "$NS_S" link set r23ks1 up
ip -n "$NS_E" link set r23ke1 up
ip -n "$NS_C" route add 172.31.253.0/29 via 172.31.254.1
ip -n "$NS_E" route add 172.31.254.0/28 via 172.31.253.1
ip netns exec "$NS_S" sysctl -qw net.ipv4.ip_forward=1

ip netns exec "$NS_S" python3 -B "$PROBE" --serve --addr 172.31.254.1 --port 17420 >/dev/null 2>&1 &
PIDS+=("$!")
ip netns exec "$NS_S" python3 -B "$PROBE" --serve --addr 172.31.254.1 --port 17421 >/dev/null 2>&1 &
PIDS+=("$!")
ip netns exec "$NS_S" python3 -B "$PROBE" --serve --addr fd42:23d:1::1 --port 17421 >/dev/null 2>&1 &
PIDS+=("$!")
ip netns exec "$NS_E" python3 -B "$PROBE" --serve --addr 172.31.253.2 --port 17420 >/dev/null 2>&1 &
PIDS+=("$!")

probe() {
  local ns="$1" ipaddr="$2" port="$3"
  ip netns exec "$ns" python3 -B "$PROBE" --probe --addr "$ipaddr" --port "$port" >/dev/null 2>&1
}
ready() {
  local ns="$1" ipaddr="$2" port="$3"
  for _ in $(seq 1 30); do
    if probe "$ns" "$ipaddr" "$port"; then return 0; fi
    sleep 0.1
  done
  return 1
}
# Prove all fake servers are reachable BEFORE installing guard. Otherwise
# negative probes could pass merely because the destination is unavailable.
ready "$NS_C" 172.31.254.1 17420
ready "$NS_C" 172.31.254.1 17421
ready "$NS_C" fd42:23d:1::1 17421
ready "$NS_C" 172.31.253.2 17420
echo "R23D2B1K_PREGUARD_TCP_CONNECTIVITY_PASS"

# Render EXACT existing reviewed policy; add it only INSIDE NS_S.
python3 -B - "$RENDER" > "$TEMP/guard.nft" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location("r23d2b1k_policy",sys.argv[1])
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.stdout.write(mod.render_policy(**mod.SYNTHETIC))
PY
ip netns exec "$NS_S" nft --check --file "$TEMP/guard.nft"
ip netns exec "$NS_S" nft --file "$TEMP/guard.nft"

# Live kernel NFT JSON attested structurally; not proof of the future
# HomeDocker lifetime/boot ownership or a Docker peer.
ip netns exec "$NS_S" nft -j list table inet homedocker_r23d_guard \
  | python3 -B -c 'import importlib.util,json,sys
spec=importlib.util.spec_from_file_location("guard",sys.argv[1])
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
r=v.renderer()
assert v.check_guard(json.load(sys.stdin),**r.SYNTHETIC) is True' "$ATTEST"

probe "$NS_C" 172.31.254.1 17420 || {
  echo "R23D2B1K_ALLOWED_RELAY_BLOCKED_STOP" >&2; exit 5;
}
if probe "$NS_C" 172.31.254.1 17421; then
  echo "R23D2B1K_FORBIDDEN_INPUT_REACHABLE_STOP" >&2; exit 5
fi
if probe "$NS_C" 172.31.253.2 17420; then
  echo "R23D2B1K_FORWARD_EGRESS_REACHABLE_STOP" >&2; exit 5
fi
if probe "$NS_C" fd42:23d:1::1 17421; then
  echo "R23D2B1K_IPV6_INPUT_BYPASS_STOP" >&2; exit 5
fi
echo "R23D2B1K_KERNEL_INPUT_FORWARD_IPV6_TCP_CONTROLS_PASS"
echo "R23D2B1K_GITHUB_EPHEMERAL_NAMESPACE_LAB_PASS_NO_HOMEDOCKER_APPLY"
