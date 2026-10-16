#!/usr/bin/env python3
"""R23D-2B1J: bounded REAL Docker/nft read-only evidence collector, HOLD-only.

No apply/install/launch/cleanup. Accepts no caller-chosen paths, commands,
interfaces, images or credentials. Never prints raw inspect JSON, IPs, env
variables, NFT rules or token data. Observations are sequential (not atomic)
and do NOT attest enforcement, source ownership or boot safety.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ABSENCE=HERE/"audit-v140-r23d2b1h-real-evidence-readonly.py"
JOINT=HERE/"verify-v140-r23d2b1i-nft-docker-evidence-offline.py"
CONTAINER="homedocker-v140-r23d-gateway-canary"
NETWORK="homedocker_v140_r23d_local"
BRIDGE="br-r23dgate"
NFT_TABLE="homedocker_r23d_guard"
LIMIT=1024*1024
LIST_COMMANDS=(
    ("docker","ps","-a","--format","{{.Names}}"),
    ("docker","network","ls","--format","{{.Name}}"),
    ("ip","-j","link","show"),
    ("nft","-j","list","tables"),
)
DETAIL_COMMANDS=(
    ("docker","container","inspect",CONTAINER),
    ("docker","network","inspect",NETWORK),
    ("nft","-j","list","table","inet",NFT_TABLE),
)
ALLOWED=frozenset(LIST_COMMANDS+DETAIL_COMMANDS)

class Stop(Exception):
    pass

def module(path,name):
    if not path.is_file() or path.is_symlink():
        raise Stop("SOURCE_MODULE_MISSING")
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise Stop("SOURCE_MODULE_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def read(cmd):
    if type(cmd) not in (tuple,list) or tuple(cmd) not in ALLOWED:
        raise Stop("READ_COMMAND_NOT_ALLOWLISTED")
    try:
        p=subprocess.run(list(cmd),capture_output=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("READ_ONLY_INSPECTION_UNAVAILABLE") from None
    if p.returncode!=0 or len(p.stdout)>LIMIT or len(p.stderr)>LIMIT:
        raise Stop("READ_ONLY_INSPECTION_UNAVAILABLE")
    try:
        return p.stdout.decode("utf-8","strict")
    except UnicodeError:
        raise Stop("READ_ONLY_ENCODING_INVALID") from None

def parse(raw):
    try:
        if not isinstance(raw,str) or len(raw)>LIMIT:
            raise Stop("READ_ONLY_EVIDENCE_SIZE_INVALID")
        return json.loads(raw)
    except (ValueError,UnicodeError,RecursionError):
        raise Stop("READ_ONLY_EVIDENCE_JSON_INVALID") from None

def presence_report(container_names,network_names,link_doc,nft_doc):
    h=module(ABSENCE,"r23d2b1j_names")
    names1=h.names(container_names)
    names2=h.names(network_names)
    if (not isinstance(link_doc,list) or len(link_doc)>2048
        or any(not isinstance(x,dict) or not isinstance(x.get("ifname"),str)
               for x in link_doc)):
        raise Stop("LINK_DATA_INVALID")
    if (not isinstance(nft_doc,dict) or set(nft_doc)!={"nftables"}
        or not isinstance(nft_doc["nftables"],list)
        or len(nft_doc["nftables"])>4096):
        raise Stop("NFT_TABLES_DATA_INVALID")
    table_names=[]
    for item in nft_doc["nftables"]:
        if not isinstance(item,dict) or len(item)!=1:
            raise Stop("NFT_TABLES_DATA_INVALID")
        if "metainfo" in item:
            if not isinstance(item["metainfo"],dict):
                raise Stop("NFT_TABLES_DATA_INVALID")
        elif "table" in item and isinstance(item["table"],dict):
            table=item["table"]
            if not isinstance(table.get("name"),str) or not isinstance(table.get("family"),str):
                raise Stop("NFT_TABLES_DATA_INVALID")
            table_names.append((table["family"],table["name"]))
        else:
            raise Stop("NFT_TABLES_DATA_INVALID")
    table_matches=[x for x in table_names if x[1]==NFT_TABLE]
    if len(table_matches)>1 or (table_matches and table_matches!=[("inet",NFT_TABLE)]):
        raise Stop("UNTRUSTED_NFT_TABLE_NAME_OR_FAMILY")
    flags={
        "container":CONTAINER in names1,
        "network":NETWORK in names2,
        "interface":BRIDGE in [x["ifname"] for x in link_doc],
        "guard_table":bool(table_matches),
    }
    if all(not value for value in flags.values()):
        return "ABSENT"
    if all(flags.values()):
        return "PRESENT_UNTRUSTED"
    raise Stop("PARTIAL_OR_COLLIDING_R23D_RESOURCE_SET")

def preview_structural(container_doc,network_doc,nft_doc):
    """Only an observation preview. Even PASS never proves a live fence."""
    j=module(JOINT,"r23d2b1j_joint")
    if not isinstance(container_doc,list) or len(container_doc)!=1:
        raise Stop("CONTAINER_SNAPSHOT_SHAPE")
    if not isinstance(network_doc,list) or len(network_doc)!=1:
        raise Stop("NETWORK_SNAPSHOT_SHAPE")
    c,n=container_doc[0],network_doc[0]
    if not isinstance(c,dict) or not isinstance(n,dict):
        raise Stop("DETAIL_SNAPSHOT_SHAPE")
    try:
        subnet=n["IPAM"]["Config"][0]["Subnet"]
        gateway=n["IPAM"]["Config"][0]["Gateway"]
        canary=c["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
        if (not isinstance(subnet,str) or not isinstance(gateway,str)
            or not isinstance(canary,str)):
            raise ValueError()
        verifier=j.verifier()
        # Explicit original image/tag, restart and remote-host restrictions.
        host=c.get("HostConfig") or {}
        if (c.get("Image")!=j.ORIGINAL_IMAGE_ID
            or (c.get("Config") or {}).get("Image")!=j.EXACT_IMAGE_TAG
            or (host.get("RestartPolicy") or {}).get("Name")!="no"
            or host.get("PidMode") not in (None,"")
            or host.get("ExtraHosts") or host.get("Dns")):
            raise ValueError()
        if verifier.check_persistent_state(
            nft_doc,container_doc,network_doc,bridge=BRIDGE,subnet=subnet,
            host_gateway=gateway,canary=canary,relay_port=j.RELAY_PORT,
            expected_image_id=j.ORIGINAL_IMAGE_ID) is not True:
            raise ValueError()
    except Exception:
        raise Stop("UNTRUSTED_SNAPSHOT_STRUCTURE_REJECTED") from None
    return True

def report(status,*,details=False,baseline=False):
    return {
        "audit":"R23D2B1J_DOCKER_NFT_REAL_READONLY_COLLECTOR",
        "status":status,
        "docker_nft_ip_read_operations_only":True,
        "actual_r23d_runtime_details_read":details,
        "protected_absence_baseline_rechecked":baseline,
        "sequential_evidence_not_atomic":True,
        "reader_identity_or_ownership_authenticated":False,
        "boot_time_fence_installed_or_proven":False,
        "kernel_packet_enforcement_qualified":False,
        "independent_watchdog_or_kill_path_wired":False,
        "docker_nft_systemd_nginx_or_production_modified":False,
        "real_canary_start_authorized":False,
        "authenticated_aio_relay_allowed":False,
    }

def audit():
    if os.geteuid()!=0:
        raise Stop("ROOT_REQUIRED_FOR_READONLY_COLLECTION")
    states=[read(list(x)) for x in LIST_COMMANDS]
    classification=presence_report(states[0],states[1],parse(states[2]),parse(states[3]))
    if classification=="ABSENT":
        inherited=module(ABSENCE,"r23d2b1j_protected_absence").audit()
        if inherited.get("status")!="INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD":
            raise Stop("PROTECTED_ABSENCE_BASELINE_CHANGED")
        return report("ABSENT_CONFIRMED_BOOT_FENCING_HOLD",baseline=True)
    # This route is FORENSIC ONLY; it cannot qualify a runtime or preflight.
    detail=[parse(read(list(x))) for x in DETAIL_COMMANDS]
    preview_structural(detail[0],detail[1],detail[2])
    return report("STRUCTURE_OBSERVED_UNTRUSTED_BOOT_FENCING_HOLD",details=True)

ERRORS={
    "SOURCE_MODULE_MISSING","READ_COMMAND_NOT_ALLOWLISTED",
    "READ_ONLY_INSPECTION_UNAVAILABLE","READ_ONLY_ENCODING_INVALID",
    "READ_ONLY_EVIDENCE_SIZE_INVALID","READ_ONLY_EVIDENCE_JSON_INVALID",
    "LINK_DATA_INVALID","NFT_TABLES_DATA_INVALID",
    "UNTRUSTED_NFT_TABLE_NAME_OR_FAMILY",
    "PARTIAL_OR_COLLIDING_R23D_RESOURCE_SET",
    "CONTAINER_SNAPSHOT_SHAPE","NETWORK_SNAPSHOT_SHAPE",
    "DETAIL_SNAPSHOT_SHAPE","UNTRUSTED_SNAPSHOT_STRUCTURE_REJECTED",
    "ROOT_REQUIRED_FOR_READONLY_COLLECTION",
    "PROTECTED_ABSENCE_BASELINE_CHANGED",
}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only",action="store_true")
    args=parser.parse_args()
    if not args.audit_only:
        parser.error("NO_APPLY_OR_DEPLOY_MODE")
    try:
        result=audit()
        print(json.dumps(result,indent=2))
        print("R23D2B1J_EVIDENCE_OBSERVED_HOLD")
    except Exception as e:
        reason=e.args[0] if isinstance(e,Stop) and len(e.args)==1 else ""
        if reason not in ERRORS:
            reason="PROTECTED_OR_UNCLASSIFIED_COLLECTOR_STOP"
        print(json.dumps({
            "audit":"R23D2B1J_SAFE_DIAGNOSTIC","status":"STOP",
            "reason":reason,"authenticated_aio_relay_allowed":False,
            "host_mutations_performed_by_collector":False,
        },indent=2),file=sys.stderr)
        raise SystemExit(1) from None

if __name__=="__main__":
    main()
