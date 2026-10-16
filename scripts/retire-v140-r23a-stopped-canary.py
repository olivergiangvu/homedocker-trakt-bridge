#!/usr/bin/env python3
"""Exact R23A *stopped* canary retirement; audit-only unless explicitly confirmed.

Preserve ALL images, all named volumes, active services, experimental R23B
canary, resume contract canary, Trakt history, nginx, nft and systemd.
Only deletion targets: one stopped, no-mount R23A container and its private,
zero-endpoint network. Fail closed if owner state/identities have drifted.
"""
import argparse
import json
import subprocess
import sys

OLD="homedocker-v140-integrated-canary"
OLD_NETWORK="homedocker_v140_canary_internal"
R23B="homedocker-v140-r23b-canary"
RESUME="homedocker-resume-contract-canary"
BRIDGE="trakt-bridge"
BRIDGE_ID="sha256:74ef797c7d8556f27f57571023cc2a9371afc81e13766b81295490df745e2116"
CANARY_ID="sha256:6f239a2f37ab58fe74d2eac8bfc17c095ce22f6a0c0542f2a89b06e672a924fd"
OLD_TAG="homedocker-trakt-bridge:v140-r23a-e442ce461209"
CONFIRM="RETIRE_STOPPED_R23A_CONTAINER_AND_EMPTY_NETWORK"
EXPECTED_BINDINGS={
    "7000/tcp":[{"HostIp":"127.0.0.1","HostPort":"17041"}],
    "7101/tcp":[{"HostIp":"127.0.0.1","HostPort":"17141"}],
}

class Stop(Exception):
    pass

def cmd(*args):
    if not args or args[0]!="docker":
        raise Stop("NON_DOCKER_COMMAND_REJECTED")
    allowed_read=(
        ("docker","ps","-aq","--no-trunc"),
        ("docker","container","inspect",OLD),
        ("docker","container","inspect",R23B),
        ("docker","container","inspect",RESUME),
        ("docker","container","inspect",BRIDGE),
        ("docker","network","inspect",OLD_NETWORK),
    )
    allowed_mutate=(
        ("docker","container","rm",OLD),
        ("docker","network","rm",OLD_NETWORK),
    )
    if args not in allowed_read+allowed_mutate and not (
        len(args)>3 and args[:3]==("docker","container","inspect")
        and all(isinstance(x,str) and len(x)==64
                and all(c in "0123456789abcdef" for c in x)
                for x in args[3:])
    ):
        raise Stop("COMMAND_NOT_ALLOWLISTED")
    try:
        p=subprocess.run(args,capture_output=True,timeout=20,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("DOCKER_READ_OR_DELETE_UNAVAILABLE") from None
    if p.returncode or len(p.stdout)>8*1024*1024:
        raise Stop("DOCKER_READ_OR_DELETE_UNAVAILABLE")
    try:
        return p.stdout.decode("utf8","strict")
    except UnicodeError:
        raise Stop("DOCKER_EVIDENCE_ENCODING_INVALID") from None

def single(kind,name):
    try:
        doc=json.loads(cmd("docker",kind,"inspect",name))
    except (ValueError,TypeError):
        raise Stop("DOCKER_JSON_INVALID") from None
    if not isinstance(doc,list) or len(doc)!=1 or not isinstance(doc[0],dict):
        raise Stop("DOCKER_EVIDENCE_AMBIGUOUS")
    return doc[0]

def image_tag(c):
    return (c.get("Config") or {}).get("Image")
def state(c):
    return c.get("State") or {}
def restart(c):
    return ((c.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
def healthy(c):
    return bool(state(c).get("Running") and
                (state(c).get("Health") or {}).get("Status")=="healthy")

def check():
    bridge=single("container",BRIDGE)
    r23b=single("container",R23B)
    resume=single("container",RESUME)
    old=single("container",OLD)
    network=single("network",OLD_NETWORK)
    if not healthy(bridge) or bridge.get("Image")!=BRIDGE_ID or restart(bridge)!="unless-stopped":
        raise Stop("PRODUCTION_RC6_BASELINE_CHANGED")
    if not healthy(r23b) or r23b.get("Image")!=CANARY_ID or restart(r23b)!="no":
        raise Stop("R23B_CANARY_BASELINE_CHANGED")
    if not healthy(resume) or restart(resume)!="no":
        raise Stop("RESUME_CANARY_BASELINE_CHANGED")
    config=old.get("Config") or {}
    host=old.get("HostConfig") or {}
    labels=config.get("Labels") or {}
    if (old.get("Name")!="/"+OLD or old.get("Image")!=CANARY_ID
        or image_tag(old)!=OLD_TAG
        or state(old).get("Status")!="exited" or state(old).get("Running") is not False
        or restart(old)!="no"
        or old.get("Mounts")!=[]
        or host.get("AutoRemove") is not False
        or host.get("NetworkMode")!=OLD_NETWORK
        or host.get("PortBindings")!=EXPECTED_BINDINGS
        or labels.get("com.docker.compose.project") is not None
        or labels.get("com.docker.compose.service") is not None):
        raise Stop("RETIRED_CANARY_OWNER_IDENTITY_CHANGED")
    if (network.get("Name")!=OLD_NETWORK or network.get("Driver")!="bridge"
        or network.get("Containers") not in ({},None)
        or not isinstance(network.get("Id"),str)
        or len(network["Id"])!=64):
        raise Stop("RETIRED_CANARY_NETWORK_OWNER_CHANGED")
    # A stopped peer can retain a reference to the network even if the
    # network reports zero LIVE endpoints. Do not remove it if any OTHER
    # container references the old network, even if that peer is stopped.
    refs=cmd("docker","ps","-aq","--no-trunc").split()
    if len(set(refs))!=len(refs):
        raise Stop("CONTAINER_INVENTORY_DUPLICATED")
    if refs:
        raw=cmd("docker","container","inspect",*refs)
        try:
            allcontainers=json.loads(raw)
        except ValueError:
            raise Stop("CONTAINER_INVENTORY_MALFORMED") from None
        if not isinstance(allcontainers,list) or len(allcontainers)!=len(refs):
            raise Stop("CONTAINER_INVENTORY_INCOMPLETE")
        for c in allcontainers:
            if not isinstance(c,dict):
                raise Stop("CONTAINER_INVENTORY_MALFORMED")
            if c.get("Name")=="/"+OLD:
                continue
            m=(c.get("HostConfig") or {}).get("NetworkMode")
            attached=(c.get("NetworkSettings") or {}).get("Networks") or {}
            if m==OLD_NETWORK or OLD_NETWORK in attached:
                raise Stop("NETWORK_REFERENCED_BY_OTHER_CONTAINER")
    return {"retired_container_id":old.get("Id"),
            "retired_network_id":network["Id"],
            "r23b_id":r23b.get("Id"),
            "resume_id":resume.get("Id"),
            "production_id":bridge.get("Id")}

def audit_result(action):
    print(json.dumps({
        "audit":"V140_RETIRED_R23A_EXACT_TARGET_CLEANUP",
        "action":action,
        "only_target_container":OLD,
        "only_target_network":OLD_NETWORK,
        "production_rc6_unchanged":True,
        "r23b_and_resume_canaries_unchanged":True,
        "images_deleted":0,
        "volumes_deleted":0,
        "trakt_or_aio_history_modified":False,
        "stable_v140_release_authorized":False,
    },indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--confirm",default="")
    args=parser.parse_args()
    if args.apply != bool(args.confirm):
        raise Stop("EXPLICIT_APPLY_AND_CONFIRM_MUST_MATCH")
    if args.apply and args.confirm!=CONFIRM:
        raise Stop("EXACT_CONFIRMATION_REQUIRED")
    before=check()
    if any(not isinstance(v,str) or not v for v in before.values()):
        raise Stop("OWNED_RESOURCE_IDS_MISSING")
    if not args.apply:
        audit_result("ELIGIBLE_DRY_RUN_NO_DELETION")
        return
    # Revalidation immediately before target mutation; no general prune.
    if check()!=before:
        raise Stop("RESOURCE_DRIFT_BEFORE_DELETE")
    cmd("docker","container","rm",OLD)
    # Container remove must have had no effect on any protected identity.
    for name,key in ((BRIDGE,"production_id"),(R23B,"r23b_id"),(RESUME,"resume_id")):
        if single("container",name).get("Id")!=before[key]:
            raise Stop("PROTECTED_CONTAINER_CHANGED_AFTER_RETIREMENT")
    net=single("network",OLD_NETWORK)
    if (net.get("Id")!=before["retired_network_id"]
        or net.get("Containers") not in ({},None)):
        raise Stop("NETWORK_CHANGED_AFTER_RETIREMENT")
    cmd("docker","network","rm",OLD_NETWORK)
    audit_result("R23A_CONTAINER_AND_EMPTY_NETWORK_REMOVED")
    print("V140_TARGETED_RETIREMENT_COMPLETE")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        reason=e.args[0] if isinstance(e,Stop) and e.args and isinstance(e.args[0],str) else "UNEXPECTED_FAIL_CLOSED"
        print(json.dumps({"status":"STOP","reason":reason,
                          "may_require_manual_inventory":True,
                          "no_other_cleanup_authorized":True}),file=sys.stderr)
        raise SystemExit(1) from None
