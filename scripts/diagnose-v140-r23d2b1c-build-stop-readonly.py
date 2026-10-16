#!/usr/bin/env python3
"""R23D-2B1C build STOP recovery. NO-WRITE, NO REBUILD, NO PRUNE.

Diagnoses the exact source-pinned 3c08d06bf081 R23D2B1C tag following an
unclassified --apply STOP. Never prints raw Docker inspect, Env, error logs,
host addresses, image IDs, paths, tokens or SHA. Does not use failed
builder.preflight() (which deliberately REJECTS a present target image tag).
Only read-only protected production baseline + local Docker and nft inventory
+ exact image-config attestation from the original reviewed builder.

Run from a new pinned, clean, trusted detached checkout with sudo. There is
no --apply/--fix path and no permission to rebuild or use real AIO sessions.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
BUILD=HERE/"build-v140-r23d2b1c-gateway-only.py"
ACQUIRE=HERE/"acquire-v140-r23d2b1b-pinned-base.py"
PLAN=HERE/"plan-v140-r23d2b-gateway-offline.py"
BASELINE=HERE/"audit-v140-r23d1f-firewall-plan-readonly.py"
ORIGINAL_SHA="3c08d06bf08154283d5b548d371729436f9ea392"
ORIGINAL_TAG="homedocker-trakt-bridge:v140-r23d-"+ORIGINAL_SHA[:12]
EXPECTED_NETWORK="homedocker_v140_r23d_local"
EXPECTED_CANARY="homedocker-v140-r23d-gateway-canary"
EXPECTED_INTERFACE="br-r23dgate"
GUARD_TABLE="homedocker_r23d_guard"
MAX_BYTES=196608
VALID_CODES={
  "GATEWAY_BUILT_IMAGE_IDENTITY_REJECTED",
  "GATEWAY_BUILT_IMAGE_TAG_REJECTED",
  "GATEWAY_IMAGE_CONFIG_REJECTED",
  "GATEWAY_ENV_CONFIG_REJECTED",
  "GATEWAY_ENV_DUPLICATE_KEYS",
  "GATEWAY_ENV_SECRET_CONFIG_REJECTED",
  "GATEWAY_HEALTHCHECK_REJECTED",
}

class Stop(Exception):
    pass

def load(path, name):
    if not path.is_file() or path.is_symlink():
        raise Stop("TRUSTED_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise Stop("TRUSTED_SOURCE_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def command(args):
    """Fixed read-only executable arguments; never print stderr."""
    try:
        p=subprocess.run(args,capture_output=True,timeout=22,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("READONLY_HOST_INSPECTION_UNAVAILABLE") from None
    if len(p.stdout)>MAX_BYTES or len(p.stderr)>MAX_BYTES:
        raise Stop("READONLY_HOST_INSPECTION_OVERSIZED")
    return p

def json_command(args):
    p=command(args)
    if p.returncode:
        raise Stop("READONLY_HOST_INSPECTION_UNAVAILABLE")
    try:
        return json.loads(p.stdout)
    except (ValueError,UnicodeError):
        raise Stop("READONLY_HOST_INSPECTION_JSON_INVALID") from None

def inspect_image_exact():
    """Distinguish absent tag only with independent local tag inventory."""
    listing=command(["docker","image","ls","--format","{{.Repository}}:{{.Tag}}"])
    if listing.returncode:
        raise Stop("DOCKER_IMAGE_TAG_INVENTORY_UNAVAILABLE")
    inventory=listing.stdout.decode("utf8","strict").splitlines()
    if ORIGINAL_TAG not in inventory:
        return None
    detail=json_command(["docker","image","inspect",ORIGINAL_TAG])
    if not isinstance(detail,list) or len(detail)!=1 or not isinstance(detail[0],dict):
        raise Stop("DOCKER_IMAGE_INSPECT_MALFORMED")
    return detail[0]

def matches_no_ephemeral():
    names=command(["docker","ps","-a","--format","{{.Names}}"])
    nets=command(["docker","network","ls","--format","{{.Name}}"])
    if names.returncode or nets.returncode:
        raise Stop("DOCKER_RESOURCE_INVENTORY_UNAVAILABLE")
    if EXPECTED_CANARY in names.stdout.decode("utf8","strict").splitlines():
        raise Stop("R23D2B1C_UNEXPECTED_FINAL_CANARY_EXISTS")
    if EXPECTED_NETWORK in nets.stdout.decode("utf8","strict").splitlines():
        raise Stop("R23D2B1C_UNEXPECTED_FINAL_NETWORK_EXISTS")
    links=json_command(["ip","-j","link","show"])
    if not isinstance(links,list):
        raise Stop("HOST_LINK_INVENTORY_MALFORMED")
    if EXPECTED_INTERFACE in [v.get("ifname") for v in links if isinstance(v,dict)]:
        raise Stop("R23D2B1C_UNEXPECTED_FINAL_BRIDGE_EXISTS")
    tables=json_command(["nft","-j","list","tables"])
    entries=tables.get("nftables") if isinstance(tables,dict) else None
    if not isinstance(entries,list):
        raise Stop("NFT_INVENTORY_MALFORMED")
    if any(isinstance(v,dict) and isinstance(v.get("table"),dict)
           and v["table"].get("name")==GUARD_TABLE
           and v["table"].get("family")=="inet" for v in entries):
        raise Stop("R23D2B1C_UNEXPECTED_NFT_TABLE_EXISTS")
    return True

def classify_image(image,original_builder,base_arch):
    try:
        accepted=original_builder.attest_built_image(
            image,tag=ORIGINAL_TAG,sha=ORIGINAL_SHA,base_arch=base_arch)
    except Exception as e:
        code=e.args[0] if len(e.args)==1 else None
        return code if code in VALID_CODES else "IMAGE_ATTESTATION_EXCEPTION_REDACTED"
    return "ATTESTATION_PASS" if accepted is True else "IMAGE_ATTESTATION_UNKNOWN"

def preflight():
    if os.geteuid()!=0:
        raise Stop("SUDO_REQUIRED")
    acquired=load(ACQUIRE,"r23d2b1c_triage_base_source")
    acquired.require_local_docker_socket()
    planner=load(PLAN,"r23d2b1c_triage_planner")
    protected=load(BASELINE,"r23d2b1c_triage_protected_host")
    original_builder=load(BUILD,"r23d2b1c_triage_original_attestor")
    # Existing R23D0/1F baseline checks production and old rollback but does
    # not require the R23D new target image tag to be absent.
    protected.baseline()
    matches_no_ephemeral()
    acquired.image_inspect(acquired.PINNED_BASE)
    base=json_command(["docker","image","inspect",acquired.PINNED_BASE])
    if not isinstance(base,list) or len(base)!=1 or not isinstance(base[0],dict):
        raise Stop("CACHED_BASE_INSPECT_INVALID")
    base_arch=base[0].get("Architecture")
    if base_arch not in ("amd64","arm64"):
        raise Stop("CACHED_BASE_ARCH_UNEXPECTED")
    image=inspect_image_exact()
    if image is None:
        return {"tag":"ABSENT","attestation":"NOT_APPLICABLE"}
    return {"tag":"PRESENT",
            "attestation":classify_image(image,original_builder,base_arch)}

def main():
    try:
        state=preflight()
        present=state["tag"]=="PRESENT"
        good=state["attestation"]=="ATTESTATION_PASS"
        print(json.dumps({
            "audit":"R23D2B1C_BUILD_STOP_READONLY_TRIAGE",
            "status":("IMAGE_TAG_PRESENT_ATTESTED_NO_RUNTIME" if good else
                      "IMAGE_TAG_PRESENT_REQUIRES_REVIEW" if present else
                      "IMAGE_TAG_ABSENT_BUILD_NOT_CONFIRMED"),
            "old_source_image_tag_present":present,
            "old_source_image_attestation_passed":good,
            "old_source_image_attestation_result":state["attestation"],
            "protected_rc6_r23b_r23a_baseline_passed":True,
            "new_canary_bridge_network_nft_table_absent":True,
            "pinned_node_base_repo_digest_verified":True,
            "docker_image_build_or_prune_performed":False,
            "docker_firewall_nginx_aio_history_writes":0,
            "authenticated_jellyfin_session_allowed":False,
            "next":"REVIEW_IMAGE_STATE_BEFORE_ANY_NEW_BUILD",
        },indent=2))
        print("R23D2B1C_READONLY_TRIAGE_COMPLETE")
    except Exception as error:
        code=error.args[0] if isinstance(error,Stop) and len(error.args)==1 else ""
        reasons={
            "TRUSTED_SOURCE_MISSING","READONLY_HOST_INSPECTION_UNAVAILABLE",
            "READONLY_HOST_INSPECTION_OVERSIZED",
            "READONLY_HOST_INSPECTION_JSON_INVALID",
            "DOCKER_IMAGE_TAG_INVENTORY_UNAVAILABLE",
            "DOCKER_IMAGE_INSPECT_MALFORMED",
            "DOCKER_RESOURCE_INVENTORY_UNAVAILABLE",
            "R23D2B1C_UNEXPECTED_FINAL_CANARY_EXISTS",
            "R23D2B1C_UNEXPECTED_FINAL_NETWORK_EXISTS",
            "R23D2B1C_UNEXPECTED_FINAL_BRIDGE_EXISTS",
            "R23D2B1C_UNEXPECTED_NFT_TABLE_EXISTS",
            "HOST_LINK_INVENTORY_MALFORMED","NFT_INVENTORY_MALFORMED",
            "SUDO_REQUIRED","CACHED_BASE_INSPECT_INVALID",
            "CACHED_BASE_ARCH_UNEXPECTED",
        }
        if code not in reasons:
            code="PROTECTED_BASELINE_OR_UNCLASSIFIED_INSPECTION_STOP"
        print(json.dumps({
            "audit":"R23D2B1C_READONLY_TRIAGE_SAFE_DIAGNOSTIC",
            "status":"STOP","reason":code,
            "docker_image_build_or_prune_performed":False,
            "authenticated_jellyfin_session_allowed":False,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
