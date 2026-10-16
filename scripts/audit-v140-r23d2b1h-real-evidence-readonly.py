#!/usr/bin/env python3
"""R23D-2B1H: real read-only Docker, nft and bridge inventory; NO HOST APPLY.

R23D has no owned guard, bridge or container yet: finding all absent is a
legitimate inventory PASS but MUST report BOOT_FENCING_HOLD, not live attestation.
Reuses R23D-2B1E's exact existing-image and protected baseline audit. All
additional commands are fixed allowlisted READS. No personal IDs, credentials,
host addresses or firewall rules are echoed. No --apply or runtime mode.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
HOST_AUDIT=HERE/"audit-v140-r23d2b1e-host-boot-fence-readonly.py"
CANARY="homedocker-v140-r23d-gateway-canary"
NETWORK="homedocker_v140_r23d_local"
IFACE="br-r23dgate"
NFT_TABLE="homedocker_r23d_guard"
MAX_BYTES=4*1024*1024
FIXED_READS=frozenset({
    ("docker","ps","-a","--format","{{.Names}}"),
    ("docker","network","ls","--format","{{.Name}}"),
    ("ip","-j","link","show"),
    ("nft","-j","list","tables"),
})

class Stop(Exception):
    pass

def read(argv):
    if tuple(argv) not in FIXED_READS:
        raise Stop("COMMAND_NOT_READ_ONLY_ALLOWLISTED")
    try:
        p=subprocess.run(list(argv),capture_output=True,timeout=16,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("HOST_INVENTORY_UNAVAILABLE") from None
    if p.returncode!=0 or len(p.stdout)>MAX_BYTES or len(p.stderr)>MAX_BYTES:
        raise Stop("HOST_INVENTORY_UNAVAILABLE")
    try:
        return p.stdout.decode("utf8","strict")
    except UnicodeError:
        raise Stop("HOST_INVENTORY_ENCODING_REJECTED") from None

def names(raw):
    if not isinstance(raw,str) or len(raw)>MAX_BYTES:
        raise Stop("DOCKER_RESOURCE_NAMES_INVALID")
    lines=raw.splitlines()
    if len(lines)>4096:
        raise Stop("DOCKER_RESOURCE_NAMES_INVALID")
    if any(not x or len(x)>255 or re.search(r"\s",x)
           or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",x) is None
           for x in lines):
        raise Stop("DOCKER_RESOURCE_NAMES_INVALID")
    if len(lines)!=len(set(lines)):
        raise Stop("DOCKER_RESOURCE_NAMES_INVALID")
    return set(lines)

def decode_json(raw):
    try:
        return json.loads(raw)
    except (TypeError,ValueError,UnicodeError,RecursionError):
        raise Stop("STRUCTURED_INVENTORY_REJECTED") from None

def evaluate_inventory(*,container_names,network_names,link_doc,nft_doc):
    if type(container_names) is not set or type(network_names) is not set:
        raise Stop("DOCKER_RESOURCE_NAMES_INVALID")
    if CANARY in container_names or NETWORK in network_names:
        raise Stop("OWNED_RESOURCE_NAME_COLLISION")
    if (not isinstance(link_doc,list) or len(link_doc)>2048
        or any(not isinstance(row,dict) or not isinstance(row.get("ifname"),str)
               for row in link_doc)):
        raise Stop("LINUX_INTERFACE_INVENTORY_REJECTED")
    if len([x for x in link_doc if x["ifname"]==IFACE])>0:
        raise Stop("EXPECTED_BRIDGE_ALREADY_EXISTS")
    if (not isinstance(nft_doc,dict) or set(nft_doc)!={"nftables"}
        or not isinstance(nft_doc["nftables"],list)
        or len(nft_doc["nftables"])>4096):
        raise Stop("NFT_TABLE_INVENTORY_REJECTED")
    tables=set()
    for item in nft_doc["nftables"]:
        if not isinstance(item,dict) or len(item)!=1:
            raise Stop("NFT_TABLE_INVENTORY_REJECTED")
        if "metainfo" in item:
            if not isinstance(item["metainfo"],dict):
                raise Stop("NFT_TABLE_INVENTORY_REJECTED")
            continue
        if "table" not in item or not isinstance(item["table"],dict):
            raise Stop("NFT_TABLE_INVENTORY_REJECTED")
        obj=item["table"]
        if (obj.get("family") not in {"ip","ip6","inet","arp","bridge","netdev"}
            or not isinstance(obj.get("name"),str)
            or not obj["name"] or len(obj["name"])>255):
            raise Stop("NFT_TABLE_INVENTORY_REJECTED")
        tables.add((obj["family"],obj["name"]))
        if obj["name"]==NFT_TABLE:
            raise Stop("OWNED_GUARD_TABLE_PRESENT_NOT_VERIFIED")
    return {
        "audit":"R23D2B1H_REAL_DOCKER_NFT_INVENTORY_READONLY",
        "status":"INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD",
        "protected_rc6_r23b_r23a_and_image_check_passed":True,
        "real_docker_container_inventory_checked":True,
        "real_docker_network_inventory_checked":True,
        "real_linux_interface_inventory_checked":True,
        "real_nft_kernel_table_listing_checked":True,
        "r23d_container_network_interface_and_table_absent":True,
        "kernel_guard_enforcement_proven":False,
        "nft_json_rule_semantics_attested":False,
        "docker_peer_network_runtime_attested":False,
        "independent_supervisor_or_boot_fence_installed":False,
        "synthetic_tcp_positive_negative_controls_run":False,
        "docker_nft_systemd_nginx_or_production_modified":False,
        "authenticated_aio_relay_allowed":False,
        "next":"IMPLEMENT_INDEPENDENT_BOOT_FENCE_AND_GUARD_BEFORE_RUNTIME_ATTESTATION",
    }

def load_host_audit():
    if not HOST_AUDIT.is_file() or HOST_AUDIT.is_symlink():
        raise Stop("PROTECTED_HOST_AUDIT_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location("r23d2b1h_verified_host_audit",HOST_AUDIT)
    if spec is None or spec.loader is None:
        raise Stop("PROTECTED_HOST_AUDIT_SOURCE_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def audit():
    if os.geteuid()!=0:
        raise Stop("ROOT_REQUIRED_FOR_HOST_INVENTORY")
    prior=load_host_audit().audit()
    if prior.get("status")!="PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD":
        raise Stop("PROTECTED_HOST_BASELINE_NOT_VERIFIED")
    return evaluate_inventory(
        container_names=names(read(["docker","ps","-a","--format","{{.Names}}"])),
        network_names=names(read(["docker","network","ls","--format","{{.Name}}"])),
        link_doc=decode_json(read(["ip","-j","link","show"])),
        nft_doc=decode_json(read(["nft","-j","list","tables"])),
    )

REASONS={
    "COMMAND_NOT_READ_ONLY_ALLOWLISTED","HOST_INVENTORY_UNAVAILABLE",
    "HOST_INVENTORY_ENCODING_REJECTED","DOCKER_RESOURCE_NAMES_INVALID",
    "STRUCTURED_INVENTORY_REJECTED","OWNED_RESOURCE_NAME_COLLISION",
    "LINUX_INTERFACE_INVENTORY_REJECTED","EXPECTED_BRIDGE_ALREADY_EXISTS",
    "NFT_TABLE_INVENTORY_REJECTED","OWNED_GUARD_TABLE_PRESENT_NOT_VERIFIED",
    "PROTECTED_HOST_AUDIT_SOURCE_MISSING","ROOT_REQUIRED_FOR_HOST_INVENTORY",
    "PROTECTED_HOST_BASELINE_NOT_VERIFIED",
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only",action="store_true")
    args=parser.parse_args()
    if not args.audit_only:
        parser.error("NO_APPLY_OR_RUNTIME_SUPPORTED")
    try:
        print(json.dumps(audit(),indent=2))
        print("R23D2B1H_REAL_INVENTORY_PASS_BOOT_FENCING_HOLD")
    except Exception as exc:
        reason=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        if reason not in REASONS:
            reason="PROTECTED_HOST_OR_UNCLASSIFIED_INVENTORY_STOP"
        print(json.dumps({
            "audit":"R23D2B1H_SAFE_DIAGNOSTIC","status":"STOP",
            "reason":reason,"runtime_or_kernel_enforcement_qualified":False,
            "authenticated_aio_relay_allowed":False,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
