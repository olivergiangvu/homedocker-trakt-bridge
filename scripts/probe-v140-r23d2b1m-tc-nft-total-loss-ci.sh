#!/usr/bin/env bash
# R23D-2B1M: EXCLUSIVELY disposable GitHub-hosted runner, never HomeDocker.
# Prove a boot-order L2 barrier and an INDEPENDENT tc ingress kernel filter
# survive total nftables loss inside a private server network namespace.
set -euo pipefail
if [[ "$#" -ne 1 || "$1" != "--ci-ephemeral-only" ||
      "${GITHUB_ACTIONS:-}" != "true" ||
      "${RUNNER_OS:-}" != "Linux" ||
      "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "${CI:-}" != "true" || "$(id -u)" -ne 0 ]]; then
  echo "R23D2B1M_STOP_NOT_EPHEMERAL_GITHUB_RUNNER" >&2
  exit 3
fi
for bin in ip nft tc python3 sysctl; do
  command -v "$bin" >/dev/null || { echo "R23D2B1M_MISSING_DEPENDENCY" >&2; exit 3; }
done
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PROBE="$ROOT/scripts/probe-v140-r23d2b1k-ci.py"
RENDER="$ROOT/scripts/render-v140-r23d-nft-policy-offline.py"
test -f "$PROBE" && test -f "$RENDER"
NS_H="r23d2b1m-h-$$"
NS_C="r23d2b1m-c-$$"
NS_E="r23d2b1m-e-$$"
OWNED=()
PIDS=()
TMP="$(mktemp -d)"
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  for ((idx=${#OWNED[@]}-1;idx>=0;idx--)); do
    ip netns del "${OWNED[$idx]}" 2>/dev/null || true
  done
  for ns in "${OWNED[@]}"; do
    if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then rc=6; fi
  done
  rm -rf -- "$TMP"
  if ((rc==0)); then echo "R23D2B1M_OWNED_NAMESPACES_CLEANUP_PASS"
  else echo "R23D2B1M_LAB_STOP" >&2; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for ns in "$NS_H" "$NS_C" "$NS_E"; do
  if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then
    echo "R23D2B1M_NAMESPACE_COLLISION_STOP" >&2; exit 4
  fi
  ip netns add "$ns"
  OWNED+=("$ns")
  ip -n "$ns" link set lo up
done
# Exact local test-only addressing. Keep the client-to-bridge server veth
# DOWN until the independent tc fence is installed (boot barrier).
ip -n "$NS_H" link add br-r23dgate type bridge
ip -n "$NS_H" addr add 172.31.254.1/28 dev br-r23dgate
ip -n "$NS_H" -6 addr add fd42:23d:1::1/64 dev br-r23dgate nodad
ip -n "$NS_H" link set br-r23dgate up
ip -n "$NS_H" link add r23ms0 type veth peer name r23mc0
ip -n "$NS_H" link set r23mc0 netns "$NS_C"
ip -n "$NS_H" link set r23ms0 master br-r23dgate
# NOTE: r23ms0 stays DOWN until tc is effective.
ip -n "$NS_C" addr add 172.31.254.2/28 dev r23mc0
ip -n "$NS_C" -6 addr add fd42:23d:1::2/64 dev r23mc0 nodad
ip -n "$NS_C" link set r23mc0 up

ip -n "$NS_H" link add r23ms1 type veth peer name r23me1
ip -n "$NS_H" link set r23me1 netns "$NS_E"
ip -n "$NS_H" addr add 172.31.253.1/29 dev r23ms1
ip -n "$NS_E" addr add 172.31.253.2/29 dev r23me1
ip -n "$NS_H" link set r23ms1 up
ip -n "$NS_E" link set r23me1 up
ip -n "$NS_C" route add 172.31.253.0/29 via 172.31.254.1
ip -n "$NS_E" route add 172.31.254.0/28 via 172.31.253.1
ip netns exec "$NS_H" sysctl -qw net.ipv4.ip_forward=1

server() {
  local ns="$1" addr="$2" port="$3"
  ip netns exec "$ns" python3 -B "$PROBE" --serve --addr "$addr" --port "$port" >/dev/null 2>&1 &
  PIDS+=("$!")
}
probe() {
  ip netns exec "$NS_C" python3 -B "$PROBE" --probe --addr "$1" --port "$2" >/dev/null 2>&1
}
ready() {
  local addr="$1" port="$2"
  for _ in $(seq 1 30); do
    if probe "$addr" "$port"; then return 0; fi
    sleep 0.1
  done
  return 1
}
must_deny() {
  if probe "$1" "$2"; then
    echo "R23D2B1M_FORBIDDEN_TCP_REACHABLE_STOP" >&2
    exit 5
  fi
}
server "$NS_H" 172.31.254.1 17420
server "$NS_H" 172.31.254.1 17421
server "$NS_H" fd42:23d:1::1 17421
server "$NS_E" 172.31.253.2 17420

# While L2 is DOWN, even an early or compromised fake gateway cannot be
# reached through the client peer.
must_deny 172.31.254.1 17420
echo "R23D2B1M_LINK_DOWN_BEFORE_FENCE_PASS"

# A totally separate TC ingress filter is installed BEFORE unblocking the
# client-facing link. This persists when nft flush ruleset is executed.
# Exact IPv4 source/destination/protocol/destination port only; all other
# IPv4 and ALL IPv6 are rejected by the independent terminal drop.
install_tc() {
  ip netns exec "$NS_H" tc qdisc add dev r23ms0 clsact
  ip netns exec "$NS_H" tc filter add dev r23ms0 ingress protocol ip pref 10 u32 \
    match ip src 172.31.254.2/32 \
    match ip dst 172.31.254.1/32 \
    match ip protocol 6 0xff \
    match ip dport 17420 0xffff \
    action pass
  ip netns exec "$NS_H" tc filter add dev r23ms0 ingress protocol all pref 20 \
    matchall action drop
}
install_tc
# Confirm rules exist before allowing this namespace's link to be brought UP.
ip netns exec "$NS_H" tc filter show dev r23ms0 ingress > "$TMP/tc-before.txt"
grep -q "pref 10" "$TMP/tc-before.txt"
grep -q "pref 20" "$TMP/tc-before.txt"
ip -n "$NS_H" link set r23ms0 up

ready 172.31.254.1 17420 || {
  echo "R23D2B1M_ALLOWED_BOOT_RELAY_UNREACHABLE_STOP" >&2; exit 5;
}
must_deny 172.31.254.1 17421
must_deny 172.31.253.2 17420
must_deny fd42:23d:1::1 17421
echo "R23D2B1M_TC_BOOT_BARRIER_AND_PACKET_CONTROLS_PASS"

# Install the same reviewed nft table in the PRIVATE server namespace.
python3 -B - "$RENDER" > "$TMP/guard.nft" <<'PY'
import importlib.util,sys
s=importlib.util.spec_from_file_location("r23d2b1m",sys.argv[1])
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
sys.stdout.write(m.render_policy(**m.SYNTHETIC))
PY
ip netns exec "$NS_H" nft --check -f "$TMP/guard.nft"
ip netns exec "$NS_H" nft -f "$TMP/guard.nft"
ready 172.31.254.1 17420
must_deny 172.31.254.1 17421
echo "R23D2B1M_NFT_PLUS_TC_COMBINED_PASS"

# TOTAL NFT RULESET LOSS in NS_H only, never the runner's/root netns.
# This is NOT a real production host's firewall outage.
ip netns exec "$NS_H" nft flush ruleset
ip netns exec "$NS_H" nft -j list ruleset > "$TMP/flushed.json"
python3 -B - "$TMP/flushed.json" <<'PY'
import json,sys
data=json.load(open(sys.argv[1],encoding="utf8"))
assert isinstance(data,dict) and data.get("nftables")==[{"metainfo":data["nftables"][0]["metainfo"]}], "RULESET_NOT_EMPTY_AFTER_FLUSH"
PY
ready 172.31.254.1 17420
must_deny 172.31.254.1 17421
must_deny 172.31.253.2 17420
must_deny fd42:23d:1::1 17421
echo "R23D2B1M_TOTAL_NFT_FLUSH_TC_INDEPENDENT_DENY_PASS"

# Independent tc safety must still be effective even after a replacement
# test listener starts. NO production restart, Docker daemon, or systemd here.
last=$((${#PIDS[@]}-4))
kill "${PIDS[$last]}" 2>/dev/null || true
wait "${PIDS[$last]}" 2>/dev/null || true
server "$NS_H" 172.31.254.1 17420
ready 172.31.254.1 17420
must_deny 172.31.254.1 17421
echo "R23D2B1M_SYNTHETIC_LISTENER_RESTART_GUARD_PERSISTS_PASS"

# NEGATIVE CONTROL: with nft emptied AND tc removed, the previously
# prohibited alive fake server/forward/IPv6 ports MUST become reachable.
# This explicitly demonstrates the tc guard (not a dead listener) blocked.
ip netns exec "$NS_H" tc qdisc del dev r23ms0 clsact
ready 172.31.254.1 17421
ready 172.31.253.2 17420
ready fd42:23d:1::1 17421
echo "R23D2B1M_REMOVING_LAST_FENCE_EXPOSES_FORBIDDEN_TCP_POSITIVE_CONTROL"

# Reinstall exact independent tc guard and reconfirm it blocks again.
install_tc
ready 172.31.254.1 17420
must_deny 172.31.254.1 17421
must_deny 172.31.253.2 17420
must_deny fd42:23d:1::1 17421
echo "R23D2B1M_TC_FENCE_RESTORED_PASS"
echo "R23D2B1M_EPHEMERAL_NAMESPACE_KERNEL_LAB_PASS_NO_HOMEDOCKER_APPLY"
