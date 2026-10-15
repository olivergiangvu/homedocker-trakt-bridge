#!/usr/bin/env python3
"""R23D-1C: inspect-only Docker-host firewall policy SURFACE, not enforcement.

The host's R23D-0 baseline must still pass; both ephemeral R23D-1B probe
resources MUST be absent. This only reads iptables filter chains, nft table
presence and local listening sockets. Outputs ONLY fixed chain/policy categories,
counts and binding classes for the five previously advertised TCP probe ports.
Never exposes real IPs, chains' rule bodies, usernames, tokens, logs or paths.

NO docker/network/firewall/nginx writes, no AIO GET or credentials. This DOES
NOT qualify effective INPUT/FORWARD rule order, conntrack, IPv6, host port
exposure or R23D egress isolation. All user-session relay remains BLOCKED.
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
PREFLIGHT=HERE/"audit-v140-r23d-host-preflight.py"
PROBE_CONTAINER="homedocker-v140-r23d-egress-probe"
PROBE_NETWORK="homedocker_v140_r23d_egress_probe"
PORTS=(22,80,9000,3240,7000)
CHAINS=("INPUT","FORWARD","DOCKER-USER")

class Stop(Exception):
    pass

def cmd(name,*args):
    binary=shutil.which(name)
    if binary is None:
        return None
    try:
        r=subprocess.run([binary,*args],capture_output=True,text=True,
                         check=False,timeout=10)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    # Never print raw stderr, rules, interface/IP addresses, or stdout.
    if r.returncode != 0 or len(r.stdout)>262144:
        return None
    return r.stdout

def docker_object_exists(kind,name):
    try:
        p=subprocess.run(["docker",kind,"inspect",name],capture_output=True,
                         check=False,timeout=10)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("DOCKER_INSPECTION_UNAVAILABLE") from None
    if p.returncode == 0:
        return True
    # Distinguish a missing object from a broken Docker daemon.
    # Docker inspect uses exit code 1 for both; use a second read-only
    # listing to assert that the daemon is responsive.
    arg=("container","ls","-a","--format","{{.Names}}") if kind=="container" \
        else ("network","ls","--format","{{.Name}}")
    listing=cmd("docker",*arg)
    if listing is None:
        raise Stop("DOCKER_INSPECTION_UNAVAILABLE")
    return name in listing.splitlines()

def baseline():
    if not PREFLIGHT.is_file() or PREFLIGHT.is_symlink():
        raise Stop("HOST_TOPOLOGY_FAILED")
    try:
        spec=importlib.util.spec_from_file_location("r23d1c_base",PREFLIGHT)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out=mod.audit()
        if out.get("status")!="PREFLIGHT_PASS":
            raise Stop("HOST_TOPOLOGY_FAILED")
    except Exception:
        raise Stop("HOST_TOPOLOGY_FAILED") from None
    if docker_object_exists("container",PROBE_CONTAINER) or \
       docker_object_exists("network",PROBE_NETWORK):
        raise Stop("EPHEMERAL_PROBE_CLEANUP_NOT_CONFIRMED")
    return True

def summarize_chain(raw, chain):
    if raw is None:
        return {
            "readable":False,"default_policy":"UNAVAILABLE",
            "rule_count":None,"terminal_accept_rules":None,
            "terminal_drop_or_reject_rules":None,
            "direct_docker_user_jump_present":None,
        }
    text=raw.splitlines()
    allowed={"ACCEPT","DROP","REJECT","RETURN"}
    policy="UNSET_OR_CUSTOM"
    prefix="-P "+chain+" "
    for line in text:
        if line.startswith(prefix):
            candidate=line[len(prefix):].strip()
            policy=candidate if candidate in allowed else "OTHER"
            break
    rules=[line for line in text if line.startswith("-A "+chain+" ")]
    def jumps_to(line,targets):
        return bool(re.search(r"(?:^|\s)-j\s+(?:"+ "|".join(targets) + r")(?:\s|$)",line))
    return {
        "readable":True,
        "default_policy":policy,
        "rule_count":len(rules),
        "terminal_accept_rules":sum(jumps_to(x,("ACCEPT",)) for x in rules),
        "terminal_drop_or_reject_rules":sum(jumps_to(x,("DROP","REJECT")) for x in rules),
        "direct_docker_user_jump_present":any(jumps_to(x,("DOCKER-USER",)) for x in rules),
    }

def binding_category(host):
    h=host.strip("[]")
    if h in ("*","0.0.0.0"):
        return "IPV4_WILDCARD"
    if h in ("::","[::]"):
        return "IPV6_WILDCARD"
    try:
        ip=ipaddress.ip_address(h.split("%",1)[0])
        return "IPV4_LOOPBACK" if ip.version==4 and ip.is_loopback else \
            "IPV6_LOOPBACK" if ip.version==6 and ip.is_loopback else \
            "IPV4_HOST_INTERFACE" if ip.version==4 else "IPV6_HOST_INTERFACE"
    except ValueError:
        return "UNKNOWN_BIND_ADDRESS"

def summarize_listeners(raw):
    result={str(p):{"listening":False,"binding_classes":[]} for p in PORTS}
    if raw is None:
        return {str(p):{"listening":None,"binding_classes":["UNAVAILABLE"]} for p in PORTS}
    for line in raw.splitlines():
        fields=line.split()
        if len(fields)<5:
            continue
        local=fields[3]
        try:
            host,port=local.rsplit(":",1)
            port=int(port)
        except (ValueError,TypeError):
            continue
        if port not in PORTS:
            continue
        key=str(port)
        result[key]["listening"]=True
        cls=binding_category(host)
        if cls not in result[key]["binding_classes"]:
            result[key]["binding_classes"].append(cls)
    for val in result.values():
        val["binding_classes"].sort()
    return result

def inspect_policy():
    chains={name:summarize_chain(cmd("iptables","-S",name),name) for name in CHAINS}
    nft=cmd("nft","list","tables")
    ss=cmd("ss","-H","-ltn")
    return {
        "audit":"R23D1C_READ_ONLY_HOST_INPUT_FORWARD_REVIEW",
        "status":"OBSERVED_NOT_EGRESS_QUALIFIED",
        "r23d0_topology_still_passed":True,
        "ephemeral_probe_container_and_network_absent":True,
        "iptables_filter_chains":chains,
        "native_nft_table_listing_readable":nft is not None,
        "native_nft_table_count":len([x for x in nft.splitlines()
            if x.strip().startswith("table ")]) if nft is not None else None,
        "fixed_probe_host_tcp_listener_bindings":summarize_listeners(ss),
        "listener_binding_is_not_container_tcp_reachability":True,
        "iptables_rule_counts_are_not_effective_firewall_semantics":True,
        "input_and_forward_rule_order_or_conntrack_qualified":False,
        "actual_egress_test_with_enforced_policy_performed":False,
        "network_egress_firewall_verified":False,
        "gateway_user_session_allowed":False,
        "raw_firewall_rules_host_ips_pids_or_credentials_printed":False,
        "docker_firewall_nginx_aio_or_watch_state_modifications":0,
        "next":"DESIGN_RULE_ORDER_IN_HOST_INPUT_AND_FORWARD_THEN_SYNTHETIC_RETEST_BEFORE_SESSION",
    }

STAGES={
    "HOST_TOPOLOGY_FAILED":"BASELINE",
    "EPHEMERAL_PROBE_CLEANUP_NOT_CONFIRMED":"CLEANUP",
    "DOCKER_INSPECTION_UNAVAILABLE":"INSPECTION",
}
def main():
    try:
        baseline()
        print(json.dumps(inspect_policy(),indent=2,sort_keys=True))
        print("R23D1C_READONLY_POLICY_OBSERVATION_COMPLETE")
    except Exception as exc:
        reason=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        if reason not in STAGES:
            reason="UNCLASSIFIED_READONLY_AUDIT_FAILURE"
        print(json.dumps({
            "audit":"R23D1C_SAFE_DIAGNOSTIC","status":"STOP",
            "reason":reason,
            "stage":STAGES.get(reason,"UNCLASSIFIED"),
            "network_egress_firewall_verified":False,
            "docker_firewall_nginx_or_history_modifications":0,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
