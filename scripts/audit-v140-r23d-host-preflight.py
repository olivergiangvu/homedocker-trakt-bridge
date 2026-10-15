#!/usr/bin/env python3
"""R23D-0: audit-only HomeDocker readiness for a distinct AIO GET-relay canary.

NO login, AIO GET, Docker changes, firewall modifications or secrets read.
No assumption that disabling Docker bridge masquerade blocks all egress.
This probes production+rollback invariants and potential port occupancy only.
"""
import json
import ipaddress
import os
import re
import socket
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request

RC6 = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
       "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")
AIO_IMAGE = "ghcr.io/viren070/aiostreams:latest"
R23B = "homedocker-v140-r23b-canary"
R23A = "homedocker-v140-integrated-canary"
R23B_NETWORK = "homedocker_v140_r23b_local"
R23D = "homedocker-v140-r23d-gateway-canary"
R23D_NETWORK = "homedocker_v140_r23d_local"
R23D_GATEWAY_PORT = 17145
RELAY_PORT = 17420
CANARY_GATEWAY_PORT = 17141

class Stop(Exception):
    pass

def command(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           check=False, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        raise Stop("docker_or_host_inspection_unavailable") from None
    if p.returncode:
        raise Stop("docker_or_host_inspection_unavailable")
    return p.stdout

def container(name):
    raw = json.loads(command("docker", "container", "inspect", name))
    if len(raw) != 1 or raw[0].get("Name") != "/" + name:
        raise Stop("container_identity_unexpected")
    return raw[0]

def network(name):
    raw = json.loads(command("docker", "network", "inspect", name))
    if len(raw) != 1 or raw[0].get("Name") != name:
        raise Stop("network_identity_unexpected")
    return raw[0]

def running_with_image(name, image):
    obj = container(name)
    if not obj.get("State", {}).get("Running"):
        raise Stop("required_production_or_comparator_stopped")
    if obj.get("Config", {}).get("Image") != image:
        raise Stop("required_image_reference_changed")
    return obj

def loopback_port_binding(obj, host, inner):
    ports = (obj.get("HostConfig") or {}).get("PortBindings") or {}
    return any(row.get("HostIp") == "127.0.0.1"
               and row.get("HostPort") == str(host)
               for row in ports.get(str(inner) + "/tcp") or [])

def available_bind_port(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False

def health(port):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(f"http://127.0.0.1:{port}/health",
                                 method="GET"), timeout=5) as resp:
            return resp.status
    except (HTTPError, URLError, OSError, TimeoutError):
        return None

def audit():
    if os.geteuid() != 0:
        raise Stop("sudo_required_for_readonly_host_inventory")
    running_with_image("trakt-bridge", RC6)
    aio = running_with_image("aiostreams", AIO_IMAGE)
    if not loopback_port_binding(aio, 3240, 3000):
        raise Stop("AIO_expected_loopback_3240_binding_missing")
    if not container("homedocker-resume-contract-canary").get("State", {}).get("Running"):
        raise Stop("resume_contract_comparator_unavailable")

    current = container(R23B)
    old = container(R23A)
    if not current.get("State", {}).get("Running") or old.get("State", {}).get("Running"):
        raise Stop("R23B_active_or_R23A_rollback_state_changed")
    # R23A was created with a tag; R23B explicitly used that immutable
    # image ID in 'docker run' (not the tag). Docker therefore legitimately
    # stores sha256:<id> in R23B Config.Image. Verify both the original
    # provenance and the exact shared immutable Image ID.
    source_tag = "homedocker-trakt-bridge:v140-r23a-e442ce461209"
    if old.get("Config", {}).get("Image") != source_tag:
        raise Stop("R23A_rollback_original_source_tag_changed")
    image_id = current.get("Image")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(image_id)):
        raise Stop("R23B_immutable_container_image_id_invalid")
    if old.get("Image") != image_id:
        raise Stop("R23A_rollback_image_different_from_R23B")
    if current.get("Config", {}).get("Image") not in (source_tag, image_id):
        raise Stop("R23B_image_reference_not_immutable_parent")
    if not current.get("HostConfig", {}).get("ReadonlyRootfs"):
        raise Stop("R23B_readonly_rootfs_missing")
    if current.get("HostConfig", {}).get("NetworkMode") != R23B_NETWORK:
        raise Stop("R23B_dedicated_network_changed")
    nets = current.get("NetworkSettings", {}).get("Networks") or {}
    if set(nets) != {R23B_NETWORK}:
        raise Stop("R23B_network_membership_changed")
    if any(m.get("Type") in ("bind", "volume") for m in current.get("Mounts") or []):
        raise Stop("R23B_persistent_mount_added")
    if not (loopback_port_binding(current, 17041, 7000)
            and loopback_port_binding(current, CANARY_GATEWAY_PORT, 7101)):
        raise Stop("R23B_loopback_binding_changed")
    net = network(R23B_NETWORK)
    if net.get("Internal") is not False or (net.get("Options") or {}).get(
        "com.docker.network.bridge.enable_ip_masquerade") != "false":
        raise Stop("R23B_network_assumptions_changed")
    if (health(17041), health(CANARY_GATEWAY_PORT)) != (200, 200):
        raise Stop("R23B_localhost_health_unavailable")

    all_names = command("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    if R23D in all_names:
        raise Stop("separate_R23D_canary_already_exists")
    if subprocess.run(["docker", "network", "inspect", R23D_NETWORK],
                      capture_output=True, timeout=15).returncode == 0:
        raise Stop("separate_R23D_network_already_exists")
    if not available_bind_port(R23D_GATEWAY_PORT) or not available_bind_port(RELAY_PORT):
        raise Stop("proposed_gateway_or_relay_loopback_port_occupied")

    for name in all_names:
        obj = current if name == R23B else container(name)
        bindings = (obj.get("HostConfig") or {}).get("PortBindings") or {}
        for entries in bindings.values():
            for entry in entries or []:
                if int(entry.get("HostPort") or 0) in (R23D_GATEWAY_PORT, RELAY_PORT):
                    raise Stop("proposed_gateway_or_relay_port_reserved_by_docker")
    return {
        "audit": "R23D0_READ_ONLY_HOST_TOPOLOGY",
        "status": "PREFLIGHT_PASS",
        "rc6_pinned_and_running": True,
        "aio_loopback_3240_expected": True,
        "r23b_loopback_17041_17141_http_200": True,
        "r23a_stopped_rollback_retained": True,
        "resume_comparator_running": True,
        "r23b_production_mounts_or_media_network_added": False,
        "proposed_r23d_loopback_port": R23D_GATEWAY_PORT,
        "proposed_host_relay_port": RELAY_PORT,
        "separate_r23d_container_and_network_absent": True,
        "aio_get_or_session_created": False,
        "network_egress_firewall_verified": False,
        "host_relay_running": False,
        "gateway_authenticated_live_normalization_proven": False,
        "production_modifications": 0,
    }

def main():
    try:
        print(json.dumps(audit(), indent=2))
        print("R23D0_READONLY_PREFLIGHT_PASS")
    except (Stop, ValueError, KeyError, TypeError, OSError):
        print("STOP: R23D0 host topology not qualified; no changes attempted",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
