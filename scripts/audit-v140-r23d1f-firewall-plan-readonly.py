#!/usr/bin/env python3
"""R23D-1F PREP ONLY: read-only subnet/interface/table reservation and nft check.

This command cannot create a Docker network, container, nft firewall table or
real AIO session; there is no --apply. Requires R23D1C/R23D0 invariants and
the R23D1B synthetic resource cleanup to remain true. Inspects Docker-managed
network subnets, kernel routes/addresses and nft table names only in memory.
Renders a hypothetical RFC1918 R23D-specific INPUT+FORWARD guard, checks its
syntax with 'nft --check --file -' (NO mutation), then discards the policy text.

A candidate subnet not seen in these inventories MAY still collide with custom
routing, policy routing, VPN or a future Docker allocation. This is only a
preparation gate, NOT enforced packet filtering or host-network isolation.
"""
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
BASE=HERE/"audit-v140-r23d-host-policy-readonly.py"
RENDER=HERE/"render-v140-r23d-nft-policy-offline.py"
TEST_BRIDGE="br-r23dtrial"
TEST_NET="homedocker_v140_r23d1f_trial"
TEST_CONTAINER="homedocker-v140-r23d1f-trial"
MAX_BYTES=4*1024*1024
CANDIDATES=(
    "10.231.240.0/28", "10.231.240.16/28",
    "10.231.240.32/28", "172.30.250.0/28",
    "172.30.250.16/28", "192.168.247.0/28",
)

class Stop(Exception):
    pass

def run(argv,*,stdin=None,may_fail=False):
    if not isinstance(argv,(list,tuple)) or not argv:
        raise Stop("INSPECTION_COMMAND_INVALID")
    exe=shutil.which(argv[0])
    if not exe:
        raise Stop("INSPECTION_TOOL_MISSING")
    try:
        p=subprocess.run([exe,*argv[1:]],input=stdin,capture_output=True,
                         timeout=16,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("HOST_INSPECTION_UNAVAILABLE") from None
    if len(p.stdout)>MAX_BYTES or len(p.stderr)>MAX_BYTES:
        raise Stop("HOST_INSPECTION_OVERSIZED")
    if p.returncode:
        if may_fail:
            return None
        raise Stop("HOST_INSPECTION_COMMAND_FAILED")
    return p.stdout

def parsed(argv,**kwargs):
    raw=run(argv,**kwargs)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError,UnicodeError,TypeError):
        raise Stop("HOST_INSPECTION_JSON_REJECTED") from None

def load_module(path,name):
    if not path.is_file() or path.is_symlink():
        raise Stop("AUDITED_SOURCE_MISSING")
    spec=importlib.util.spec_from_file_location(name,path)
    if not spec or not spec.loader:
        raise Stop("AUDITED_SOURCE_MISSING")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def baseline():
    guard=load_module(BASE,"r23d1f_host_policy_guard")
    try:
        if guard.baseline() is not True:
            raise Stop("HOST_TOPOLOGY_OR_PROBE_CLEANUP_FAILED")
    except Exception:
        raise Stop("HOST_TOPOLOGY_OR_PROBE_CLEANUP_FAILED") from None
    return True

def used_subnets(routes,addrs,docker_networks):
    used=[]
    if not isinstance(routes,list) or not isinstance(addrs,list) or \
       not isinstance(docker_networks,list):
        raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
    for record in routes:
        if not isinstance(record,dict):
            raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
        dst=record.get("dst")
        if not dst or dst=="default":
            continue
        try:
            net=ipaddress.ip_network(dst,strict=False)
        except ValueError:
            raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED") from None
        if net.version==4:
            used.append(net)
    for iface in addrs:
        if not isinstance(iface,dict) or not isinstance(iface.get("addr_info"),list):
            raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
        for address in iface["addr_info"]:
            if not isinstance(address,dict):
                raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
            if address.get("family")!="inet":
                continue
            try:
                used.append(ipaddress.ip_interface(
                    str(address["local"])+"/"+str(address["prefixlen"])).network)
            except (ValueError,KeyError,TypeError):
                raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED") from None
    for network in docker_networks:
        if not isinstance(network,dict):
            raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
        config=(network.get("IPAM") or {}).get("Config") or []
        if not isinstance(config,list):
            raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
        for row in config:
            if not isinstance(row,dict):
                raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
            subnet=row.get("Subnet")
            if not subnet:
                continue
            try:
                net=ipaddress.ip_network(subnet,strict=False)
            except ValueError:
                raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED") from None
            if net.version==4:
                used.append(net)
    return used

def candidate(used):
    for item in CANDIDATES:
        choice=ipaddress.ip_network(item)
        if not any(choice.overlaps(current) for current in used):
            return choice
    raise Stop("NO_SAFE_CANDIDATE_IN_KNOWN_INVENTORIES")

def inspect_candidates():
    routes=parsed(["ip","-j","-4","route","show","table","all"])
    addrs=parsed(["ip","-j","-4","address","show"])
    links=parsed(["ip","-j","link","show"])
    ids=run(["docker","network","ls","-q"]).decode("ascii","strict").splitlines()
    if len(ids)>128 or any(not re.fullmatch(r"[0-9a-f]{12,64}",x) for x in ids):
        raise Stop("DOCKER_NETWORK_INVENTORY_REJECTED")
    networks=parsed(["docker","network","inspect",*ids]) if ids else []
    if not isinstance(links,list) or any(not isinstance(l,dict) for l in links):
        raise Stop("NETWORK_INVENTORY_SHAPE_REJECTED")
    if TEST_BRIDGE in {x.get("ifname") for x in links}:
        raise Stop("PROPOSED_TEST_INTERFACE_OCCUPIED")
    names=run(["docker","network","ls","--format","{{.Name}}"]).decode("utf8","strict").splitlines()
    if TEST_NET in names:
        raise Stop("PROPOSED_TEST_DOCKER_NETWORK_OCCUPIED")
    container_names=run(["docker","ps","-a","--format","{{.Names}}"]).decode("utf8","strict").splitlines()
    if TEST_CONTAINER in container_names:
        raise Stop("PROPOSED_TEST_DOCKER_CONTAINER_OCCUPIED")
    tables=parsed(["nft","-j","list","tables"])
    if not isinstance(tables,dict) or not isinstance(tables.get("nftables"),list):
        raise Stop("NFT_TABLE_INVENTORY_REJECTED")
    renderer=load_module(RENDER,"r23d1f_offline_renderer")
    owned_table=renderer.TABLE
    for entry in tables["nftables"]:
        if not isinstance(entry,dict):
            raise Stop("NFT_TABLE_INVENTORY_REJECTED")
        table=entry.get("table")
        if table is not None and not isinstance(table,dict):
            raise Stop("NFT_TABLE_INVENTORY_REJECTED")
        if table and table.get("family")=="inet" and table.get("name")==owned_table:
            raise Stop("PROPOSED_GUARD_TABLE_OCCUPIED")
    return candidate(used_subnets(routes,addrs,networks)),renderer

def syntactic_check(net,renderer):
    # Planned values only. No address is assigned to a host or container.
    hosts=list(net.hosts())
    if len(hosts)<2:
        raise Stop("NO_TWO_USABLE_CANDIDATE_HOSTS")
    policy=renderer.render_policy(
        TEST_BRIDGE,str(net),str(hosts[0]),str(hosts[1]),
        renderer.RELAY_PORT)
    if not isinstance(policy,str) or len(policy)>8192:
        raise Stop("GENERATED_POLICY_UNEXPECTED")
    # A 'check' can interrogate kernel parser, but MUST NOT commit a rule.
    try:
        run(["nft","--check","--file","-"],stdin=policy.encode("ascii"))
    except Stop:
        raise Stop("NFT_SYNTAX_CHECK_FAILED") from None
    return True

REASONS={
    "AUDITED_SOURCE_MISSING":"SOURCE",
    "HOST_TOPOLOGY_OR_PROBE_CLEANUP_FAILED":"BASELINE",
    "INSPECTION_COMMAND_INVALID":"INSPECTION",
    "INSPECTION_TOOL_MISSING":"INSPECTION",
    "HOST_INSPECTION_UNAVAILABLE":"INSPECTION",
    "HOST_INSPECTION_COMMAND_FAILED":"INSPECTION",
    "HOST_INSPECTION_OVERSIZED":"INSPECTION",
    "HOST_INSPECTION_JSON_REJECTED":"INSPECTION",
    "NETWORK_INVENTORY_SHAPE_REJECTED":"INVENTORY",
    "DOCKER_NETWORK_INVENTORY_REJECTED":"INVENTORY",
    "NO_SAFE_CANDIDATE_IN_KNOWN_INVENTORIES":"INVENTORY",
    "PROPOSED_TEST_INTERFACE_OCCUPIED":"COLLISION",
    "PROPOSED_TEST_DOCKER_NETWORK_OCCUPIED":"COLLISION",
    "PROPOSED_TEST_DOCKER_CONTAINER_OCCUPIED":"COLLISION",
    "NFT_TABLE_INVENTORY_REJECTED":"INVENTORY",
    "PROPOSED_GUARD_TABLE_OCCUPIED":"COLLISION",
    "NO_TWO_USABLE_CANDIDATE_HOSTS":"DESIGN",
    "GENERATED_POLICY_UNEXPECTED":"DESIGN",
    "NFT_SYNTAX_CHECK_FAILED":"NFT_PARSER",
}
def main():
    try:
        baseline()
        net,renderer=inspect_candidates()
        syntactic_check(net,renderer)
        print(json.dumps({
            "audit":"R23D1F_NFT_POLICY_PLAN_READONLY",
            "status":"SYNTAX_CHECK_PASS_NOT_ENFORCED",
            "previous_host_topology_and_ephemeral_cleanup_passed":True,
            "candidate_rfc1918_subnet_available_in_visible_route_address_docker_inventory":True,
            "r23d_test_linux_interface_name_unused":True,
            "r23d_test_network_and_container_names_unused":True,
            "reserved_nft_guard_table_name_unused":True,
            "kernel_nft_check_only_passed":True,
            "actual_test_network_created":False,
            "actual_firewall_table_or_rules_installed":False,
            "real_lan_policy_routes_vpn_or_future_docker_conflicts_ruled_out":False,
            "firewall_effective_egress_isolation_verified":False,
            "jellyfin_session_allowed":False,
            "docker_firewall_nginx_aio_or_watch_history_changes":0,
            "next":"DESIGN_REVERSIBLE_SYNTHETIC_APPLY_AND_ROLLBACK_TO_TEST_SELECTED_BRIDGE",
        },indent=2))
        print("R23D1F_READONLY_NFT_SYNTAX_PASS")
    except Exception as err:
        reason=err.args[0] if isinstance(err,Stop) and len(err.args)==1 else ""
        if reason not in REASONS:
            reason="UNCLASSIFIED_READONLY_PLAN_FAILURE"
        print(json.dumps({
            "audit":"R23D1F_SAFE_DIAGNOSTIC","status":"STOP",
            "stage":REASONS.get(reason,"UNCLASSIFIED"),
            "reason":reason,
            "firewall_effective_egress_isolation_verified":False,
            "docker_firewall_nginx_aio_or_watch_history_changes":0,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
