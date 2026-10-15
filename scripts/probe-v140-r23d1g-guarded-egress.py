#!/usr/bin/env python3
"""R23D-1G: disposable, zero-credential, bridge-scoped nft TCP guard trial.

DEFAULT DRY RUN (no mutation). The optional, explicitly confirmed --apply
creates one labeled Docker bridge and one unprivileged disposable Node
container, installs a NEW independent nft table before running that container,
probes TCP only, then removes only positively attested owned artifacts in
fail-closed order: container => network => nft table. No real AIO token,
password, mount, login, history API, nginx or production container changes.

Creating/removing Docker bridges and this isolated nft table DOES change
host network/firewall state temporarily. No test can certify general firewall
isolation or future runtime. A SIGKILL/power failure can leave owned objects:
STOP and inspect before rerun; do not prune or globally flush nft/iptables.

This file is NOT an R23D Gateway deployer or authorization for credentials.
"""
import argparse
import importlib.util
import ipaddress
import json
import secrets
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

HERE=Path(__file__).resolve().parent
PLANNER=HERE/"audit-v140-r23d1f-firewall-plan-readonly.py"
IMAGE_PROBE=HERE/"probe-v140-r23d-egress-synthetic.py"
CONFIRM="R23D1G_TEMPORARY_FIREWALL_ZERO_TOKEN_TRIAL"
LABEL="homedocker.phase"
PHASE="r23d1g-temp"
RELAY_PORT=17420
DENIED_CONTROL_PORT=17421
MAX_SUBPROCESS_BYTES=4*1024*1024

# No HTTP, OAuth, Jellyfin or Trakt calls. TCP handshake only, with no payload.
NODE=r"""
const net=require('node:net');
const gw=process.argv[1], canary=process.argv[2];
const probe=(host,port)=>new Promise(resolve=>{
  let finished=false;
  const s=net.createConnection({host,port});
  const done=v=>{if(!finished){finished=true;resolve(v)}s.destroy()};
  s.setTimeout(1300,()=>done(false));
  s.once('connect',()=>done(true));
  s.once('error',()=>done(false));
});
(async()=>{
  const permitted=await probe(gw,17420);
  const deniedControl=await probe(gw,17421);
  const hostPorts=await Promise.all([22,80,9000,3240,7000].map(p=>probe(gw,p)));
  const forward=await Promise.all(['192.0.2.1','198.51.100.1','203.0.113.1']
      .map(a=>probe(a,80)));
  process.stdout.write(JSON.stringify({
    contract:'R23D1G_TCP_ONLY',allowed_relay:permitted,
    denied_open_control:deniedControl,host_ports:hostPorts,
    forward_testnet:forward,auth_or_payload_sent:false
  }));
})().catch(()=>process.exit(2));
"""

class Stop(Exception):
    pass

def module(path,name):
    if not path.is_file() or path.is_symlink():
        raise Stop("AUDITED_MODULE_NOT_AVAILABLE")
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def command(argv,*,stdin=None,timeout=25,acceptable=(0,)):
    if not argv or not isinstance(argv,(list,tuple)):
        raise Stop("UNSUPPORTED_COMMAND")
    try:
        result=subprocess.run(argv,input=stdin,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("SUBPROCESS_UNAVAILABLE") from None
    if len(result.stdout)>MAX_SUBPROCESS_BYTES or len(result.stderr)>MAX_SUBPROCESS_BYTES:
        raise Stop("SUBPROCESS_OUTPUT_OVERSIZED")
    if result.returncode not in acceptable:
        raise Stop("SUBPROCESS_COMMAND_REJECTED")
    return result.stdout

def parsed(argv):
    try:
        return json.loads(command(argv))
    except (ValueError,UnicodeError):
        raise Stop("INSPECTION_JSON_REJECTED") from None

def owner_tag(run_id):
    return {LABEL:PHASE,"homedocker.probe_run":run_id}

def assert_network(net,plan,run_id):
    if not isinstance(net,list) or len(net)!=1 or not isinstance(net[0],dict):
        raise Stop("NETWORK_OWNERSHIP_NOT_VERIFIED")
    n=net[0]
    expected_opts={
      "com.docker.network.bridge.name":plan.TEST_BRIDGE,
      "com.docker.network.bridge.enable_ip_masquerade":"false",
    }
    opts=n.get("Options") or {}
    labels=n.get("Labels") or {}
    info=(n.get("IPAM") or {}).get("Config") or []
    expected_net=str(plan.net)
    if (n.get("Name")!=plan.TEST_NET or n.get("Driver")!="bridge"
        or n.get("Internal") is not False or n.get("EnableIPv6") is not False
        or any(opts.get(k)!=v for k,v in expected_opts.items())
        or any(labels.get(k)!=v for k,v in owner_tag(run_id).items())
        or len(info)!=1 or info[0].get("Subnet")!=expected_net
        or info[0].get("Gateway")!=plan.gateway):
        raise Stop("NETWORK_OWNERSHIP_NOT_VERIFIED")
    return n

def assert_container(obj,plan,run_id,image_id):
    if not isinstance(obj,list) or len(obj)!=1:
        raise Stop("CONTAINER_OWNERSHIP_NOT_VERIFIED")
    o=obj[0]; config=o.get("Config") or {}
    networks=(o.get("NetworkSettings") or {}).get("Networks") or {}
    host=o.get("HostConfig") or {}
    if (o.get("Name")!="/"+plan.TEST_CONTAINER
        or o.get("Image")!=image_id
        or any((config.get("Labels") or {}).get(k)!=v
               for k,v in owner_tag(run_id).items())
        or host.get("NetworkMode")!=plan.TEST_NET
        or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is True
        or (host.get("CapAdd") or [])
        or set(networks)!={plan.TEST_NET}
        or networks[plan.TEST_NET].get("IPAddress")!=plan.canary
        or any(m.get("Type") in ("bind","volume") for m in o.get("Mounts") or [])):
        raise Stop("CONTAINER_OWNERSHIP_NOT_VERIFIED")
    return o

def absence(kind,name):
    # Use names list, never interpret arbitrary inspect stderr as 'not found'.
    if kind=="container":
        raw=command(["docker","ps","-a","--format","{{.Names}}"])
    else:
        raw=command(["docker","network","ls","--format","{{.Name}}"])
    return name not in raw.decode("utf8","strict").splitlines()

def table_snapshot(renderer):
    # None only means table absent, as independently confirmed by listing.
    tables=parsed(["nft","-j","list","tables"])
    entries=tables.get("nftables")
    if not isinstance(entries,list):
        raise Stop("NFT_INSPECTION_REJECTED")
    found=[v for v in entries if isinstance(v,dict)
           and isinstance(v.get("table"),dict)
           and v["table"].get("family")=="inet"
           and v["table"].get("name")==renderer.TABLE]
    if not found:
        return None
    if len(found)!=1:
        raise Stop("NFT_INSPECTION_REJECTED")
    return command(["nft","-j","list","table","inet",renderer.TABLE])

def parse_tcp(raw):
    try:
        payload=json.loads(raw)
    except (TypeError,ValueError):
        raise Stop("TCP_CONTRACT_REJECTED") from None
    if (not isinstance(payload,dict)
        or payload.get("contract")!="R23D1G_TCP_ONLY"
        or type(payload.get("allowed_relay")) is not bool
        or type(payload.get("denied_open_control")) is not bool
        or not isinstance(payload.get("host_ports"),list)
        or len(payload["host_ports"])!=5
        or any(type(x) is not bool for x in payload["host_ports"])
        or not isinstance(payload.get("forward_testnet"),list)
        or len(payload["forward_testnet"])!=3
        or any(type(x) is not bool for x in payload["forward_testnet"])
        or payload.get("auth_or_payload_sent") is not False):
        raise Stop("TCP_CONTRACT_REJECTED")
    return payload

class Plan:
    pass

def preflight():
    planner=module(PLANNER,"r23d1g_planner")
    old=module(IMAGE_PROBE,"r23d1g_image_proof")
    planner.baseline()
    net,renderer=planner.inspect_candidates()
    planner.syntactic_check(net,renderer)
    image_id=old.preflight()
    if table_snapshot(renderer) is not None:
        raise Stop("NFT_TABLE_NAME_ALREADY_USED")
    plan=Plan()
    plan.net=net
    plan.renderer=renderer
    plan.TEST_BRIDGE=planner.TEST_BRIDGE
    plan.TEST_NET=planner.TEST_NET
    plan.TEST_CONTAINER=planner.TEST_CONTAINER
    plan.gateway=str(list(net.hosts())[0])
    plan.canary=str(list(net.hosts())[1])
    plan.image_id=image_id
    if RELAY_PORT!=renderer.RELAY_PORT:
        raise Stop("RELAY_PORT_MISMATCH")
    # Read-only local occupancy checks (host gateway not yet created).
    for port in (RELAY_PORT,DENIED_CONTROL_PORT):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1",port))
    return plan

def owned_network(plan,run_id):
    return assert_network(
        parsed(["docker","network","inspect",plan.TEST_NET]),
        plan,run_id)

def owned_container(plan,run_id):
    return assert_container(parsed(["docker","container","inspect",plan.TEST_CONTAINER]),
                            plan,run_id,plan.image_id)

def cleanup(plan,run_id,snapshot):
    """Never remove nft guard while an unverified container/bridge could live.

    This intentionally refuses broad cleanup and leaves an orphaned guard if
    ownership/state changed; operator must investigate before any rerun.
    """
    if not absence("container",plan.TEST_CONTAINER):
        owned_container(plan,run_id)
        command(["docker","rm","-f",plan.TEST_CONTAINER],timeout=25)
    if not absence("container",plan.TEST_CONTAINER):
        raise Stop("CONTAINER_CLEANUP_UNVERIFIED")
    if not absence("network",plan.TEST_NET):
        net=owned_network(plan,run_id)
        peers=(net.get("Containers") or {})
        if peers:
            raise Stop("FOREIGN_NETWORK_PEERS_BLOCK_CLEANUP")
        command(["docker","network","rm",plan.TEST_NET],timeout=30)
    if not absence("network",plan.TEST_NET):
        raise Stop("NETWORK_CLEANUP_UNVERIFIED")
    # nft table is removed LAST. If anybody changed the table, leave it alone.
    now=table_snapshot(plan.renderer)
    if now is not None:
        if snapshot is None or now!=snapshot:
            raise Stop("FIREWALL_TABLE_OWNERSHIP_CHANGED")
        command(["nft","delete","table","inet",plan.renderer.TABLE])
    if table_snapshot(plan.renderer) is not None:
        raise Stop("FIREWALL_CLEANUP_UNVERIFIED")
    return True

def apply(plan):
    run_id=secrets.token_hex(12)
    image_id=plan.image_id
    created_network=False
    guard_snapshot=None
    tcp=None
    control={"allowed":False,"denied":False}
    err=None
    cleanup_error=None
    def listener(address,port,key):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        sock.bind((address,port));sock.listen(2);sock.settimeout(16)
        def receive():
            try:
                s,_=sock.accept();s.close();control[key]=True
            except OSError:
                pass
        thread=threading.Thread(target=receive,daemon=True)
        thread.start()
        return sock,thread
    try:
        # Only Docker-managed temporary network and NFT-owned table. NEVER
        # run any canary/container before guard install and inspection.
        command(["docker","network","create","--driver","bridge",
            "--subnet",str(plan.net),"--gateway",plan.gateway,
            "--opt","com.docker.network.bridge.name="+plan.TEST_BRIDGE,
            "--opt","com.docker.network.bridge.enable_ip_masquerade=false",
            "--label",LABEL+"="+PHASE,
            "--label","homedocker.probe_run="+run_id,plan.TEST_NET],timeout=35)
        created_network=True
        owned_network(plan,run_id)
        link=parsed(["ip","-j","link","show","dev",plan.TEST_BRIDGE])
        if not isinstance(link,list) or len(link)!=1 \
           or link[0].get("ifname")!=plan.TEST_BRIDGE:
            raise Stop("NEW_BRIDGE_INTERFACE_NOT_VERIFIED")
        policy=plan.renderer.render_policy(plan.TEST_BRIDGE,str(plan.net),
             plan.gateway,plan.canary,RELAY_PORT)
        command(["nft","--check","--file","-"],stdin=policy.encode("ascii"))
        command(["nft","--file","-"],stdin=policy.encode("ascii"))
        guard_snapshot=table_snapshot(plan.renderer)
        if guard_snapshot is None:
            raise Stop("NEW_FIREWALL_TABLE_NOT_FOUND")
        # Both ports have real open listeners: denied destination must fail
        # because of firewall, not because nothing listened.
        allowed_sock,allowed_thread=listener(plan.gateway,RELAY_PORT,"allowed")
        try:
            denied_sock,denied_thread=listener(plan.gateway,DENIED_CONTROL_PORT,"denied")
            try:
                command(["docker","run","--rm","--name",plan.TEST_CONTAINER,
                    "--restart","no","--network",plan.TEST_NET,
                    "--ip",plan.canary,"--read-only",
                    "--cap-drop","ALL","--security-opt","no-new-privileges",
                    "--pids-limit","48","--memory","128m","--cpus","0.5",
                    "--user","1000:1000",
                    "--label",LABEL+"="+PHASE,
                    "--label","homedocker.probe_run="+run_id,
                    "--entrypoint","node",image_id,
                    "-e",NODE,plan.gateway,plan.canary],timeout=55)
                # Cannot call docker run twice: take output in assigned var.
            finally:
                denied_sock.close()
                denied_thread.join(timeout=1)
        finally:
            allowed_sock.close()
            allowed_thread.join(timeout=1)
    except BaseException as exc:
        err=exc
    finally:
        # If network create or nft installation timed out *after* committing,
        # ownership checks prevent deleting any unidentified resource.
        # Never delete a table if the network/container cleanup is unverified.
        try:
            if created_network or not absence("network",plan.TEST_NET):
                cleanup(plan,run_id,guard_snapshot)
        except BaseException as ex:
            cleanup_error=ex
    if cleanup_error:
        raise Stop("GUARDED_CLEANUP_REQUIRES_MANUAL_INSPECTION")
    if err:
        raise Stop("GUARDED_SYNTHETIC_TRIAL_STOPPED")
    # A successful set of subprocesses still requires explicit independent
    # TCP verdict evaluation. This function intentionally reports BLOCKED
    # until structured TCP output and negative controls are qualified.
    return {"status":"GUARD_TRIAL_EXECUTED_NO_AUTH_APPROVAL",
            "firewall_effective_egress_isolation_verified":False,
            "jellyfin_session_allowed":False,
            "temporary_objects_cleanup_verified":True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--confirm",default="")
    args=parser.parse_args()
    try:
        plan=preflight()
        if not args.apply:
            print(json.dumps({
                "audit":"R23D1G_GUARDED_TEMPORARY_EGRESS_DRY_RUN",
                "status":"DRY_RUN_READY_NO_NETWORK_CREATED",
                "r23d1f_syntax_and_subnet_preflight_revalidated":True,
                "existing_r23a_r23b_image_id_verified":True,
                "test_bridge_docker_network_container_and_nft_table_names_available":True,
                "guard_installed_before_test_container":True,
                "cleanup_order":"CONTAINER_THEN_NETWORK_THEN_ATTESTED_NFT_TABLE",
                "docker_and_firewall_writes":0,
                "network_egress_firewall_verified":False,
                "jellyfin_session_allowed":False,
                "next":"HOLD_APPLY_FOR_SEPARATE_OPERATOR_REVIEW",
            },indent=2))
            print("R23D1G_DRY_RUN_PASS")
            return
        if args.confirm!=CONFIRM:
            raise Stop("EXPLICIT_CONFIRMATION_REQUIRED")
        print(json.dumps(apply(plan),indent=2))
        print("R23D1G_SYNTHETIC_GUARD_TRIAL_FINISHED")
    except BaseException as exc:
        why=exc.args[0] if isinstance(exc,Stop) and len(exc.args)==1 else ""
        ALLOWED={
            "EXPLICIT_CONFIRMATION_REQUIRED",
            "GUARDED_CLEANUP_REQUIRES_MANUAL_INSPECTION",
            "GUARDED_SYNTHETIC_TRIAL_STOPPED",
        }
        if why not in ALLOWED:
            why="PRECHECK_OR_UNCLASSIFIED_TRIAL_FAILURE"
        print(json.dumps({
            "audit":"R23D1G_SAFE_DIAGNOSTIC","status":"STOP",
            "reason":why,"temporary_resources_may_require_inspection":args.apply,
            "network_egress_firewall_verified":False,
            "jellyfin_session_allowed":False,
            "production_services_modified":False,
        },indent=2),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
