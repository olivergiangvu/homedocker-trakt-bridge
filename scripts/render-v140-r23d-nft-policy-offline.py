#!/usr/bin/env python3
"""R23D-1E STATIC DESIGN ONLY: nftables R23D interface-scoped deny guard.

THIS FILE CANNOT APPLY FIREWALL RULES. No subprocess, Docker, shell, nft
invocation, real firewall read, or host network discovery. The CLI emits one
fully synthetic design fixture only (addresses intentionally not from host).

R23D host evidence: nft family ip forward priority 0 default drop, no nft
INPUT base chain, ip6 forward priority 0 default accept; iptables INPUT
default ACCEPT with 0 rules. Previous synthetic Docker bridge could reach
2/5 host ports. A Docker FORWARD/DOCKER-USER-only filter cannot restrict
container to host INPUT. Hence a dedicated inet table matching the NEW
Docker bridge's Linux interface name for BOTH input and forward.

Do not use this file as a production firewall installer. Without live bridge
ifname/IP verification, hook/packet path tests and independent rollback,
this template provides neither effective isolation nor an authorized relay.
"""
import ipaddress
import json
import re

TABLE = "homedocker_r23d_guard"
BRIDGE_RE = re.compile(r"^br-r23d[a-z0-9]{2,7}$")
PRIORITY = -20
RELAY_PORT = 17420
SYNTHETIC = {
    "bridge": "br-r23dgate",
    "subnet": "172.31.254.0/28",
    "host_gateway": "172.31.254.1",
    "canary": "172.31.254.2",
    "relay_port": RELAY_PORT,
}

class Stop(Exception):
    pass

def validate(bridge, subnet, host_gateway, canary, relay_port):
    if not isinstance(bridge,str) or not BRIDGE_RE.fullmatch(bridge):
        raise Stop("R23D_BRIDGE_NAME_NOT_SCOPED")
    if type(relay_port) is not int or relay_port!=RELAY_PORT:
        raise Stop("UNEXPECTED_RELAY_PORT")
    try:
        net = ipaddress.ip_network(subnet,strict=True)
        gw = ipaddress.ip_address(host_gateway)
        target = ipaddress.ip_address(canary)
    except ValueError:
        raise Stop("INVALID_IPV4_NETWORK") from None
    rfc1918 = (ipaddress.ip_network("10.0.0.0/8"),
               ipaddress.ip_network("172.16.0.0/12"),
               ipaddress.ip_network("192.168.0.0/16"))
    if (net.version!=4 or not any(net.subnet_of(parent) for parent in rfc1918)
        or net.prefixlen<24 or net.prefixlen>29 or gw.version!=4 or target.version!=4
        or gw not in net.hosts() or target not in net.hosts() or gw==target):
        raise Stop("INVALID_IPV4_NETWORK")
    if not isinstance(host_gateway,str) or str(gw)!=host_gateway or \
       not isinstance(canary,str) or str(target)!=canary:
        raise Stop("NONCANONICAL_HOST_ADDRESS")
    return bridge, str(gw), str(target)

def render_policy(bridge, subnet, host_gateway, canary, relay_port):
    bridge,gw,target=validate(bridge,subnet,host_gateway,canary,relay_port)
    # Every DROP is interface-scoped to the exclusively reserved new bridge.
    # All traffic on other network interfaces is unaffected by THIS table.
    # ACCEPT can still be rejected by some OTHER chain at this hook.
    # Unknown bridge interface or bypassing netfilter would make protection
    # ineffective: active, credential-free TCP verification remains essential.
    return (
f"""table inet {TABLE} {{
    chain r23d_input {{
        type filter hook input priority {PRIORITY}; policy accept;
        iifname "{bridge}" ip saddr {target} ip daddr {gw} tcp dport {relay_port} accept
        iifname "{bridge}" drop
    }}
    chain r23d_forward {{
        type filter hook forward priority {PRIORITY}; policy accept;
        iifname "{bridge}" drop
    }}
}}
"""
    )

def offline_fixture_report():
    template=render_policy(**SYNTHETIC)
    return {
        "audit":"R23D1E_OFFLINE_POLICY_DESIGN",
        "status":"SYNTHETIC_FIXTURE_ONLY_NO_HOST_CHANGES",
        "source":"HOST_OBSERVED_HOOKS_R23D1D",
        "table_family":"inet",
        "table_name":TABLE,
        "filter_base_chains":[
            {"hook":"input","priority":PRIORITY,"policy":"accept",
             "intended_effect":"ALLOW_CANARY_TO_EXACT_HOST_RELAY_ONLY_AND_DROP_OTHER_BRIDGE_INPUT"},
            {"hook":"forward","priority":PRIORITY,"policy":"accept",
             "intended_effect":"DROP_ALL_OTHER_NEW_BRIDGE_FORWARD_EGRESS"},
        ],
        "fixture_bridge":SYNTHETIC["bridge"],
        "fixture_only":True,
        "policy_text":template,
        "guard_specificity_checked_in_unit_tests":True,
        "nft_kernel_syntax_check_run":False,
        "host_bridge_interface_or_addresses_verified":False,
        "real_firewall_order_or_behavior_verified":False,
        "network_egress_firewall_verified":False,
        "jellyfin_session_or_relay_allowed":False,
        "docker_firewall_nginx_aio_history_writes":0,
        "required_rollback_sequence":[
            "STOP_EXACT_NEW_R23D_GATEWAY_CANARY_FIRST",
            "STOP_EXACT_HOST_RELAY_SECOND",
            "REMOVE_EXACT_NEW_R23D_NETWORK_THIRD_ONLY_AFTER_PEERS_GONE",
            "DELETE_ONLY_ATTESTED_OWNED_NFT_TABLE_LAST",
            "VERIFY_RC6_R23B_R23A_AND_CLIENT_ROUTES_UNCHANGED",
        ],
        "next":"VERIFY_EXACT_NETWORK_INTERFACE_AND_WRITE_INDEPENDENT_REVERSIBLE_SYNTHETIC_TEST",
    }

if __name__=="__main__":
    print(json.dumps(offline_fixture_report(), indent=2))
