#!/usr/bin/env python3
"""R23D-1D: read-only netfilter base-chain hooks and priorities review.

Re-check R23D-0 and disposable R23D-1B cleanup via the previously qualified
R23D-1C module. Read nftables ruleset in memory; emit ONLY normalized family,
hook, numeric priority, policy and counts. Never emit names, rules, expressions,
interface names, IPs, MACs, usernames, secrets or actual nft JSON.

This is an inventory, NOT a firewall packet-path qualification; iptables-nft
compatibility and non-nft/legacy hook users may not be fully reflected. Never
report that egress is blocked or start a real AIO session.
"""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
GUARD=HERE/"audit-v140-r23d-host-policy-readonly.py"
FAMILIES={"inet","ip","ip6","bridge","netdev","arp"}
HOOKS={"prerouting","input","forward","output","postrouting","ingress","egress"}
POLICIES={"accept","drop"}
MAX_BYTES=4*1024*1024
MAX_BASE_CHAINS=256

class Stop(Exception):
    pass

def baseline():
    if not GUARD.is_file() or GUARD.is_symlink():
        raise Stop("R23D1C_HOST_BASELINE_MISSING")
    try:
        spec=importlib.util.spec_from_file_location("r23d1d_guard",GUARD)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.baseline() is not True:
            raise Stop("R23D1C_HOST_BASELINE_FAILED")
    except Exception:
        raise Stop("R23D1C_HOST_BASELINE_FAILED") from None

def read_nft_ruleset():
    exe=shutil.which("nft")
    if exe is None:
        raise Stop("NFT_COMMAND_UNAVAILABLE")
    try:
        p=subprocess.run([exe,"-j","list","ruleset"],capture_output=True,
                         check=False,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("NFT_JSON_INSPECTION_FAILED") from None
    if p.returncode or len(p.stdout)>MAX_BYTES:
        raise Stop("NFT_JSON_INSPECTION_FAILED")
    try:
        decoded=json.loads(p.stdout)
    except (ValueError,UnicodeError):
        raise Stop("NFT_JSON_INSPECTION_FAILED") from None
    return decoded

def priority(value):
    # Strictly normalize only known kernel numeric prio or conventional
    # nft policy aliases; newer unknown syntax gets an opaque unknown bucket,
    # never a forged numeric value.
    if type(value) is int and -500<=value<=500:
        return value
    if isinstance(value,str):
        aliases={"raw":-300,"mangle":-150,"dstnat":-100,
                 "filter":0,"security":50,"srcnat":100}
        if value in aliases:
            return aliases[value]
        match=re.fullmatch(r"(-?\d+)",value)
        if match and -500<=int(value)<=500:
            return int(value)
    return "UNKNOWN"

def summarize(obj):
    if not isinstance(obj,dict) or not isinstance(obj.get("nftables"),list):
        raise Stop("NFT_JSON_STRUCTURE_UNSUPPORTED")
    summary={}
    table_count=0
    nonbase=0
    base=0
    unknown=0
    for elem in obj["nftables"]:
        if not isinstance(elem,dict):
            raise Stop("NFT_JSON_STRUCTURE_UNSUPPORTED")
        if "table" in elem:
            table_count+=1
        chain=elem.get("chain")
        if chain is None:
            continue
        if not isinstance(chain,dict):
            raise Stop("NFT_JSON_STRUCTURE_UNSUPPORTED")
        if "hook" not in chain:
            nonbase+=1
            continue
        base+=1
        if base>MAX_BASE_CHAINS:
            raise Stop("NFT_BASE_CHAIN_LIMIT_EXCEEDED")
        family=chain.get("family")
        hook=chain.get("hook")
        policy=chain.get("policy","UNKNOWN")
        prio=priority(chain.get("prio"))
        if family not in FAMILIES:
            family="UNKNOWN"
        if hook not in HOOKS:
            hook="UNKNOWN"
        if policy not in POLICIES:
            policy="UNKNOWN"
        if family=="UNKNOWN" or hook=="UNKNOWN" or policy=="UNKNOWN" or prio=="UNKNOWN":
            unknown+=1
        key=(str(family),str(hook),str(prio),str(policy))
        summary[key]=summary.get(key,0)+1
    groups=[
      {"family":fam,"hook":hook,"priority":int(prio) if prio!="UNKNOWN" else prio,
       "policy":pol,"chains":qty}
      for (fam,hook,prio,pol),qty in sorted(summary.items())
    ]
    return {
        "audit":"R23D1D_READ_ONLY_NFT_HOOK_PRIORITY_MAP",
        "status":"OBSERVED_NOT_EGRESS_QUALIFIED",
        "r23d0_baseline_and_ephemeral_cleanup_passed":True,
        "nft_table_count":table_count,
        "base_chains_total":base,
        "regular_chains_total":nonbase,
        "base_chains_with_unrecognized_fields":unknown,
        "base_chain_aggregates":groups,
        "hook_priority_observation_is_not_rule_order_certification":True,
        "iptables_compatibility_and_other_kernel_paths_not_certified":True,
        "firewall_effective_egress_isolation_verified":False,
        "gateway_user_session_allowed":False,
        "raw_names_rules_interfaces_ips_pids_or_secrets_printed":False,
        "docker_firewall_nginx_aio_watch_history_writes":0,
        "next":"REVIEW_SCOPED_NFT_INPUT_FORWARD_DENY_POLICY_AND_ROLLBACK_OFFLINE",
    }

REASONS={
    "R23D1C_HOST_BASELINE_MISSING":"BASELINE",
    "R23D1C_HOST_BASELINE_FAILED":"BASELINE",
    "NFT_COMMAND_UNAVAILABLE":"NETFILTER",
    "NFT_JSON_INSPECTION_FAILED":"NETFILTER",
    "NFT_JSON_STRUCTURE_UNSUPPORTED":"NETFILTER",
    "NFT_BASE_CHAIN_LIMIT_EXCEEDED":"NETFILTER",
}

def main():
    try:
        baseline()
        print(json.dumps(summarize(read_nft_ruleset()),indent=2))
        print("R23D1D_NFT_HOOK_READONLY_OBSERVATION_COMPLETE")
    except Exception as exc:
        reason=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        if reason not in REASONS:
            reason="UNKNOWN_READONLY_INSPECTION_FAILURE"
        print(json.dumps({
            "audit":"R23D1D_SAFE_DIAGNOSTIC",
            "status":"STOP",
            "stage":REASONS.get(reason,"UNCLASSIFIED"),
            "reason":reason,
            "firewall_effective_egress_isolation_verified":False,
            "docker_firewall_nginx_aio_watch_history_writes":0,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
