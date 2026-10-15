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
import re
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

FINAL_NETWORK="homedocker_v140_r23d_local"
FINAL_CONTAINER="homedocker-v140-r23d-gateway-canary"
FINAL_PHASE="r23d-persistent-canary"
EXPECTED_GATEWAY_HOST_PORT="17145"
EXPECTED_GATEWAY_INNER_PORT="7101/tcp"
IMAGE_SHA_RE=re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_TAG_RE=re.compile(r"^homedocker-trakt-bridge:v140-r23d-[0-9a-f]{12}$")

def check_final_docker_scope(container_doc,network_doc,*,bridge,subnet,
                             host_gateway,canary,expected_image_id):
    """Pure final-network verification. NO Docker engine or host access.

    Tests must use full Docker inspect JSON, not simulated summary counters.
    This does not verify symlink/file content or that the nft guard is present;
    check_guard MUST also pass on fresh kernel nft JSON.
    """
    expected=renderer()
    try:
        expected.validate(bridge,subnet,host_gateway,canary,expected.RELAY_PORT)
    except Exception:
        raise Stop("DOCKER_GUARD_NETWORK_SCOPE_INVALID") from None
    if not IMAGE_SHA_RE.fullmatch(str(expected_image_id)):
        raise Stop("DOCKER_EXPECTED_IMAGE_ID_INVALID")
    if (not isinstance(container_doc,list) or len(container_doc)!=1
        or not isinstance(container_doc[0],dict)
        or not isinstance(network_doc,list) or len(network_doc)!=1
        or not isinstance(network_doc[0],dict)):
        raise Stop("DOCKER_GUARD_INSPECT_DOCUMENT_REJECTED")
    obj=container_doc[0]
    net=network_doc[0]
    conf=obj.get("Config") or {}
    host=obj.get("HostConfig") or {}
    settings=(obj.get("NetworkSettings") or {}).get("Networks") or {}
    expected_labels={"homedocker.phase":FINAL_PHASE}
    if (obj.get("Name")!="/"+FINAL_CONTAINER
        or not obj.get("State",{}).get("Running")
        or obj.get("Image")!=expected_image_id
        or not IMAGE_TAG_RE.fullmatch(str(conf.get("Image")))
        or conf.get("User") not in ("node","1000","1000:1000")
        or not all((conf.get("Labels") or {}).get(k)==v for k,v in expected_labels.items())
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or (host.get("CapAdd") or [])
        or "ALL" not in [str(x).upper() for x in host.get("CapDrop") or []]
        or not any("no-new-privileges" in str(x) for x in host.get("SecurityOpt") or [])
        or host.get("NetworkMode")!=FINAL_NETWORK
        or set(settings)!={FINAL_NETWORK}
        or settings[FINAL_NETWORK].get("IPAddress")!=canary):
        raise Stop("DOCKER_FINAL_CANARY_SCOPE_CHANGED")
    bindings=host.get("PortBindings") or {}
    if (set(bindings)!={EXPECTED_GATEWAY_INNER_PORT}
        or bindings[EXPECTED_GATEWAY_INNER_PORT]!=[
            {"HostIp":"127.0.0.1","HostPort":EXPECTED_GATEWAY_HOST_PORT}
        ]):
        raise Stop("DOCKER_FINAL_CANARY_PUBLISH_CHANGED")
    mounts=obj.get("Mounts") or []
    if (not isinstance(mounts,list) or len(mounts)!=1
        or mounts[0].get("Type")!="bind"
        or mounts[0].get("Destination")!="/run/identity-proof.json"
        or mounts[0].get("RW") is not False):
        raise Stop("DOCKER_FINAL_CANARY_PROOF_MOUNT_CHANGED")
    opt=net.get("Options") or {}
    if (net.get("Name")!=FINAL_NETWORK
        or net.get("Driver")!="bridge"
        or net.get("Internal") is not False
        or net.get("EnableIPv6") is not False
        or opt.get("com.docker.network.bridge.name")!=bridge
        or opt.get("com.docker.network.bridge.enable_ip_masquerade")!="false"
        or not all((net.get("Labels") or {}).get(k)==v for k,v in expected_labels.items())
        or (net.get("IPAM") or {}).get("Config")!=[
            {"Subnet":subnet,"Gateway":host_gateway}
        ]):
        raise Stop("DOCKER_FINAL_NETWORK_SCOPE_CHANGED")
    peers=net.get("Containers") or {}
    container_id=obj.get("Id")
    if (not isinstance(container_id,str)
        or not re.fullmatch(r"[0-9a-f]{64}",container_id)
        or set(peers)!={container_id}
        or not isinstance(peers[container_id],dict)
        or peers[container_id].get("Name")!=FINAL_CONTAINER
        or peers[container_id].get("IPv4Address")!=canary+"/"+subnet.split("/")[-1]):
        raise Stop("DOCKER_FINAL_NETWORK_PEERS_CHANGED")
    return True

def check_persistent_state(nft_doc,container_doc,network_doc,**expected):
    """Both state sources must match at the SAME observation cycle."""
    if not check_final_docker_scope(
        container_doc,network_doc,
        **{key:expected[key] for key in
           ("bridge","subnet","host_gateway","canary","expected_image_id")}):
        raise Stop("DOCKER_FINAL_SCOPE_NOT_VERIFIED")
    return check_guard(nft_doc,**{
        key:expected[key] for key in
        ("bridge","subnet","host_gateway","canary","relay_port")
    })

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
