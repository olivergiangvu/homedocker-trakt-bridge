#!/usr/bin/env python3
"""R23D-1B: ONE temporary no-credential Docker bridge egress experiment.

DEFAULT: dry-run; creates nothing. --apply additionally requires an exact
confirmation string, current R23D-0 host preflight, and a locally present
immutable R23A/R23B image. Creates ONE TEMPORARY NONINTERNAL Docker bridge with
masquerading disabled, a short-lived host-gateway-only TCP positive control,
and ONE disposable unprivileged read-only Node probe. No user credentials,
network domains, mounts, write APIs, Trakt, AIO GETs or firewall rules set
directly. Docker network creation/removal DOES transiently alter Docker-managed
network/firewall state. Always tries to clean up only OWN test artifacts.

This can DETECT some unintended host/network access; negative TCP responses
DO NOT prove a firewall policy or internet-wide isolation. In ALL outcomes:
network_egress_firewall_verified = false. No real session may be forwarded.
"""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
PREFLIGHT = HERE / "audit-v140-r23d-host-preflight.py"
NETWORK = "homedocker_v140_r23d_egress_probe"
CONTAINER = "homedocker-v140-r23d-egress-probe"
CONFIRM = "R23D1B_TEMPORARY_ZERO_CREDENTIAL_PROBE_ONLY"
EXPECTED_ORIGINAL_TAG = "homedocker-trakt-bridge:v140-r23a-e442ce461209"
MAX_SECONDS = 65

# The probe uses Node built into the already present pinned Bridge image.
# It only attempts TCP handshakes, sends NO application data.
NODE_PROBE = r"""
const net = require('node:net');
const gw = process.argv[1], positivePort = Number(process.argv[2]);
const test = (host, port) => new Promise(resolve => {
  let settled = false;
  const finish = value => {
    if (!settled) { settled = true; resolve(value); }
    socket.destroy();
  };
  const socket = net.createConnection({host,port});
  socket.setTimeout(1250, () => finish(false));
  socket.once('connect', () => finish(true));
  socket.once('error', () => finish(false));
});
(async () => {
  const positive = await test(gw,positivePort);
  const hostPorts = [22, 80, 9000, 3240, 7000];
  const hostReachable = await Promise.all(hostPorts.map(p => test(gw,p)));
  // TEST-NET documentation addresses, NOT another user's LAN or account.
  const testNet = await Promise.all([
    test('192.0.2.1',80),test('198.51.100.1',80),test('203.0.113.1',80)
  ]);
  process.stdout.write(JSON.stringify({
    contract:'R23D1B_SYNTHETIC_TCP_ONLY',
    positive_control_host_gateway:positive,
    host_reachable_port_bools:hostReachable,
    testnet_reachable_bools:testNet,
    probes_performed:9,
    authentication_or_payload_sent:false,
    firewall_isolation_proven:false
  }));
})().catch(() => process.exit(3));
"""

class Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason)

STAGES = {
    "R23D0_PREFLIGHT_FAILED": "BASELINE",
    "DOCKER_COMMAND_FAILED": "DOCKER",
    "PROBE_ALREADY_EXISTS": "RESOURCE_COLLISION",
    "PROBE_NETWORK_ALREADY_EXISTS": "RESOURCE_COLLISION",
    "EXPECTED_IMMUTABLE_IMAGE_MISSING": "IMAGE",
    "DOCKER_PROBE_NETWORK_UNEXPECTED": "NETWORK",
    "DOCKER_PROBE_IMAGE_UNEXPECTED": "IMAGE",
    "PROBE_POSITIVE_CONTROL_FAILED": "GATEWAY_POSITIVE_CONTROL",
    "PROBE_OUTPUT_REJECTED": "RESULT_VALIDATION",
    "PROBE_CLEANUP_FAILED": "CLEANUP",
    "PROBE_TERMINATED_UNEXPECTEDLY": "RUNTIME",
    "EXPLICIT_APPLY_CONFIRMATION_REQUIRED": "AUTHORIZATION",
}

def docker(*args, timeout=20):
    try:
        completed = subprocess.run(["docker", *args], capture_output=True,
                                   text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("DOCKER_COMMAND_FAILED") from None
    if completed.returncode:
        raise Stop("DOCKER_COMMAND_FAILED")
    return completed.stdout.strip()

def exists(kind, name):
    try:
        subprocess.run(["docker", kind, "inspect", name], capture_output=True,
                       timeout=10, check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("DOCKER_COMMAND_FAILED") from None

def r23d0():
    if not PREFLIGHT.is_file() or PREFLIGHT.is_symlink():
        raise Stop("R23D0_PREFLIGHT_FAILED")
    try:
        spec = importlib.util.spec_from_file_location("r23d0_probe_guard", PREFLIGHT)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        result = guard.audit()
    except Exception:
        raise Stop("R23D0_PREFLIGHT_FAILED") from None
    if result.get("status") != "PREFLIGHT_PASS":
        raise Stop("R23D0_PREFLIGHT_FAILED")
    return guard

def preflight():
    if os.geteuid() != 0:
        raise Stop("R23D0_PREFLIGHT_FAILED")
    guard = r23d0()
    if exists("container", CONTAINER):
        raise Stop("PROBE_ALREADY_EXISTS")
    if exists("network", NETWORK):
        raise Stop("PROBE_NETWORK_ALREADY_EXISTS")
    original = guard.container(guard.R23A)
    current = guard.container(guard.R23B)
    if original["Config"]["Image"] != EXPECTED_ORIGINAL_TAG:
        raise Stop("EXPECTED_IMMUTABLE_IMAGE_MISSING")
    image_id = original.get("Image")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(image_id)):
        raise Stop("EXPECTED_IMMUTABLE_IMAGE_MISSING")
    if image_id != current.get("Image"):
        raise Stop("EXPECTED_IMMUTABLE_IMAGE_MISSING")
    try:
        obj = json.loads(docker("image", "inspect", image_id, timeout=15))
    except (ValueError, TypeError):
        raise Stop("EXPECTED_IMMUTABLE_IMAGE_MISSING") from None
    if len(obj) != 1 or obj[0].get("Id") != image_id:
        raise Stop("EXPECTED_IMMUTABLE_IMAGE_MISSING")
    return image_id

def assert_probe_network():
    result = json.loads(docker("network", "inspect", NETWORK))
    if len(result) != 1:
        raise Stop("DOCKER_PROBE_NETWORK_UNEXPECTED")
    net = result[0]
    opts = net.get("Options") or {}
    if (net.get("Internal") is not False or net.get("Driver") != "bridge"
            or opts.get("com.docker.network.bridge.enable_ip_masquerade") != "false"
            or (net.get("Labels") or {}).get("homedocker.phase") != "r23d1b-temp"):
        raise Stop("DOCKER_PROBE_NETWORK_UNEXPECTED")
    cfg = ((net.get("IPAM") or {}).get("Config") or [])
    try:
        gateway = ipaddress.IPv4Address(cfg[0]["Gateway"])
        subnet = ipaddress.IPv4Network(cfg[0]["Subnet"], strict=False)
    except (IndexError, KeyError, TypeError, ValueError):
        raise Stop("DOCKER_PROBE_NETWORK_UNEXPECTED") from None
    if gateway not in subnet or not gateway.is_private or gateway.is_loopback:
        raise Stop("DOCKER_PROBE_NETWORK_UNEXPECTED")
    return str(gateway)

def parse_probe(raw):
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        raise Stop("PROBE_OUTPUT_REJECTED") from None
    if (not isinstance(result, dict)
            or result.get("contract") != "R23D1B_SYNTHETIC_TCP_ONLY"
            or not isinstance(result.get("positive_control_host_gateway"), bool)
            or not isinstance(result.get("host_reachable_port_bools"), list)
            or len(result["host_reachable_port_bools"]) != 5
            or not all(isinstance(x,bool) for x in result["host_reachable_port_bools"])
            or not isinstance(result.get("testnet_reachable_bools"), list)
            or len(result["testnet_reachable_bools"]) != 3
            or not all(isinstance(x,bool) for x in result["testnet_reachable_bools"])
            or result.get("authentication_or_payload_sent") is not False
            or result.get("firewall_isolation_proven") is not False):
        raise Stop("PROBE_OUTPUT_REJECTED")
    return result

def cleanup(container_started, network_created):
    # Strict, explicit names only. Never prune or remove external resources.
    errors = []
    if container_started or exists("container", CONTAINER):
        try:
            docker("rm", "-f", CONTAINER, timeout=20)
        except Stop:
            errors.append("container")
    if network_created:
        try:
            docker("network", "rm", NETWORK, timeout=20)
        except Stop:
            errors.append("network")
    if errors:
        raise Stop("PROBE_CLEANUP_FAILED")

def apply(image_id):
    network_created = False
    container_created = False
    result = None
    try:
        docker("network", "create", "--driver", "bridge",
               "--opt", "com.docker.network.bridge.enable_ip_masquerade=false",
               "--label", "homedocker.phase=r23d1b-temp", NETWORK)
        network_created = True
        gateway = assert_probe_network()
        # Bind only Docker's temporary bridge gateway IP to a random available
        # TCP port. No network service, user data or credentials.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind((gateway, 0))
            listener.listen(2)
            listener.settimeout(20)
            control_port = listener.getsockname()[1]
            status = {"accepted": False}
            def accept_once():
                try:
                    conn, _ = listener.accept()
                    conn.close()
                    status["accepted"] = True
                except OSError:
                    pass
            worker = threading.Thread(target=accept_once, daemon=True)
            worker.start()
            opts = [
                "run", "--rm", "--name", CONTAINER, "--restart", "no",
                "--network", NETWORK, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "48", "--memory", "128m", "--cpus", "0.5",
                "--user", "1000:1000", "--label", "homedocker.phase=r23d1b-temp",
                "--entrypoint", "node",
                image_id, "-e", NODE_PROBE, gateway, str(control_port),
            ]
            # The run is synchronous. If it fails after creating a container,
            # cleanup() checks and removes only that unique name.
            raw = docker(*opts, timeout=MAX_SECONDS)
            result = parse_probe(raw)
            worker.join(timeout=2)
            if not status["accepted"] or not result["positive_control_host_gateway"]:
                raise Stop("PROBE_POSITIVE_CONTROL_FAILED")
    finally:
        # Also covers a failed docker run that created a stopped container.
        cleanup(container_created, network_created)
    return {
        "audit":"R23D1B_SYNTHETIC_DOCKER_EGRESS_OBSERVATION",
        "status":"OBSERVATION_COMPLETE_NOT_FIREWALL_QUALIFIED",
        "positive_control_reached_host_gateway":True,
        "unintended_host_tcp_service_reachable":any(result["host_reachable_port_bools"]),
        "host_service_reachable_count":sum(result["host_reachable_port_bools"]),
        "documentation_testnet_tcp_reachable_count":sum(result["testnet_reachable_bools"]),
        "testnet_failures_not_proof_of_blocking":True,
        "network_egress_firewall_verified":False,
        "docker_managed_network_and_rules_temporarily_changed":True,
        "existing_firewall_rules_directly_changed":False,
        "credentials_or_aio_history_accessed":False,
        "separate_canary_production_or_client_routes_changed":False,
        "probe_container_and_network_removed":True,
        "next":"REVIEW_HOST_INPUT_FORWARD_AND_TARGETED_FIREWALL_POLICY_BEFORE_AUTHENTICATED_GATEWAY",
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args=parser.parse_args()
    try:
        image_id=preflight()
        if not args.apply:
            print(json.dumps({
                "audit":"R23D1B_SYNTHETIC_EGRESS_DRY_RUN",
                "status":"DRY_RUN_READY",
                "image_pinned_to_existing_r23a_r23b":True,
                "extra_image_pull_or_build":False,
                "proposed_temporary_docker_bridge":True,
                "proposed_temporary_no_credentials_node_container":True,
                "docker_managed_network_rules_will_temporarily_change_on_apply":True,
                "production_container_or_nginx_changes":0,
                "actual_egress_probes_performed":False,
                "network_egress_firewall_verified":False,
            },indent=2))
            print("R23D1B_DRY_RUN_PASS")
            return
        if args.confirm != CONFIRM:
            raise Stop("EXPLICIT_APPLY_CONFIRMATION_REQUIRED")
        print(json.dumps(apply(image_id), indent=2))
        print("R23D1B_SYNTHETIC_OBSERVATION_COMPLETE")
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, Stop) and len(exc.args) == 1 else ""
        if reason not in STAGES:
            reason = "PROBE_TERMINATED_UNEXPECTEDLY"
        print(json.dumps({
            "audit":"R23D1B_SAFE_DIAGNOSTIC",
            "status":"STOP","stage":STAGES[reason],"reason":reason,
            "network_egress_firewall_verified":False,
            "production_changes_attempted":False,
            "docker_probe_resources_may_require_inspection":True,
            "no_credentials_in_probe":True,
        },indent=2), file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
