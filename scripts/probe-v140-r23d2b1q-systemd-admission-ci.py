#!/usr/bin/env python3
"""R23D-2B1Q disposable CI-only *manual* systemd admission interlock.

Exact synthetic peers, no host credentials. Not a production boot guardian.
"""
import argparse
import json
import os
import re
import subprocess
import sys

IP_CLIENT="172.31.252.2"
IP_SERVER="172.31.252.3"
TABLE="r23d2b1q_lease"
BRIDGE="br-r23d2b1q"

class Stop(Exception):
    pass

def call(*argv, timeout=12):
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop("CI_INSPECT_OR_COMMAND_UNAVAILABLE") from None
    if p.returncode:
        raise Stop("CI_COMMAND_FAILED_"+argv[0].upper())
    return p.stdout

def inspect(name):
    try:
        x=json.loads(call("docker","container","inspect",name))
        if not isinstance(x,list) or len(x)!=1:raise ValueError()
        return x[0]
    except ValueError:
        raise Stop("AMBIGUOUS_DOCKER_INSPECT") from None

def verify(a):
    if not (
        os.geteuid()==0
        and os.getenv("GITHUB_ACTIONS")=="true"
        and os.getenv("CI")=="true"
        and os.getenv("RUNNER_OS")=="Linux"
        and os.getenv("RUNNER_ENVIRONMENT")=="github-hosted"
        and os.getenv("R23D2B1Q_EPHEMERAL_ONLY")=="true"
    ): raise Stop("NOT_DISPOSABLE_GITHUB_RUNNER")
    if not re.fullmatch(r"[1-9][0-9]*",a.suffix):
        raise Stop("BAD_RESOURCE_SUFFIX")
    server="r23d2b1q-server-"+a.suffix
    client="r23d2b1q-client-"+a.suffix
    network="r23d2b1q-private-"+a.suffix
    s=inspect(server)
    c=inspect(client)
    for row,name in ((s,server),(c,client)):
        host=row.get("HostConfig") or {}
        labels=(row.get("Config") or {}).get("Labels") or {}
        if (row.get("Name")!="/"+name
            or labels.get("r23d2b1q.lab")!="disposable"
            or (host.get("RestartPolicy") or {}).get("Name")!="no"
            or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False
            or host.get("PortBindings") not in ({},None)):
            raise Stop("OWNERSHIP_OR_RESTART_POLICY_DRIFT")
    cn=(c.get("NetworkSettings") or {}).get("Networks") or {}
    sn=(s.get("NetworkSettings") or {}).get("Networks") or {}
    if (not c["State"]["Running"] or set(cn)!={network}
        or cn[network].get("IPAddress")!=IP_CLIENT):
        raise Stop("CLIENT_NETWORK_DRIFT")
    # Server must be STOPPED and DETACHED. No Docker API attached peer
    # can be admitted until both kernel safeguards are proven.
    # Docker --network none can appear as a named "none" NetworkSettings
    # entry even though it has no routable Docker bridge endpoint.
    if s["State"]["Running"] or not set(sn).issubset({"none"}):
        raise Stop("SERVER_NOT_STOPPED_AND_NETWORK_DETACHED")
    nd=json.loads(call("docker","network","inspect",network))
    if (len(nd)!=1 or nd[0].get("Internal") is not True
        or nd[0].get("Driver")!="bridge"):
        raise Stop("NOT_PRIVATE_INTERNAL_BRIDGE")
    pid=c["State"].get("Pid")
    if not isinstance(pid,int) or pid<2: raise Stop("CLIENT_PID_INVALID")
    m=re.search(r"eth0@if([1-9][0-9]*):",call("nsenter","-t",str(pid),
                         "-n","ip","-o","link","show","dev","eth0"))
    if not m:raise Stop("CLIENT_IFLINK_UNAVAILABLE")
    interfaces=json.loads(call("ip","-j","link","show"))
    matches=[x for x in interfaces if x.get("ifindex")==int(m.group(1))]
    if len(matches)!=1:raise Stop("HOST_VETH_NOT_UNIQUE")
    iface=matches[0]
    dev=iface["ifname"].split("@",1)[0]
    if not dev.startswith("veth") or iface.get("master")!=BRIDGE:
        raise Stop("HOST_VETH_NOT_OWNED")
    q=call("tc","qdisc","show","dev",dev)
    fl=call("tc","filter","show","dev",dev,"ingress")
    if "clsact" not in q or "pref 10" not in fl or "pref 20" not in fl:
        raise Stop("INDEPENDENT_TC_FENCE_NOT_PROVEN")
    try:
        policy=call("nft","list","table","bridge",TABLE)
    except Stop:
        raise Stop("NFT_ADMISSION_POLICY_MISSING")
    required=("hook forward", "iifname", "oifname", "@authorized",
              "flags timeout", "drop", IP_CLIENT, IP_SERVER)
    missing=[str(i) for i,t in enumerate(required) if t not in policy]
    if missing:
        raise Stop("NFT_ADMISSION_POLICY_UNQUALIFIED_FIELDS_"+",".join(missing))
    return server,network

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--suffix",required=True)
    p.add_argument("--admit",action="store_true")
    a=p.parse_args()
    server,network=verify(a)
    if not a.admit:
        print("R23D2B1Q_SYSTEMD_GUARD_CHECK_PASS",flush=True)
        return
    # Only the exact synthetic bridge set can receive a 20s trial grant.
    # Not a production renewing lease; only to test crashless admission.
    granted=False
    try:
        call("nft","add","element","bridge",TABLE,"authorized",
             "{ "+IP_CLIENT+" timeout 20s }")
        granted=True
        call("docker","network","connect","--ip",IP_SERVER,network,server)
        call("docker","start",server,timeout=30)
        live=inspect(server)
        sn=live["NetworkSettings"]["Networks"]
        if (not live["State"]["Running"] or set(sn)!={network}
            or sn[network]["IPAddress"]!=IP_SERVER):
            raise Stop("POST_ADMISSION_GATEWAY_IDENTITY_CHANGED")
        print("R23D2B1Q_SYSTEMD_INTERLOCK_ADMITTED_AFTER_TC_AND_NFT",flush=True)
    except Exception:
        # Stop and detach the exact test peer before propagating any failure.
        subprocess.run(["docker","stop","--time","1",server],
                       capture_output=True,timeout=15)
        subprocess.run(["docker","network","disconnect","-f",network,server],
                       capture_output=True,timeout=15)
        if granted:
            subprocess.run(["nft","delete","element","bridge",TABLE,
                            "authorized","{ "+IP_CLIENT+" }"],
                           capture_output=True,timeout=6)
        raise

if __name__=="__main__":
    try:main()
    except Stop as e:
        print("R23D2B1Q_ADMISSION_STOP_"+str(e),file=sys.stderr)
        raise SystemExit(5) from None
