#!/usr/bin/env python3
"""R23D-1A: read-only HomeDocker firewall capability survey, NO egress PASS.

Requires the previously qualified R23D-0 topology to remain valid.
No privileged network or Docker changes, no new containers, no login, no
AIO history/GET, no secret/header/database reads. Probe only capability and
presence of firewall control paths; do NOT inspect or print rule addresses.
Actual new bridge cannot be certified until a synthetic, zero-credential
active egress test has been deployed and independently reviewed.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PRECHECK = HERE / "audit-v140-r23d-host-preflight.py"

class Stop(Exception):
    pass

def verify_precheck():
    if not PRECHECK.is_file() or PRECHECK.is_symlink():
        raise Stop("R23D0_PREFLIGHT_SOURCE_MISSING")
    spec = importlib.util.spec_from_file_location("r23d0_qualified_guard", PRECHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        result = mod.audit()
    except (mod.Stop, OSError, ValueError, TypeError, KeyError):
        raise Stop("R23D0_PREFLIGHT_NO_LONGER_VALID") from None
    if result.get("status") != "PREFLIGHT_PASS" or result.get("production_modifications") != 0:
        raise Stop("R23D0_PREFLIGHT_NO_LONGER_VALID")
    return True

def probe(name, *args):
    binary = shutil.which(name)
    if not binary:
        return None, ""
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True,
                                timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False, ""
    # Never output raw command stdout/stderr: they may contain private network
    # addresses, other service names, policy rules or paths.
    return result.returncode == 0, result.stdout[:4096] if result.returncode == 0 else ""

def firewall_capabilities():
    iptables_v, iptables_version = probe("iptables", "--version")
    nft_v, _ = probe("nft", "--version")
    ip6tables_v, _ = probe("ip6tables", "--version")
    ip_v, _ = probe("ip", "-Version")
    do_user, _ = probe("iptables", "-S", "DOCKER-USER") if iptables_v else (False, "")
    forward, _ = probe("iptables", "-S", "FORWARD") if iptables_v else (False, "")
    nft_tables, _ = probe("nft", "list", "tables") if nft_v else (False, "")
    ipv6_default, ipv6_route = probe("ip", "-6", "route", "show", "default") if ip_v else (False, "")
    sys_ipv4, forward4 = probe("sysctl", "-n", "net.ipv4.ip_forward")
    sys_ipv6, forward6 = probe("sysctl", "-n", "net.ipv6.conf.all.forwarding")
    v4_routing = forward4.strip() == "1" if sys_ipv4 else None
    v6_routing = forward6.strip() == "1" if sys_ipv6 else None

    backend = "NOT_DETERMINED"
    if iptables_v:
        if "nf_tables" in iptables_version:
            backend = "IPTABLES_NFT_COMPATIBILITY"
        elif "legacy" in iptables_version:
            backend = "IPTABLES_LEGACY"
        else:
            backend = "IPTABLES_VERSION_UNKNOWN"
    return {
        "audit": "R23D1A_READ_ONLY_FIREWALL_CAPABILITY",
        "status": "CAPABILITY_SURVEY_COMPLETE_NOT_EGRESS_VERIFIED",
        "r23d0_topology_still_passed": True,
        "iptables_command_available": bool(iptables_v),
        "iptables_backend_hint": backend,
        "docker_user_chain_readable": bool(do_user),
        "forward_chain_readable": bool(forward),
        "nft_command_available": bool(nft_v),
        "nft_tables_readable": bool(nft_tables),
        "ip6tables_command_available": bool(ip6tables_v),
        "ipv4_kernel_forwarding_enabled": v4_routing,
        "ipv6_kernel_forwarding_enabled": v6_routing,
        "ipv6_default_route_present": bool(ipv6_route.strip()) if ipv6_default else None,
        "new_r23d_bridge_exists": False,
        "new_canary_created": False,
        "actual_egress_probes_performed": False,
        "network_egress_firewall_verified": False,
        "aio_login_or_authenticated_get": False,
        "nginx_docker_firewall_or_history_writes": 0,
        "secrets_routes_ips_rules_or_hostnames_printed": False,
        "next": "SYNTHETIC_ZERO_CREDENTIAL_EGRESS_TEST_REQUIRED_BEFORE_REAL_AIO_TOKEN",
    }

def main():
    try:
        verify_precheck()
        result = firewall_capabilities()
        print(json.dumps(result, indent=2))
        print("R23D1A_CAPABILITY_SURVEY_COMPLETE")
    except Exception:
        # Diagnostics are a fixed envelope; never copy raw command/system text.
        print(json.dumps({
            "audit": "R23D1A_SAFE_DIAGNOSTIC",
            "status": "STOP",
            "reason": "R23D0_PREFLIGHT_OR_HOST_CAPABILITY_REJECTED",
            "network_egress_firewall_verified": False,
            "production_modifications": 0,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
