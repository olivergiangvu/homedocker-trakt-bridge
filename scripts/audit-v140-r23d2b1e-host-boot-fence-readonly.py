#!/usr/bin/env python3
"""R23D-2B1E: inspect-only boot fencing prerequisites; NEVER authorize apply.

Reattests existing immutable R23D-2B1C image and RC6/R23B/R23A baseline,
then checks host systemd and Docker-owned resource absence. No files written,
no docker run/pull/build, no nft install, no systemctl mutations, no
authenticated AIO/Trakt calls. The existing protected baseline may check
loopback /health endpoints. A successful audit is a HOLD, never a green-light
for a canary: independent boot fencing is NOT YET IMPLEMENTED.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BUILDER = HERE / "build-v140-r23d2b1c-gateway-only.py"
PHASE_LABEL = "homedocker.phase=r23d-persistent-canary"
UNITS = ("homedocker-r23d-guardian.service", "homedocker-r23d-gateway-canary.service")
MAX_BYTES = 131072

class Stop(Exception):
    pass

def read_only_command(args):
    # Exact argv allowlist; no shell, expansion, writes or caller-chosen flags.
    commands = {
        ("docker", "info", "--format", "{{.ServerVersion}}"),
        ("docker", "ps", "-a", "--filter", "label=" + PHASE_LABEL,
         "--format", "{{.ID}}"),
        ("systemctl", "show", "docker.service", "--property=LoadState",
         "--property=ActiveState", "--no-pager"),
    }
    commands.update(("systemctl", "show", name, "--property=LoadState", "--no-pager")
                    for name in UNITS)
    if tuple(args) not in commands:
        raise Stop("READ_ONLY_COMMAND_NOT_ALLOWLISTED")
    try:
        p = subprocess.run(args, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("HOST_INSPECTION_UNAVAILABLE") from None
    if p.returncode or len(p.stdout) > MAX_BYTES or len(p.stderr) > MAX_BYTES:
        raise Stop("HOST_INSPECTION_UNAVAILABLE")
    try:
        return p.stdout.decode("utf-8", "strict")
    except UnicodeError:
        raise Stop("HOST_INSPECTION_UNAVAILABLE") from None

def parse_properties(output, allowed):
    if not isinstance(output, str) or not output or len(output) > 8192:
        raise Stop("SYSTEMD_PROPERTY_FORMAT_REJECTED")
    result = {}
    for line in output.splitlines():
        if line.count("=") != 1:
            raise Stop("SYSTEMD_PROPERTY_FORMAT_REJECTED")
        key, value = line.split("=", 1)
        if key not in allowed or key in result or not value:
            raise Stop("SYSTEMD_PROPERTY_FORMAT_REJECTED")
        result[key] = value
    if set(result) != set(allowed):
        raise Stop("SYSTEMD_PROPERTY_FORMAT_REJECTED")
    return result

def evaluate_host_fence(*, image_attested, docker_active, tagged_peers_absent,
                        guardian_unit_absent, canary_unit_absent):
    vals = (image_attested, docker_active, tagged_peers_absent,
            guardian_unit_absent, canary_unit_absent)
    if any(type(v) is not bool for v in vals):
        raise Stop("BOOLEAN_EVIDENCE_INVALID")
    if not image_attested:
        raise Stop("EXACT_IMAGE_ATTESTATION_MISSING")
    if not docker_active:
        raise Stop("DOCKER_SERVICE_NOT_ACTIVE")
    if not tagged_peers_absent:
        raise Stop("UNEXPECTED_R23D_LABELED_CONTAINER")
    if not guardian_unit_absent or not canary_unit_absent:
        raise Stop("R23D_UNIT_COLLISION_REQUIRES_OWNERSHIP_REVIEW")
    return {
        "audit": "R23D2B1E_HOST_BOOT_FENCE_READONLY",
        "status": "PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD",
        "existing_r23d_gateway_image_attested": True,
        "rc6_r23b_r23a_protected_baseline_passed": True,
        "docker_service_active": True,
        "r23d_labeled_container_absent": True,
        "r23d_guardian_and_canary_units_absent": True,
        "independent_host_fence_installed": False,
        "boot_order_enforcement_proven": False,
        "reboot_crash_fail_closed_proven": False,
        "kernel_guard_installed": False,
        "docker_nft_systemd_or_production_modified": False,
        "authenticated_aio_relay_allowed": False,
        "next": "DESIGN_INDEPENDENT_SYSTEMD_FENCE_NO_LIVE_R23D_APPLY",
    }

def attest_existing():
    if not BUILDER.is_file() or BUILDER.is_symlink():
        raise Stop("REVIEWED_GATEWAY_VERIFIER_MISSING")
    spec = importlib.util.spec_from_file_location("r23d2b1e_existing_verifier", BUILDER)
    if spec is None or spec.loader is None:
        raise Stop("REVIEWED_GATEWAY_VERIFIER_MISSING")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.verify_existing().get("status") == "EXISTING_IMAGE_ATTESTED_PROTECTED_BASELINE_PASS"

def audit():
    if os.geteuid() != 0:
        raise Stop("ROOT_REQUIRED_FOR_READONLY_HOST_AUDIT")
    if not attest_existing():
        raise Stop("EXACT_IMAGE_ATTESTATION_MISSING")
    if not read_only_command(["docker", "info", "--format", "{{.ServerVersion}}"]).strip():
        raise Stop("DOCKER_DAEMON_NOT_AVAILABLE")
    docker = parse_properties(read_only_command([
        "systemctl", "show", "docker.service", "--property=LoadState",
        "--property=ActiveState", "--no-pager"]), {"LoadState", "ActiveState"})
    unit_status = []
    for unit in UNITS:
        props = parse_properties(read_only_command([
            "systemctl", "show", unit, "--property=LoadState", "--no-pager"]), {"LoadState"})
        unit_status.append(props["LoadState"] == "not-found")
    rows = read_only_command(["docker", "ps", "-a", "--filter",
                              "label=" + PHASE_LABEL, "--format", "{{.ID}}"])
    if rows.strip() and (len(rows) > 4096 or any(len(row)>70 for row in rows.splitlines())):
        raise Stop("DOCKER_LABEL_QUERY_UNEXPECTED")
    return evaluate_host_fence(
        image_attested=True,
        docker_active=docker == {"LoadState": "loaded", "ActiveState": "active"},
        tagged_peers_absent=not rows.strip(),
        guardian_unit_absent=unit_status[0],
        canary_unit_absent=unit_status[1])

REASONS = {
    "READ_ONLY_COMMAND_NOT_ALLOWLISTED", "HOST_INSPECTION_UNAVAILABLE",
    "SYSTEMD_PROPERTY_FORMAT_REJECTED", "BOOLEAN_EVIDENCE_INVALID",
    "EXACT_IMAGE_ATTESTATION_MISSING", "DOCKER_SERVICE_NOT_ACTIVE",
    "UNEXPECTED_R23D_LABELED_CONTAINER",
    "R23D_UNIT_COLLISION_REQUIRES_OWNERSHIP_REVIEW",
    "REVIEWED_GATEWAY_VERIFIER_MISSING", "ROOT_REQUIRED_FOR_READONLY_HOST_AUDIT",
    "DOCKER_DAEMON_NOT_AVAILABLE",
}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit-only", action="store_true")
    args = p.parse_args()
    if not args.audit_only:
        p.error("ONLY_AUDIT_ONLY_SUPPORTED")
    try:
        print(json.dumps(audit(), indent=2))
        print("R23D2B1E_READONLY_HOST_AUDIT_COMPLETE_BOOT_FENCE_HOLD")
    except Exception as e:
        reason = e.args[0] if isinstance(e, Stop) and len(e.args)==1 else ""
        if reason not in REASONS:
            reason = "PROTECTED_HOST_OR_UNCLASSIFIED_READONLY_STOP"
        print(json.dumps({
            "audit": "R23D2B1E_READONLY_SAFE_DIAGNOSTIC", "status": "STOP",
            "reason": reason, "host_changes": False,
            "authenticated_aio_relay_allowed": False,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
