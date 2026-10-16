#!/usr/bin/env python3
"""R23D-2B1C: build ONE source-pinned Gateway-only Docker image, zero sessions.

DEFAULT DRY RUN. Only separately confirmed --apply can invoke a local Docker
build FROM the already-cached exact Node 24 Alpine digest. The build uses
--pull=false and --network=none, a private empty Docker client config, and
the dedicated deny-by-default Dockerfile.gateway-canary.dockerignore context.
It never runs a live container, creates a Docker network/nft table, mounts a
Bridge database, accesses AIO, changes nginx or restarts production.

A confirmed build adds a new local Docker image/tag/layers only; BuildKit may
use temporary build execution artifacts and registry metadata checks depending
on Docker version. Do not assume absolute network isolation from --network=none
alone (that controls build RUN network, not all base-image resolution).
The verified pinned base cache and --pull=false constrain resolution.

Failures may leave cached layers or the new image tag: NEVER silently delete,
prune or retry until exact source/ownership inspected. A ready Gateway image
is NOT a running canary and does NOT authorize authenticated Jellyfin sessions.
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
ROOT=HERE.parent
READY=HERE/"audit-v140-r23d2b1-offline-runtime-readiness.py"
ACQUIRE=HERE/"acquire-v140-r23d2b1b-pinned-base.py"
IGNORE=ROOT/"Dockerfile.gateway-canary.dockerignore"
ROLE_LABEL="io.homedocker.r23d.role"
SOURCE_LABEL="io.homedocker.r23d.source_sha"
CONFIRM="R23D2B1C_BUILD_CACHED_GATEWAY_ONLY_IMAGE"
EXISTING_SHA="3c08d06bf08154283d5b548d371729436f9ea392"
EXISTING_IMAGE_ID="sha256:98b1e6dc7c235ba7381cd6f798f742a5ecc460580c2f408e8d256927199f300d"
MAX_BYTES=131072
IMAGE_ID=re.compile(r"sha256:[0-9a-f]{64}")
LABEL_SOURCE_SHA=re.compile(r"[0-9a-f]{40}")
EXPECTED_CONTEXT=["**","!Dockerfile.gateway-canary","!package.json",
                  "!src/","!src/**"]

class Stop(Exception):
    pass

def load(path,name):
    if not path.is_file() or path.is_symlink():
        raise Stop("REVIEWED_SOURCE_MODULE_MISSING")
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise Stop("REVIEWED_SOURCE_MODULE_MISSING")
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def check_deny_by_default_context():
    if not IGNORE.is_file() or IGNORE.is_symlink() or IGNORE.stat().st_size>4096:
        raise Stop("ISOLATED_DOCKER_CONTEXT_NOT_VERIFIED")
    lines=[line.strip() for line in IGNORE.read_text("utf8").splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    if lines!=EXPECTED_CONTEXT:
        raise Stop("ISOLATED_DOCKER_CONTEXT_NOT_VERIFIED")
    return True

def command(argv, *, env=None, timeout=20):
    try:
        result=subprocess.run(argv,capture_output=True,check=False,
                              timeout=timeout,env=env)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("DOCKER_COMMAND_FAILED_OR_TIMED_OUT") from None
    if (result.returncode or len(result.stdout)>MAX_BYTES
        or len(result.stderr)>MAX_BYTES):
        raise Stop("DOCKER_COMMAND_FAILED_OR_OVERSIZED")
    return result.stdout

def inspected_image(ref):
    if not isinstance(ref,str) or not (
        ref.startswith("homedocker-trakt-bridge:v140-r23d-")
        and re.fullmatch(r"homedocker-trakt-bridge:v140-r23d-[0-9a-f]{12}",ref)
        or ref.startswith("node:24-alpine@sha256:")
        and re.fullmatch(r"node:24-alpine@sha256:[0-9a-f]{64}",ref)
    ):
        raise Stop("UNEXPECTED_IMAGE_INSPECTION_REFERENCE")
    raw=command(["docker","image","inspect",ref])
    try:
        data=json.loads(raw)
    except (ValueError,TypeError,UnicodeError):
        raise Stop("DOCKER_IMAGE_INSPECT_REJECTED") from None
    if (not isinstance(data,list) or len(data)!=1
        or not isinstance(data[0],dict)
        or not IMAGE_ID.fullmatch(str(data[0].get("Id")))
        or data[0].get("Os")!="linux"
        or data[0].get("Architecture") not in ("amd64","arm64")):
        raise Stop("DOCKER_IMAGE_INSPECT_REJECTED")
    return data[0]

def preflight():
    readiness=load(READY,"r23d2b1c_protected_readiness")
    acquisition=load(ACQUIRE,"r23d2b1c_digest_provenance")
    if not acquisition.preflight(readiness):
        raise Stop("PINNED_BASE_NOT_CACHED_HOLD_BUILD")
    if not acquisition.image_inspect(acquisition.PINNED_BASE):
        raise Stop("PINNED_BASE_DIGEST_UNVERIFIED")
    check_deny_by_default_context()
    planner=readiness.load_planner()
    plan=planner.preflight()
    sha=planner.source_identity()
    if not LABEL_SOURCE_SHA.fullmatch(sha):
        raise Stop("IMMUTABLE_SOURCE_SHA_REJECTED")
    tag="homedocker-trakt-bridge:v140-r23d-"+sha[:12]
    expected_build=["docker","build","--pull=false","--network=none",
                    "-f",str(ROOT/"Dockerfile.gateway-canary"),
                    "-t",tag,str(ROOT)]
    if plan.get("image_tag")!=tag or plan.get("commands",{}).get("docker_build")!=expected_build:
        raise Stop("REVIEWED_BUILD_CONTRACT_CHANGED")
    base=inspected_image(acquisition.PINNED_BASE)
    return {"base_arch":base["Architecture"],"sha":sha,"tag":tag,
            "build_command":expected_build,"planner":planner}

def attest_built_image(image, *, tag, sha, base_arch):
    if not isinstance(image,dict) or not LABEL_SOURCE_SHA.fullmatch(sha) \
       or not IMAGE_ID.fullmatch(str(image.get("Id"))) \
       or image.get("Os")!="linux" or image.get("Architecture")!=base_arch:
        raise Stop("GATEWAY_BUILT_IMAGE_IDENTITY_REJECTED")
    if tag not in (image.get("RepoTags") or []):
        raise Stop("GATEWAY_BUILT_IMAGE_TAG_REJECTED")
    c=image.get("Config") or {}
    labels=c.get("Labels") or {}
    if (not isinstance(labels,dict)
        or labels.get(ROLE_LABEL)!="gateway-only"
        or labels.get(SOURCE_LABEL)!=sha
        or c.get("User")!="node"
        or c.get("WorkingDir")!="/app"
        or c.get("Entrypoint")!=["docker-entrypoint.sh"]
        or c.get("Cmd")!=["node","src/gateway-canary-entry.mjs"]
        or c.get("Volumes") not in (None,{})
        or c.get("ExposedPorts")!={"7101/tcp":{}}):
        raise Stop("GATEWAY_IMAGE_CONFIG_REJECTED")
    env=c.get("Env")
    if not isinstance(env,list) or len(env)>48 or any(
            not isinstance(v,str) or "=" not in v or len(v)>512 for v in env):
        raise Stop("GATEWAY_ENV_CONFIG_REJECTED")
    items=[v.split("=",1) for v in env]
    values={k:v for k,v in items}
    if len(values)!=len(items):
        raise Stop("GATEWAY_ENV_DUPLICATE_KEYS")
    for k,v in {
        "NODE_ENV":"production",
        "JELLYFIN_GATEWAY_PORT":"7101",
        "GATEWAY_PROOF_FILE":"/run/identity-proof.json",
    }.items():
        if values.get(k)!=v:
            raise Stop("GATEWAY_ENV_CONFIG_REJECTED")
    if any(k.startswith(("TRAKT_","BRIDGE_SECRET","ADMIN_KEY",
                         "X_EMBY","AIO_API_KEY")) for k in values):
        raise Stop("GATEWAY_ENV_SECRET_CONFIG_REJECTED")
    hc=(c.get("Healthcheck") or {}).get("Test")
    if hc!=["CMD-SHELL",
             "wget -q -O /dev/null http://127.0.0.1:7101/health || exit 1"]:
        raise Stop("GATEWAY_HEALTHCHECK_REJECTED")
    return True

def verify_existing():
    """Read-only recovery: exact old source artifact, new clean verifier checkout.

    Does not call the tag-absence readiness check. Revalidates protected
    host baseline, final network collision/nft syntax, local socket and
    immutable original image/config against the exact approved base.
    """
    readiness=load(READY,"r23d2b1c_existing_readiness")
    acquisition=load(ACQUIRE,"r23d2b1c_existing_acquisition")
    acquisition.require_local_docker_socket()
    planner=readiness.load_planner()
    plan=planner.preflight()  # protected production + nft --check, no image-tag check
    if not planner.check_gateway_source():
        raise Stop("GATEWAY_ONLY_IMAGE_CONTRACT_CHANGED")
    if readiness.BASE_RE.findall(planner.GATEWAY_IMAGE_FILE.read_text("utf8"))!=[acquisition.PINNED_BASE]:
        raise Stop("SOURCE_IMAGE_DIGEST_CHANGED")
    check_deny_by_default_context()
    if not acquisition.image_inspect(acquisition.PINNED_BASE):
        raise Stop("PINNED_BASE_DIGEST_UNVERIFIED")
    base=inspected_image(acquisition.PINNED_BASE)
    if (base.get("Config") or {}).get("Entrypoint")!=["docker-entrypoint.sh"]:
        raise Stop("PINNED_BASE_ENTRYPOINT_MISMATCH")
    tag="homedocker-trakt-bridge:v140-r23d-"+EXISTING_SHA[:12]
    image=inspected_image(tag)
    if image.get("Id")!=EXISTING_IMAGE_ID:
        raise Stop("EXISTING_GATEWAY_IMAGE_ID_MISMATCH")
    attest_built_image(image,tag=tag,sha=EXISTING_SHA,
                       base_arch=base["Architecture"])
    if inspected_image(tag).get("Id")!=EXISTING_IMAGE_ID:
        raise Stop("GATEWAY_IMAGE_ID_CHANGED_AFTER_POSTFLIGHT")
    return {
        "audit":"R23D2B1C_EXISTING_IMAGE_RECOVERY",
        "status":"EXISTING_IMAGE_ATTESTED_PROTECTED_BASELINE_PASS",
        "original_image_sha_verified":True,
        "original_image_id_verified":True,
        "inherited_pinned_base_entrypoint_verified":True,
        "protected_rc6_r23b_r23a_preflight_passed":True,
        "gateway_container_started":False,
        "network_nft_or_production_modified":False,
        "authenticated_jellyfin_session_allowed":False,
        "next":"REVIEW_GUARDED_SYNTHETIC_ONLY_RUNTIME",
    }

def apply(state):
    argv=state["build_command"][:2]+[
        "--label",ROLE_LABEL+"=gateway-only",
        "--label",SOURCE_LABEL+"="+state["sha"],
    ]+state["build_command"][2:]
    with tempfile.TemporaryDirectory(prefix="homedocker-r23d2b1c-build-") as cfg:
        env=os.environ.copy()
        env["DOCKER_CONFIG"]=cfg
        env["DOCKER_BUILDKIT"]="1"
        env["BUILDKIT_PROGRESS"]="quiet"
        try:
            command(argv,env=env,timeout=240)
        except Stop:
            raise Stop("PINNED_GATEWAY_BUILD_STOP_INSPECT_LOCAL_TAG") from None
    image=inspected_image(state["tag"])
    attest_built_image(image,tag=state["tag"],
                       sha=state["sha"],base_arch=state["base_arch"])
    # R23D2B0 preflight is deliberately separate from R23D2B1A cache check,
    # which REQUIRES tag absence and therefore fails AFTER a successful build.
    post=state["planner"].preflight()
    if post.get("image_tag")!=state["tag"]:
        raise Stop("PROTECTED_POST_BUILD_BASELINE_CHANGED")
    if inspected_image(state["tag"]).get("Id")!=image["Id"]:
        raise Stop("GATEWAY_IMAGE_ID_CHANGED_AFTER_POSTFLIGHT")
    return {
        "audit":"R23D2B1C_CACHED_GATEWAY_ONLY_BUILD",
        "status":"PINNED_GATEWAY_IMAGE_BUILT_AND_ATTESTED_NO_RUNTIME",
        "exact_cached_node_digest_only":True,
        "build_network_mode_none_pull_disabled":True,
        "build_context_deny_by_default":True,
        "new_source_label_and_immutable_image_id_verified":True,
        "gateway_nonroot_no_volume_and_private_proof_path_verified":True,
        "protected_rc6_r23b_r23a_postflight_passed":True,
        "local_docker_image_cache_modified":True,
        "gateway_canary_container_created":False,
        "final_network_or_nft_firewall_created":False,
        "aio_session_or_history_accessed":False,
        "aio_authenticated_relay_allowed":False,
        "next":"DESIGN_GUARDED_FINAL_NETWORK_FAKE_ONLY_RUNTIME",
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--verify-existing",action="store_true")
    parser.add_argument("--confirm",default="")
    args=parser.parse_args()
    try:
        if args.verify_existing:
            if args.apply or args.confirm:
                raise Stop("VERIFY_EXISTING_REQUIRES_READ_ONLY_MODE")
            print(json.dumps(verify_existing(),indent=2))
            print("R23D2B1C_EXISTING_IMAGE_VERIFY_PASS")
            return
        state=preflight()
        if not args.apply:
            print(json.dumps({
                "audit":"R23D2B1C_GATEWAY_ONLY_IMAGE_BUILD_DRY_RUN",
                "status":"READY_FOR_EXPLICIT_OFFLINE_GATEWAY_BUILD",
                "protected_production_and_pinned_source_passed":True,
                "pinned_base_locally_cached_and_repo_digest_verified":True,
                "reviewed_deny_by_default_build_context":True,
                "new_gateway_image_tag_not_in_use":True,
                "docker_registry_pull_enabled":False,
                "gateway_image_built":False,
                "container_network_nft_or_production_modified":False,
                "authenticated_jellyfin_session_allowed":False,
                "next":"EXPLICIT_OPERATOR_GATEWAY_BUILD_CONFIRMATION_REQUIRED",
            },indent=2))
            print("R23D2B1C_GATEWAY_BUILD_DRY_RUN_PASS")
            return
        if args.confirm!=CONFIRM:
            raise Stop("EXPLICIT_GATEWAY_BUILD_CONFIRMATION_REQUIRED")
        print(json.dumps(apply(state),indent=2))
        print("R23D2B1C_GATEWAY_IMAGE_READY")
    except Exception as error:
        reason=error.args[0] if isinstance(error,Stop) and len(error.args)==1 else ""
        allowed={
            "VERIFY_EXISTING_REQUIRES_READ_ONLY_MODE",
            "SOURCE_IMAGE_DIGEST_CHANGED",
            "GATEWAY_ONLY_IMAGE_CONTRACT_CHANGED",
            "PINNED_BASE_ENTRYPOINT_MISMATCH",
            "EXISTING_GATEWAY_IMAGE_ID_MISMATCH",
            "REVIEWED_SOURCE_MODULE_MISSING",
            "ISOLATED_DOCKER_CONTEXT_NOT_VERIFIED",
            "PINNED_BASE_NOT_CACHED_HOLD_BUILD",
            "PINNED_BASE_DIGEST_UNVERIFIED",
            "IMMUTABLE_SOURCE_SHA_REJECTED",
            "REVIEWED_BUILD_CONTRACT_CHANGED",
            "UNEXPECTED_IMAGE_INSPECTION_REFERENCE",
            "DOCKER_COMMAND_FAILED_OR_TIMED_OUT",
            "DOCKER_COMMAND_FAILED_OR_OVERSIZED",
            "DOCKER_IMAGE_INSPECT_REJECTED",
            "EXPLICIT_GATEWAY_BUILD_CONFIRMATION_REQUIRED",
            "PINNED_GATEWAY_BUILD_STOP_INSPECT_LOCAL_TAG",
            "GATEWAY_BUILT_IMAGE_IDENTITY_REJECTED",
            "GATEWAY_BUILT_IMAGE_TAG_REJECTED",
            "GATEWAY_IMAGE_CONFIG_REJECTED",
            "GATEWAY_ENV_CONFIG_REJECTED",
            "GATEWAY_ENV_DUPLICATE_KEYS",
            "GATEWAY_ENV_SECRET_CONFIG_REJECTED",
            "GATEWAY_HEALTHCHECK_REJECTED",
            "PROTECTED_POST_BUILD_BASELINE_CHANGED",
            "GATEWAY_IMAGE_ID_CHANGED_AFTER_POSTFLIGHT",
        }
        if reason not in allowed:
            reason="PROTECTED_PREFLIGHT_OR_UNCLASSIFIED_BUILD_STOP"
        print(json.dumps({
            "audit":"R23D2B1C_SAFE_DIAGNOSTIC",
            "status":"STOP",
            "reason":reason,
            "image_cache_or_new_tag_may_have_changed":args.apply,
            "container_network_nft_or_production_modified":False,
            "authenticated_jellyfin_session_allowed":False,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
