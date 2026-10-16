#!/usr/bin/env python3
"""R23D-2B1A host runtime readiness and failure-order model, READ-ONLY.

Source and host checks only; no apply mode, no Docker build/pull/create/run,
nft add/delete, filesystem write, Jellyfin GET, login, credential or history.
Requires R23D2B0 protected clean-checkout preflight again. This audit detects
whether the specifically pinned base image is available offline, whether a
new dedicated image tag is free, and whether synthetic host control ports
are free. A missing local base image is *not* permission to pull it.

It models ordering/rollback constraints for the later R23D2B1 test but
DOES NOT claim any actual supervised kernel guard is installed or verified.
Output never includes host addresses, image digests, source revisions, paths,
tokens, real user IDs, JSON rules or Docker identifiers.
"""
import importlib.util
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

HERE=Path(__file__).resolve().parent
PARENT=HERE/"plan-v140-r23d2b-gateway-offline.py"
BASE_RE=re.compile(r"(?m)^FROM\s+(node:24-alpine@sha256:[a-f0-9]{64})\s*$")
IMAGE_DIGEST_RE=re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_OUT=131072
SYNTHETIC_HOST_PORTS=(17145,17420,17421)
STAGES=(
    "PROTECTED_BASELINE",
    "PINNED_IMAGE_BUILT",
    "OWNED_BRIDGE_CREATED",
    "NFT_GUARD_INSTALLED_AND_ATTESTED",
    "FAKE_TCP_CONTROLS_PASS",
    "FAKE_GATEWAY_CANARY_STARTED",
    "SAME_NETWORK_DOCKER_AND_NFT_ATTESTED",
    "FAKE_GATEWAY_HEALTH_ONLY",
    "CANARY_STOPPED_AND_REMOVED",
    "OWNED_NETWORK_REMOVED",
    "OWNED_NFT_TABLE_REMOVED",
    "PRODUCTION_POSTFLIGHT_PASS",
)

class Stop(Exception):
    pass

def load_planner():
    if not PARENT.is_file() or PARENT.is_symlink():
        raise Stop("R23D2B0_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location("r23d2b1_existing_host_planner",PARENT)
    if spec is None or spec.loader is None:
        raise Stop("R23D2B0_SOURCE_MISSING")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def inspect_image(reference):
    # Do not download unknown image bases. Distinguish missing from daemon
    # failure by checking the Docker daemon with a separate read-only call.
    if not isinstance(reference,str) or len(reference)>200 or not (
        BASE_RE.fullmatch("FROM "+reference) or
        re.fullmatch(r"homedocker-trakt-bridge:v140-r23d-[a-f0-9]{12}",reference)
    ):
        raise Stop("IMAGE_REFERENCE_UNEXPECTED")
    try:
        p=subprocess.run(["docker","image","inspect",reference],
                         capture_output=True,timeout=12,check=False)
        daemon=subprocess.run(["docker","info","--format","{{.ServerVersion}}"],
                              capture_output=True,timeout=12,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("DOCKER_INSPECTION_UNAVAILABLE") from None
    if daemon.returncode or len(daemon.stdout)>MAX_OUT or p.returncode not in (0,1):
        raise Stop("DOCKER_INSPECTION_UNAVAILABLE")
    if p.returncode == 1:
        return None
    if len(p.stdout)>MAX_OUT:
        raise Stop("DOCKER_INSPECTION_OUTPUT_TOO_LARGE")
    try:
        items=json.loads(p.stdout)
    except (TypeError,ValueError,UnicodeError):
        raise Stop("IMAGE_INSPECTION_MALFORMED") from None
    if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict):
        raise Stop("IMAGE_INSPECTION_MALFORMED")
    if not IMAGE_DIGEST_RE.fullmatch(str(items[0].get("Id"))):
        raise Stop("IMAGE_INSPECTION_ID_INVALID")
    return items[0]["Id"]

def check_ports():
    """Try temporary local binds; do NOT listen, serve or reserve host ports."""
    for port in SYNTHETIC_HOST_PORTS:
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1",port))
        except OSError:
            raise Stop("SYNTHETIC_PORT_ALREADY_IN_USE") from None
    return True

def validate_lifecycle(steps):
    """Statically assert the only acceptable future execution ordering.

    No stage here interacts with Docker, host nft or actual runtime. Failure
    analysis must leave nft guard when any canary/network cleanup is uncertain.
    """
    if not isinstance(steps,tuple) or steps!=STAGES:
        raise Stop("LIFECYCLE_SEQUENCE_INVALID")
    position={name:i for i,name in enumerate(steps)}
    if not (position["OWNED_BRIDGE_CREATED"]
            < position["NFT_GUARD_INSTALLED_AND_ATTESTED"]
            < position["FAKE_TCP_CONTROLS_PASS"]
            < position["FAKE_GATEWAY_CANARY_STARTED"]
            < position["CANARY_STOPPED_AND_REMOVED"]
            < position["OWNED_NETWORK_REMOVED"]
            < position["OWNED_NFT_TABLE_REMOVED"]):
        raise Stop("GUARD_ORDER_INVALID")
    return True

def allowed_cleanup(*,canary_absent,network_absent,guard_unchanged):
    """Which artifact, if any, is allowed to be deleted NEXT (pure model)."""
    if any(type(x) is not bool for x in
           (canary_absent,network_absent,guard_unchanged)):
        raise Stop("ROLLBACK_EVIDENCE_MUST_BE_BOOLEAN")
    if not canary_absent:
        return "RETAIN_GUARD_AND_NETWORK_INSPECT_CANARY"
    if not network_absent:
        return "RETAIN_GUARD_REMOVE_ONLY_ATTESTED_EMPTY_NETWORK"
    if not guard_unchanged:
        return "RETAIN_GUARD_MANUAL_OWNERSHIP_REVIEW"
    return "REMOVE_ONLY_UNCHANGED_OWNED_GUARD_LAST"

def preflight():
    planner=load_planner()
    plan=planner.preflight()
    doc=planner.GATEWAY_IMAGE_FILE.read_text("utf8")
    matches=BASE_RE.findall(doc)
    if len(matches)!=1:
        raise Stop("GATEWAY_BASE_IMAGE_PIN_NOT_FOUND")
    cached=inspect_image(matches[0])
    if inspect_image(plan["image_tag"]) is not None:
        raise Stop("PINNED_FINAL_IMAGE_TAG_ALREADY_PRESENT")
    if not check_ports():
        raise Stop("LOCAL_TCP_CONTROL_PORT_CHECK_FAILED")
    if not validate_lifecycle(STAGES):
        raise Stop("LIFECYCLE_SEQUENCE_INVALID")
    # A cached tagged base is optional at this stage. If absent STOP short
    # of a build, never pull implicitly from an unattended deployment.
    return cached is not None

REASONS={
    "R23D2B0_SOURCE_MISSING",
    "IMAGE_REFERENCE_UNEXPECTED",
    "DOCKER_INSPECTION_UNAVAILABLE",
    "DOCKER_INSPECTION_OUTPUT_TOO_LARGE",
    "IMAGE_INSPECTION_MALFORMED",
    "IMAGE_INSPECTION_ID_INVALID",
    "SYNTHETIC_PORT_ALREADY_IN_USE",
    "ROLLBACK_EVIDENCE_MUST_BE_BOOLEAN",
    "LIFECYCLE_SEQUENCE_INVALID",
    "GUARD_ORDER_INVALID",
    "GATEWAY_BASE_IMAGE_PIN_NOT_FOUND",
    "PINNED_FINAL_IMAGE_TAG_ALREADY_PRESENT",
    "LOCAL_TCP_CONTROL_PORT_CHECK_FAILED",
}
def main():
    try:
        base_present=preflight()
        print(json.dumps({
          "audit":"R23D2B1A_READONLY_OFFLINE_RUNTIME_READINESS",
          "status":"READY_TO_DESIGN_SUPERVISED_TRIAL" if base_present
                   else "PINNED_BASE_IMAGE_NOT_CACHED_HOLD_BUILD",
          "protected_rc6_r23b_r23a_baseline_passed":True,
          "r23d2b0_final_network_subnet_and_nft_syntax_revalidated":True,
          "pinned_node_base_locally_available":base_present,
          "future_canary_image_tag_unused":True,
          "health_and_positive_negative_control_ports_unused":True,
          "fake_only_network_guard_startup_and_rollback_order_reviewed":True,
          "kernel_supervisor_running":False,
          "runtime_docker_nft_attestation_performed":False,
          "gateway_image_built_or_pulled":False,
          "final_network_container_or_firewall_modified":False,
          "real_aio_jellyfin_session_allowed":False,
          "docker_nginx_aio_trakt_history_writes":0,
          "next":"REVIEW_PINNED_IMAGE_CACHE_AND_IMPLEMENT_NO_TOKEN_GUARDED_LIFECYCLE",
        },indent=2))
        print("R23D2B1A_READONLY_RUNTIME_READINESS_COMPLETE")
    except Exception as err:
        reason=err.args[0] if isinstance(err,Stop) and len(err.args)==1 else ""
        if reason not in REASONS:
            reason="UNCLASSIFIED_READONLY_CHECK_FAILED"
        print(json.dumps({
          "audit":"R23D2B1A_SAFE_DIAGNOSTIC",
          "status":"STOP","reason":reason,
          "final_network_container_or_firewall_modified":False,
          "real_aio_jellyfin_session_allowed":False,
          "docker_nginx_aio_trakt_history_writes":0,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
