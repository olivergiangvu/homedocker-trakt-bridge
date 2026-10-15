#!/usr/bin/env python3
"""R23D-2A SOURCE-ONLY structural nft policy attestation and sticky runtime gate.

A temporary R23D-1G TCP probe passed on HomeDocker, but its nft table was
removed before returning. No persistent R23D network/guard or live AIO relay
exists. This module DOES NOT inspect Docker, call nft, create firewall rules,
login to AIO or authorize a session. Feed it an exact previously observed
nft -j list table snapshot and authenticated, independently inspected bridge
network identity from a future source-pinned canary. Any unexpected nft JSON
expression, extra rule, changed chain or interface MUST fail closed.

The lifetime monitor is a source component, NOT an active supervisor until
its verifier is wired to immutable Docker+guard state and the GET relay, with
an independently supervised cleanup/kill path. A periodic check has unavoidable
races; it is not a substitute for kernel-enforced policy.
"""
import importlib.util
import json
from pathlib import Path
import threading

HERE=Path(__file__).resolve().parent
RENDER=HERE/"render-v140-r23d-nft-policy-offline.py"

class Stop(Exception):
    pass

def renderer():
    if not RENDER.is_file() or RENDER.is_symlink():
        raise Stop("EXPECTED_POLICY_RENDERER_MISSING")
    spec=importlib.util.spec_from_file_location("r23d2a_expected_policy", RENDER)
    if not spec or not spec.loader:
        raise Stop("EXPECTED_POLICY_RENDERER_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def match(left,right):
    return {"match":{"op":"==","left":left,"right":right}}

def meta_iif(bridge):
    return match({"meta":{"key":"iifname"}},bridge)

def ip_addr(field,value):
    return match({"payload":{"protocol":"ip","field":field}},value)

def tcp_port(port):
    return match({"payload":{"protocol":"tcp","field":"dport"}},port)

def expected_rules(bridge,gw,canary,relay_port):
    in_allowed=[
        meta_iif(bridge),ip_addr("saddr",canary),ip_addr("daddr",gw),
        tcp_port(relay_port),{"accept":None}
    ]
    in_denied=[meta_iif(bridge),{"drop":None}]
    fwd_denied=[meta_iif(bridge),{"drop":None}]
    return {"r23d_input":[in_allowed,in_denied],
            "r23d_forward":[fwd_denied]}

def check_guard(nft_doc,*,bridge,subnet,host_gateway,canary,relay_port):
    """Return True only for the EXACT expected independent owned guard.

    This checks nft structural semantics, not full packet path, Docker
    network membership, output hook, IPv6 routing, running process or lifetime.
    """
    expected=renderer()
    try:
        bridge,gw,canary=expected.validate(
            bridge,subnet,host_gateway,canary,relay_port)
    except Exception:
        raise Stop("GUARD_EXPECTED_NETWORK_SCOPE_INVALID") from None
    if (not isinstance(nft_doc,dict)
        or set(nft_doc)!={"nftables"}
        or not isinstance(nft_doc["nftables"],list)
        or len(nft_doc["nftables"])>20):
        raise Stop("NFT_GUARD_DOCUMENT_REJECTED")
    table=expected.TABLE
    base={}
    rules={"r23d_input":[],"r23d_forward":[]}
    table_seen=0
    for item in nft_doc["nftables"]:
        if not isinstance(item,dict) or len(item)!=1:
            raise Stop("NFT_GUARD_DOCUMENT_REJECTED")
        if "metainfo" in item:
            if not isinstance(item["metainfo"],dict):
                raise Stop("NFT_GUARD_DOCUMENT_REJECTED")
            continue
        if "table" in item:
            value=item["table"]
            if (not isinstance(value,dict)
                or value.get("family")!="inet"
                or value.get("name")!=table):
                raise Stop("NFT_GUARD_TABLE_CHANGED")
            table_seen+=1
            continue
        if "chain" in item:
            value=item["chain"]
            if not isinstance(value,dict):
                raise Stop("NFT_GUARD_CHAIN_CHANGED")
            name=value.get("name")
            if name not in rules or name in base:
                raise Stop("NFT_GUARD_CHAIN_CHANGED")
            hook="input" if name=="r23d_input" else "forward"
            if (value.get("family")!="inet"
                or value.get("table")!=table
                or value.get("type")!="filter"
                or value.get("hook")!=hook
                or value.get("prio")!=expected.PRIORITY
                or value.get("policy")!="accept"
                or value.get("dev") is not None
                or value.get("flags") not in (None, [])):
                raise Stop("NFT_GUARD_CHAIN_CHANGED")
            base[name]=True
            continue
        if "rule" in item:
            value=item["rule"]
            if not isinstance(value,dict):
                raise Stop("NFT_GUARD_RULE_CHANGED")
            name=value.get("chain")
            if (name not in rules
                or value.get("family")!="inet"
                or value.get("table")!=table
                or not isinstance(value.get("expr"),list)):
                raise Stop("NFT_GUARD_RULE_CHANGED")
            # Reject rule-level injection or table changes. Handle is a
            # kernel-generated numeric identifier, not part of semantics.
            if any(key not in ("family","table","chain","handle","expr")
                   for key in value):
                raise Stop("NFT_GUARD_RULE_CHANGED")
            if len(rules[name])>=2:
                raise Stop("NFT_GUARD_RULE_CHANGED")
            rules[name].append(value["expr"])
            continue
        # A set/map/counter/flowtable/other object changes the policy's
        # interpretation or is outside the independent guard contract.
        raise Stop("NFT_GUARD_UNEXPECTED_OBJECT")
    if table_seen!=1 or set(base)!={"r23d_input","r23d_forward"}:
        raise Stop("NFT_GUARD_REQUIRED_CHAIN_MISSING")
    if rules!=expected_rules(bridge,gw,canary,relay_port):
        raise Stop("NFT_GUARD_RULE_CHANGED")
    return True

class GuardLifetimeGate:
    """Closed until initial verification; permanently closed on ANY failure.

    The caller must wire both ongoing verification and an independent host
    process-kill path. This gate is not connected to the relay today.
    """
    def __init__(self, verify, on_loss, interval_seconds=2.0):
        if not callable(verify) or not callable(on_loss):
            raise ValueError("verification and loss handler required")
        if not isinstance(interval_seconds,(int,float)) or isinstance(interval_seconds,bool) \
                or not 0.1<=interval_seconds<=30:
            raise ValueError("invalid guard monitor interval")
        self._verify=verify
        self._on_loss=on_loss
        self._interval=interval_seconds
        self._lock=threading.Lock()
        self._halt=threading.Event()
        self._state="CLOSED"
        self._started=False
        self._worker=None
        self._loss_signaled=False

    @property
    def state(self):
        with self._lock:
            return self._state

    def observe(self):
        """Fresh verification for every request, not only periodic poll."""
        with self._lock:
            if not self._started or self._state=="FAILED" or self._halt.is_set():
                return False
            try:
                # Only literal True qualifies. False/None/exceptions are STOP.
                qualified=self._verify() is True
            except BaseException:
                qualified=False
            if qualified:
                self._state="READY"
                return True
            self._state="FAILED"
            notify=not self._loss_signaled
            self._loss_signaled=True
        if notify:
            try:
                self._on_loss()
            except BaseException:
                pass
        return False

    def start(self):
        with self._lock:
            if self._started or self._halt.is_set():
                raise Stop("GUARD_MONITOR_ALREADY_STARTED_OR_CLOSED")
            self._started=True
        if not self.observe():
            raise Stop("PERSISTENT_NFT_GUARD_NOT_ATTESTED")
        with self._lock:
            worker=threading.Thread(target=self._poll,daemon=True,
                                    name="r23d-nft-guard-watchdog")
            self._worker=worker
        worker.start()
        return True

    def _poll(self):
        while not self._halt.wait(self._interval):
            if not self.observe():
                return

    def close(self):
        self._halt.set()
        with self._lock:
            self._state="CLOSED" if self._state!="FAILED" else "FAILED"
            worker=self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3)

def report():
    return {
        "audit":"R23D2A_PERSISTENT_GUARD_SOURCE_ONLY",
        "status":"PARSER_AND_MONITOR_NOT_CONNECTED_TO_LIVE_RELAY",
        "exact_nft_input_forward_chain_rule_scope_required":True,
        "sticky_deny_on_guard_mutation":True,
        "periodic_plus_per_request_checks_required":True,
        "runtime_docker_identity_and_nft_fetcher_wired":False,
        "kernel_firewall_enforcement_verified":False,
        "lifetime_supervisor_and_emergency_kill_wired":False,
        "live_aio_session_allowed":False,
        "docker_firewall_nginx_aio_or_history_writes":0,
    }

if __name__=="__main__":
    print(json.dumps(report(),indent=2))
