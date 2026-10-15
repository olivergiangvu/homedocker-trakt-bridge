#!/usr/bin/env python3
"""R23D-2B0: no-credential Gateway-only deployment plan; READ ONLY on host.

This is a command contract, not an installer: there is intentionally NO
--apply, subprocess use of build/create/run/nft add and NO real AIO tokens.
The real temporary R23D-1G guard trial passed, but that guard was removed.
Before running a final-network canary, recheck protected R23D0/1F baseline,
plan exact nonoverlapping RFC1918 IPv4 addresses, and confirm pinned git
source, isolated Dockerfile image, synthetic proof/one peer/no persistent volume.

Every returned command is a list of tokens for security regression assertions
only. CLI NEVER PRINTS those commands, network addresses, local paths or image
digest; it prints a fixed summary that is safe to share for host review.

A future orchestrator must *verify* exact image id, table ownership and
network interface; install guarded INPUT+FORWARD before container creation;
perform credential-free positive/negative controls on the final bridge; retain
guard throughout canary lifetime; fail closed on reboot/watchdog loss; and
remove canary -> network -> nft table ONLY after strict attested ownership.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
PLANNER=HERE/"audit-v140-r23d1f-firewall-plan-readonly.py"
RENDER=HERE/"render-v140-r23d-nft-policy-offline.py"
NFT=HERE/"attest-v140-r23d-persistent-guard.py"
GATEWAY_IMAGE_FILE=ROOT/"Dockerfile.gateway-canary"
GATEWAY_ENTRY=ROOT/"src/gateway-canary-entry.mjs"
GATEWAY_CONFIG=ROOT/"src/gateway-standalone.mjs"
FINAL_BRIDGE="br-r23dgate"
FINAL_NETWORK="homedocker_v140_r23d_local"
FINAL_CONTAINER="homedocker-v140-r23d-gateway-canary"
FINAL_LABEL="r23d-persistent-canary"
HOST_PROOF_PATH="/var/lib/homedocker-r23d-canary/identity-proof.json"
GUEST_PROOF_PATH="/run/identity-proof.json"
FAKE_USER="R23D_FAKE_USER_000"
FAKE_PROFILE="aaaaaaaaaaaaaaaaaaaaaaaa"
CONFIRMATION_ALLOWED=False

class Stop(Exception):
    pass

def load(path,name):
    if not path.is_file() or path.is_symlink():
        raise Stop("REQUIRED_PINNED_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise Stop("REQUIRED_PINNED_SOURCE_MISSING")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def source_identity():
    if os.geteuid()!=0:
        raise Stop("SUDO_REQUIRED_FOR_PROTECTED_PRECHECK")
    try:
        # sudo runs Git as root against a user-owned detached checkout.
        # Trust ONLY this exact source directory, never a global wildcard.
        git=["git","-c","safe.directory="+str(ROOT),"-C",str(ROOT)]
        rev=subprocess.run([*git,"rev-parse","HEAD"],
                           capture_output=True,text=True,timeout=10,check=False)
        dirty=subprocess.run([*git,"status","--porcelain"],
                             capture_output=True,text=True,timeout=10,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("PINNED_GIT_SOURCE_UNAVAILABLE") from None
    if (rev.returncode or dirty.returncode or dirty.stdout
        or not re.fullmatch(r"[a-f0-9]{40}",rev.stdout.strip())
        or len(dirty.stdout)>1024):
        raise Stop("GIT_SOURCE_NOT_IMMUTABLE_CLEAN")
    return rev.stdout.strip()

def check_gateway_source():
    if any(not path.is_file() or path.is_symlink()
           for path in (GATEWAY_IMAGE_FILE,GATEWAY_ENTRY,GATEWAY_CONFIG)):
        raise Stop("PINNED_GATEWAY_SOURCE_MISSING")
    if any(p.stat().st_size > 32768 for p in
           (GATEWAY_IMAGE_FILE,GATEWAY_ENTRY,GATEWAY_CONFIG)):
        raise Stop("PINNED_GATEWAY_SOURCE_UNEXPECTED")
    dockerfile=GATEWAY_IMAGE_FILE.read_text("utf8")
    entry=GATEWAY_ENTRY.read_text("utf8")
    standalone=GATEWAY_CONFIG.read_text("utf8")
    if (not re.search(r"(?m)^FROM node:24-alpine@sha256:[a-f0-9]{64}\s*$",dockerfile)
        or not re.search(r"(?m)^USER node\s*$",dockerfile)
        or re.search(r"(?im)^\s*VOLUME\b",dockerfile)
        or "src/gateway-canary-entry.mjs" not in dockerfile
        or "GATEWAY_PROOF_FILE=/run/identity-proof.json" not in dockerfile
        or "GATEWAY_PROOF_FILE" not in standalone
        or "src/index.mjs" in entry
        or "TRAKT_CLIENT_SECRET" in entry):
        raise Stop("GATEWAY_IMAGE_CONTRACT_CHANGED")
    return True

def planned_commands(net,sha):
    """Return reviewable explicit argv vectors. DO NOT EXECUTE THESE HERE."""
    offline=load(RENDER,"r23d2b_guard_renderer")
    verifier=load(NFT,"r23d2b_guard_attestation")
    hosts=list(net.hosts())
    if (len(hosts)<2 or len(sha)!=40 or
        not re.fullmatch(r"[a-f0-9]{40}",sha)
        or verifier.FINAL_CONTAINER!=FINAL_CONTAINER
        or verifier.FINAL_NETWORK!=FINAL_NETWORK
        or verifier.FINAL_PHASE!=FINAL_LABEL
        or verifier.EXPECTED_GATEWAY_HOST_PORT!="17145"
        or verifier.EXPECTED_GATEWAY_INNER_PORT!="7101/tcp"):
        raise Stop("SOURCE_PLAN_ATTESTATION_DISAGREEMENT")
    gateway,canary=str(hosts[0]),str(hosts[1])
    guard=offline.render_policy(FINAL_BRIDGE,str(net),gateway,canary,17420)
    image_tag="homedocker-trakt-bridge:v140-r23d-"+sha[:12]
    cmd={
      "docker_build":["docker","build","--pull=false","--network=none",
        "-f",str(GATEWAY_IMAGE_FILE),"-t",image_tag,str(ROOT)],
      "network_create":["docker","network","create","--driver","bridge",
        "--subnet",str(net),"--gateway",gateway,
        "--opt","com.docker.network.bridge.name="+FINAL_BRIDGE,
        "--opt","com.docker.network.bridge.enable_ip_masquerade=false",
        "--label","homedocker.phase="+FINAL_LABEL,FINAL_NETWORK],
      "guard_check":["nft","--check","--file","-"],
      "guard_install":["nft","--file","-"],
      "gateway_run":["docker","run","--detach","--pull","never",
        "--restart","no","--name",FINAL_CONTAINER,
        "--network",FINAL_NETWORK,"--ip",canary,
        "--read-only","--cap-drop","ALL",
        "--security-opt","no-new-privileges","--pids-limit","64",
        "--memory","192m","--cpus","1",
        "--user","node","--publish","127.0.0.1:17145:7101",
        "--label","homedocker.phase="+FINAL_LABEL,
        "--mount","type=bind,src="+HOST_PROOF_PATH+
                     ",dst="+GUEST_PROOF_PATH+",readonly",
        "--env","JELLYFIN_GATEWAY_AIO_USER_ID="+FAKE_USER,
        "--env","JELLYFIN_GATEWAY_UPSTREAM_URL=http://"+gateway+":17420/jellyfin",
        "--env","JELLYFIN_GATEWAY_RESUME_PASSTHROUGH=false",
        image_tag],
      "cleanup_order":["docker_canary_stop_remove",
                       "verify_private_bridge_empty_then_remove",
                       "verify_guard_unchanged_then_delete_owned_nft_table"],
    }
    return {"commands":cmd,"policy":guard,"net":str(net),"gateway":gateway,
            "canary":canary,"image_tag":image_tag}

def preflight():
    rev=source_identity()
    check_gateway_source()
    planner=load(PLANNER,"r23d2b_host_planner")
    planner.baseline()
    net,_=planner.inspect_candidates()
    offline=load(RENDER,"r23d2b_syntax_renderer")
    offline.validate(FINAL_BRIDGE,str(net),str(list(net.hosts())[0]),
                     str(list(net.hosts())[1]),17420)
    if planner.TEST_BRIDGE==FINAL_BRIDGE:
        raise Stop("FINAL_BRIDGE_CANNOT_EQUAL_TEMPORARY_BRIDGE")
    # Final interface/name collision check independent of temporary R23D1F.
    links=planner.parsed(["ip","-j","link","show"])
    netnames=planner.run(["docker","network","ls","--format",
                           "{{.Name}}"]).decode("utf8","strict").splitlines()
    contnames=planner.run(["docker","ps","-a","--format",
                           "{{.Names}}"]).decode("utf8","strict").splitlines()
    if (FINAL_BRIDGE in [x.get("ifname") for x in links]
        or FINAL_NETWORK in netnames or FINAL_CONTAINER in contnames):
        raise Stop("FINAL_R23D_DOCKER_RESOURCES_ALREADY_EXIST")
    plan=planned_commands(net,rev)
    planner.run(plan["commands"]["guard_check"],
                stdin=plan["policy"].encode("ascii"))
    return plan

REASONS={
  "SUDO_REQUIRED_FOR_PROTECTED_PRECHECK":"HOST",
  "PINNED_GIT_SOURCE_UNAVAILABLE":"SOURCE",
  "GIT_SOURCE_NOT_IMMUTABLE_CLEAN":"SOURCE",
  "PINNED_GATEWAY_SOURCE_MISSING":"SOURCE",
  "PINNED_GATEWAY_SOURCE_UNEXPECTED":"SOURCE",
  "GATEWAY_IMAGE_CONTRACT_CHANGED":"SOURCE",
  "SOURCE_PLAN_ATTESTATION_DISAGREEMENT":"SOURCE",
  "FINAL_BRIDGE_CANNOT_EQUAL_TEMPORARY_BRIDGE":"SCOPE",
  "FINAL_R23D_DOCKER_RESOURCES_ALREADY_EXIST":"COLLISION",
  "REQUIRED_PINNED_SOURCE_MISSING":"SOURCE",
}

def main():
    try:
        p=preflight()
        print(json.dumps({
          "audit":"R23D2B0_READONLY_GATEWAY_DEPLOYMENT_PLAN",
          "status":"FINAL_NETWORK_PLAN_READY_NOT_APPLIED",
          "git_source_clean_and_pinned":True,
          "gateway_only_dockerfile_no_implicit_volume":True,
          "gateway_image_source_digest_pinned":True,
          "fake_offline_jellyfin_user_only":True,
          "identity_proof_target_matches_container_image":True,
          "final_bridge_container_network_names_unused":True,
          "final_rfc1918_subnet_free_in_visible_host_inventories":True,
          "final_network_nft_kernel_syntax_check_passed":True,
          "nft_guard_must_install_before_canary_container":True,
          "persistent_guard_supervisor_and_rollback_not_yet_deployed":True,
          "real_jellyfin_session_allowed":False,
          "actual_gateway_image_built":False,
          "actual_final_network_or_firewall_created":False,
          "docker_firewall_nginx_aio_or_history_writes":0,
          "next":"IMPLEMENT_SUPERVISED_GUARD_LIFETIME_BEFORE_TEMPORARY_OFFLINE_APPLY",
        },indent=2))
        print("R23D2B0_READONLY_DEPLOYMENT_PLAN_PASS")
    except Exception as exc:
        reason=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        if reason not in REASONS:
            reason="PRECHECK_OR_NFT_SYNTAX_STOP"
        print(json.dumps({
            "audit":"R23D2B0_SAFE_DIAGNOSTIC",
            "status":"STOP","reason":reason,
            "actual_final_network_or_firewall_created":False,
            "real_jellyfin_session_allowed":False,
            "docker_firewall_nginx_aio_or_history_writes":0,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
