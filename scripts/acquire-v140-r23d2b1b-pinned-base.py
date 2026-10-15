#!/usr/bin/env python3
"""R23D-2B1B narrowly confirmed, DIGEST-ONLY Docker Hub Node base acquisition.

By default performs a protected read-only source/host/cache preflight and NO
network access or writes. '--apply' requires exact separate confirmation and
may download ONLY the exact immutable node:24-alpine@sha256 reference already
pinned in Dockerfile.gateway-canary. No other tag, image build, container run,
network create, nft change, AIO login or media history request is supported.

The optional apply changes ONLY local Docker image cache (and a short-lived
empty Docker client config directory); Docker registry network traffic occurs.
A partial/failed pull can leave blobs in Docker cache. NO automatic image
delete/prune, and no production/rollback container restarts. The result is
NOT permission to activate the Gateway or a real Jellyfin session.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
READINESS=HERE/"audit-v140-r23d2b1-offline-runtime-readiness.py"
PINNED_BASE=("node:24-alpine@sha256:"
    "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1")
CONFIRM="R23D2B1B_PULL_EXACT_PINNED_NODE_BASE_ONLY"
DIGEST=PINNED_BASE.split("@",1)[1]
IMAGE_ID=re.compile(r"^sha256:[a-f0-9]{64}$")
ALLOWED_REPODIGESTS={
    "node@"+DIGEST,
    "docker.io/library/node@"+DIGEST,
    "library/node@"+DIGEST,
}

class Stop(Exception):
    pass

def load_readiness():
    if not READINESS.is_file() or READINESS.is_symlink():
        raise Stop("REVIEWED_READINESS_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location("r23d2b1b_host_readiness",READINESS)
    if spec is None or spec.loader is None:
        raise Stop("REVIEWED_READINESS_SOURCE_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def require_local_docker_socket():
    """Avoid pulling into a remote Docker context or unexpected daemon."""
    if os.environ.get("DOCKER_HOST","") not in ("","unix:///var/run/docker.sock"):
        raise Stop("REMOTE_DOCKER_HOST_DISALLOWED")
    if os.environ.get("DOCKER_CONTEXT","") not in ("","default"):
        raise Stop("REMOTE_DOCKER_HOST_DISALLOWED")
    if not Path("/var/run/docker.sock").is_socket():
        raise Stop("LOCAL_DOCKER_SOCKET_UNAVAILABLE")
    try:
        context=subprocess.run(["docker","context","show"],
             capture_output=True,timeout=12,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("LOCAL_DOCKER_CONTEXT_UNAVAILABLE") from None
    if context.returncode or context.stdout.strip()!=b"default":
        raise Stop("REMOTE_DOCKER_HOST_DISALLOWED")
    return True

def preflight(readiness):
    # Does not pull. Re-runs current RC6/R23B/R23A/AIO, source checkout,
    # final bridge names, nft syntax, dedicated ports and image collision.
    if os.geteuid()!=0:
        raise Stop("ROOT_REQUIRED_FOR_PROTECTED_HOST_PREFLIGHT")
    require_local_docker_socket()
    if PINNED_BASE != (
        "node:24-alpine@sha256:"
        "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1"
    ):
        raise Stop("SOURCE_IMAGE_DIGEST_CHANGED")
    planner=readiness.load_planner()
    if not planner.check_gateway_source():
        raise Stop("GATEWAY_ONLY_IMAGE_CONTRACT_CHANGED")
    src=planner.GATEWAY_IMAGE_FILE.read_text("utf8")
    pinned=readiness.BASE_RE.findall(src)
    if pinned!=[PINNED_BASE]:
        raise Stop("SOURCE_IMAGE_DIGEST_CHANGED")
    return readiness.preflight()

def image_inspect(reference):
    if reference!=PINNED_BASE:
        raise Stop("DISALLOWED_DOCKER_IMAGE_REFERENCE")
    try:
        p=subprocess.run(
            ["docker","image","inspect",reference],
            capture_output=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("POST_PULL_DOCKER_INSPECTION_FAILED") from None
    if p.returncode!=0 or len(p.stdout)>131072:
        raise Stop("POST_PULL_DOCKER_INSPECTION_FAILED")
    try:
        items=json.loads(p.stdout)
    except (ValueError,UnicodeError):
        raise Stop("POST_PULL_DOCKER_INSPECTION_FAILED") from None
    if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict):
        raise Stop("POST_PULL_DOCKER_INSPECTION_FAILED")
    item=items[0]
    if (not IMAGE_ID.fullmatch(str(item.get("Id")))
        or item.get("Os")!="linux"
        or not isinstance(item.get("RepoDigests"),list)
        or not any(d in ALLOWED_REPODIGESTS for d in item["RepoDigests"])):
        raise Stop("POST_PULL_DIGEST_OR_PLATFORM_NOT_VERIFIED")
    return True

def apply(readiness):
    # Per-run private empty Docker config avoids pulling registry credentials
    # or Docker credential helper settings from the operator's home folder.
    with tempfile.TemporaryDirectory(prefix="homedocker-r23d2b1b-registry-") as home:
        env=os.environ.copy()
        env["DOCKER_CONFIG"]=home
        try:
            run=subprocess.run(
                ["docker","pull","--quiet",PINNED_BASE],
                capture_output=True,timeout=240,check=False,env=env)
        except (OSError,subprocess.TimeoutExpired):
            raise Stop("EXACT_DIGEST_PULL_FAILED_RECHECK_IMAGE_CACHE_BEFORE_RETRY") from None
    if run.returncode!=0:
        raise Stop("EXACT_DIGEST_PULL_FAILED_RECHECK_IMAGE_CACHE_BEFORE_RETRY")
    if len(run.stdout)>131072 or len(run.stderr)>131072:
        raise Stop("EXACT_DIGEST_PULL_OUTPUT_UNEXPECTED")
    image_inspect(PINNED_BASE)
    if readiness.preflight() is not True:
        raise Stop("POST_PULL_PRODUCTION_OR_BASE_READINESS_FAILED")
    return {
        "audit":"R23D2B1B_EXACT_DIGEST_BASE_ACQUISITION",
        "status":"PINNED_BASE_CACHED_AND_PROTECTED_POSTFLIGHT_PASS",
        "exact_dockerfile_digest_only":True,
        "verified_repo_digest_and_linux_os":True,
        "base_cached_after_pull":True,
        "protected_rc6_r23b_r23a_postflight_passed":True,
        "docker_image_cache_modified":True,
        "external_docker_registry_network_used":True,
        "gateway_image_built":False,
        "canary_network_or_nft_firewall_created":False,
        "aio_jellyfin_session_allowed":False,
        "next":"IMPLEMENT_GUARDED_SYNTHETIC_FINAL_NETWORK_SUPERVISOR",
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--confirm",default="")
    args=parser.parse_args()
    try:
        readiness=load_readiness()
        cached=preflight(readiness)
        if not args.apply:
            print(json.dumps({
                "audit":"R23D2B1B_EXACT_DIGEST_ACQUISITION_DRY_RUN",
                "status":("ALREADY_CACHED_NO_PULL_NEEDED" if cached
                          else "READY_FOR_EXPLICIT_DIGEST_ONLY_PULL"),
                "previous_r23d2b1a_host_hold_rechecked":True,
                "source_pin_matches_reviewed_exact_digest":True,
                "protected_rc6_r23b_r23a_baseline_passed":True,
                "will_not_use_latest_tags_or_implicit_image_pull":True,
                "docker_registry_access_during_dry_run":False,
                "docker_image_cache_modified":False,
                "gateway_image_built":False,
                "canary_network_or_nft_firewall_created":False,
                "aio_jellyfin_session_allowed":False,
                "next":"EXPLICIT_SEPARATE_OPERATOR_APPROVAL_REQUIRED_FOR_PULL",
            },indent=2))
            print("R23D2B1B_PINNED_ACQUISITION_DRY_RUN_PASS")
            return
        if args.confirm!=CONFIRM:
            raise Stop("EXPLICIT_IMAGE_PULL_CONFIRMATION_REQUIRED")
        if cached:
            # A race can make the pinned base available; never pull again.
            image_inspect(PINNED_BASE)
            print(json.dumps({
                "audit":"R23D2B1B_EXACT_DIGEST_BASE_ACQUISITION",
                "status":"ALREADY_CACHED_VERIFIED_NO_PULL",
                "docker_image_cache_modified":False,
                "gateway_image_built":False,
                "canary_network_or_nft_firewall_created":False,
                "aio_jellyfin_session_allowed":False,
            },indent=2))
            print("R23D2B1B_PINNED_BASE_READY")
            return
        print(json.dumps(apply(readiness),indent=2))
        print("R23D2B1B_PINNED_BASE_READY")
    except Exception as exc:
        reason=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        allowed={
            "REVIEWED_READINESS_SOURCE_MISSING",
            "ROOT_REQUIRED_FOR_PROTECTED_HOST_PREFLIGHT",
            "REMOTE_DOCKER_HOST_DISALLOWED",
            "LOCAL_DOCKER_SOCKET_UNAVAILABLE",
            "LOCAL_DOCKER_CONTEXT_UNAVAILABLE",
            "SOURCE_IMAGE_DIGEST_CHANGED",
            "GATEWAY_ONLY_IMAGE_CONTRACT_CHANGED",
            "EXPLICIT_IMAGE_PULL_CONFIRMATION_REQUIRED",
            "DISALLOWED_DOCKER_IMAGE_REFERENCE",
            "POST_PULL_DOCKER_INSPECTION_FAILED",
            "POST_PULL_DIGEST_OR_PLATFORM_NOT_VERIFIED",
            "POST_PULL_PRODUCTION_OR_BASE_READINESS_FAILED",
            "EXACT_DIGEST_PULL_FAILED_RECHECK_IMAGE_CACHE_BEFORE_RETRY",
        }
        if reason not in allowed:
            reason="PROTECTED_PREFLIGHT_OR_UNCLASSIFIED_ACQUISITION_STOP"
        print(json.dumps({
            "audit":"R23D2B1B_SAFE_DIAGNOSTIC",
            "status":"STOP","reason":reason,
            "docker_image_cache_may_have_changed":args.apply,
            "canary_network_or_nft_firewall_created":False,
            "aio_jellyfin_session_allowed":False,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
