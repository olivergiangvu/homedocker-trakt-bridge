#!/usr/bin/env bash
# R23D-2B1L: disposable GitHub-hosted namespace failure lab, NEVER HomeDocker.
# Exercise a kernel-timed synthetic lease with an independent deny fence,
# kill its userspace lease issuer, and demonstrate fail-closed expiration.
set -euo pipefail

if [[ "${1:-}" != "--ci-ephemeral-only" || "${GITHUB_ACTIONS:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" || "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "${CI:-}" != "true" || "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1L_STOP_NOT_PRIVILEGED_EPHEMERAL_GITHUB_HOSTED_RUNNER" >&2
  exit 3
fi
for bin in ip nft python3; do
  command -v "$bin" >/dev/null || { echo "R23D2B1L_MISSING_CI_DEPENDENCY" >&2; exit 3; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROBE="$ROOT/scripts/probe-v140-r23d2b1k-ci.py"
RENDER="$ROOT/scripts/render-v140-r23d-nft-policy-offline.py"
test -f "$PROBE" && test -f "$RENDER"

NS_H="r23d2b1l-host-$$"
NS_C="r23d2b1l-client-$$"
OWNED=()
PIDS=()
GUARDIAN_PID=""
TEMP="$(mktemp -d)"
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$GUARDIAN_PID" ]]; then
    kill "$GUARDIAN_PID" 2>/dev/null || true
    wait "$GUARDIAN_PID" 2>/dev/null || true
  fi
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  for ((idx=${#OWNED[@]}-1;idx>=0;idx--)); do
    ip netns del "${OWNED[$idx]}" 2>/dev/null || true
  done
  for ns in "${OWNED[@]}"; do
    if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then rc=6; fi
  done
  rm -rf -- "$TEMP"
  if ((rc==0)); then
    echo "R23D2B1L_OWNED_NAMESPACES_CLEANUP_PASS"
  else
    echo "R23D2B1L_PRIVATE_LAB_STOP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for ns in "$NS_H" "$NS_C"; do
  if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then
    echo "R23D2B1L_NAMESPACE_COLLISION_STOP" >&2
    exit 4
  fi
  ip netns add "$ns"
  OWNED+=("$ns")
  ip -n "$ns" link set lo up
done

ip -n "$NS_H" link add br-r23dgate type bridge
ip -n "$NS_H" addr add 172.31.254.1/28 dev br-r23dgate
ip -n "$NS_H" link set br-r23dgate up
ip -n "$NS_H" link add r23lhs0 type veth peer name r23lcc0
ip -n "$NS_H" link set r23lcc0 netns "$NS_C"
ip -n "$NS_H" link set r23lhs0 master br-r23dgate
ip -n "$NS_H" link set r23lhs0 up
ip -n "$NS_C" addr add 172.31.254.2/28 dev r23lcc0
ip -n "$NS_C" link set r23lcc0 up

ip netns exec "$NS_H" python3 -B "$PROBE" --serve --addr 172.31.254.1 --port 17420 >/dev/null 2>&1 &
PIDS+=("$!")
ip netns exec "$NS_H" python3 -B "$PROBE" --serve --addr 172.31.254.1 --port 17421 >/dev/null 2>&1 &
PIDS+=("$!")
probe() {
  ip netns exec "$NS_C" python3 -B "$PROBE" --probe --addr 172.31.254.1 --port "$1" >/dev/null 2>&1
}
ready() {
  local port="$1"
  for _ in $(seq 1 40); do
    if probe "$port"; then return 0; fi
    sleep 0.1
  done
  return 1
}
# Positive controls: the fake listener and L3 routing function independently
# of nft. If they do not, a negative test could report a false firewall PASS.
ready 17420
ready 17421
echo "R23D2B1L_PRE_FENCE_POSITIVE_TCP_CONTROLS_PASS"

# The *independent* fence exists before any lease, and remains installed
# regardless of the user's lease-issuer process. All actions stay in NS_H.
cat > "$TEMP/lease-fence.nft" <<'NFT'
table inet r23d2b1l_lease_fence {
  set authorized {
    type ipv4_addr
    flags timeout
    timeout 4s
  }
  chain input {
    type filter hook input priority -40; policy accept;
    iifname "br-r23dgate" ip saddr @authorized ip daddr 172.31.254.1 tcp dport 17420 accept
    iifname "br-r23dgate" drop
  }
  chain forward {
    type filter hook forward priority -40; policy accept;
    iifname "br-r23dgate" drop
  }
}
NFT
ip netns exec "$NS_H" nft --check --file "$TEMP/lease-fence.nft"
ip netns exec "$NS_H" nft --file "$TEMP/lease-fence.nft"
if probe 17420 || probe 17421; then
  echo "R23D2B1L_UNFENCED_BEFORE_LEASE_STOP" >&2
  exit 5
fi
echo "R23D2B1L_KERNEL_DEFAULT_DENY_BEFORE_GUARDIAN_PASS"

# Install the reviewed R23D structural rule table as a second later hook.
# ACCEPT in an earlier chain is not final; DROP in either chain stays final.
python3 -B - "$RENDER" > "$TEMP/reviewed-guard.nft" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location("r23d2b1l_existing_guard",sys.argv[1])
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.stdout.write(m.render_policy(**m.SYNTHETIC))
PY
ip netns exec "$NS_H" nft --check --file "$TEMP/reviewed-guard.nft"
ip netns exec "$NS_H" nft --file "$TEMP/reviewed-guard.nft"

# Synthetic guardian issuer in a SEPARATE process; it gets a one-shot 4s
# nft kernel lease, announces readiness, and waits to be SIGKILLed.
# The SIGKILL handler cannot run and cannot revoke the lease; the kernel TTL
# alone must subsequently fail closed. This is NOT a live watchdog/heartbeat.
ip netns exec "$NS_H" bash -c '
  set -euo pipefail
  nft add element inet r23d2b1l_lease_fence authorized "{ 172.31.254.2 timeout 4s }"
  echo R23D2B1L_LEASE_ISSUER_READY
  exec sleep 20
' > "$TEMP/guardian.log" 2>&1 &
GUARDIAN_PID="$!"

announced=0
for _ in $(seq 1 30); do
  if grep -Fxq "R23D2B1L_LEASE_ISSUER_READY" "$TEMP/guardian.log"; then
    announced=1
    break
  fi
  if ! kill -0 "$GUARDIAN_PID" 2>/dev/null; then break; fi
  sleep 0.05
done
if ((announced!=1)) || ! kill -0 "$GUARDIAN_PID" 2>/dev/null; then
  echo "R23D2B1L_LEASE_ISSUER_NOT_READY_STOP" >&2
  exit 5
fi
ready 17420
if probe 17421; then
  echo "R23D2B1L_FORBIDDEN_HOST_INPUT_REACHABLE_STOP" >&2
  exit 5
fi
echo "R23D2B1L_TEMPORARY_AUTHORIZED_RELAY_PASS"

# Hard crash bypasses Python or shell cleanup. The independent nft time-bound
# set expires without userspace renewal. The existing reviewed guard remains.
kill -KILL "$GUARDIAN_PID"
wait "$GUARDIAN_PID" 2>/dev/null || true
GUARDIAN_PID=""
sleep 5
if probe 17420 || probe 17421; then
  echo "R23D2B1L_CRASH_LEASE_DID_NOT_EXPIRE_STOP" >&2
  exit 5
fi
echo "R23D2B1L_SIGKILL_KERNEL_LEASE_EXPIRY_FAIL_CLOSED_PASS"

# Simulate a separate guard table disappearing; lease fence still blocks.
# This CANNOT simulate a total nft ruleset flush that removes the fence too.
ip netns exec "$NS_H" nft delete table inet homedocker_r23d_guard
if probe 17420 || probe 17421; then
  echo "R23D2B1L_SECONDARY_GUARD_LOSS_BYPASS_STOP" >&2
  exit 5
fi
echo "R23D2B1L_SECONDARY_GUARD_TABLE_LOSS_FENCE_REMAINS_PASS"

# Roll back only this namespace-owned independent fence. Healthy fake listeners
# must still accept traffic after removal, proving loss tests were meaningful.
ip netns exec "$NS_H" nft delete table inet r23d2b1l_lease_fence
ready 17420
ready 17421
echo "R23D2B1L_SCOPED_ROLLBACK_RESTORES_TCP_PASS"
echo "R23D2B1L_EPHEMERAL_KERNEL_CRASH_LAB_PASS_NO_HOMEDOCKER_APPLY"
