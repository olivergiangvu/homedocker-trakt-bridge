#!/usr/bin/env python3
"""R23D-2B1O disposable CI only: independent Docker guardian/watchdog.

This intentionally does NOT supply a production boot fence. In particular,
simultaneous SIGKILL of both processes leaves a time window until an external
kernel lease expires (no such production lease is installed here).
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

def stop(reason):
    raise RuntimeError(reason)

def ensure_runner():
    if not (os.geteuid()==0 and os.getenv("GITHUB_ACTIONS")=="true"
            and os.getenv("CI")=="true" and os.getenv("RUNNER_OS")=="Linux"
            and os.getenv("RUNNER_ENVIRONMENT")=="github-hosted"
            and os.getenv("R23D2B1O_EPHEMERAL_ONLY")=="true"):
        stop("B1O_STOP_NOT_EPHEMERAL_RUNNER")
def run(*a, timeout=8, check=True):
    p=subprocess.run(a,capture_output=True,text=True,timeout=timeout,check=False)
    if check and p.returncode:
        stop("B1O_COMMAND_FAILED_"+a[0].upper())
    return p.stdout.strip() if p.returncode==0 else None
def write_heartbeat(path):
    # Only CI-owned scratch paths, never Docker volume or production config.
    Path(path).write_text(str(time.monotonic()),encoding="ascii")
def fresh(path, seconds=2.5):
    try:
        return time.monotonic()-float(Path(path).read_text(encoding="ascii"))<seconds
    except (OSError,ValueError): return False
def alive(pid):
    if pid<2: return False
    try:
        with open(f"/proc/{pid}/stat",encoding="ascii") as f:
            tail=f.read().split(") ",1)[1].split()
        return tail[0] not in ("Z","X") and os.kill(pid,0) is None
    except (OSError,IndexError,ValueError): return False
def state(name):
    rows=json.loads(run("docker","inspect",name))
    if len(rows)!=1: stop("B1O_DOCKER_INSPECT_NOT_EXACT")
    return rows[0]
def verify(c,name,net,ip):
    if c["Name"]!="/"+name: stop("B1O_IDENTITY_CHANGED")
    if (c["HostConfig"]["RestartPolicy"]["Name"]!="no"
        or c["HostConfig"]["ReadonlyRootfs"] is not True
        or c["HostConfig"]["Privileged"] is not False
        or c["HostConfig"].get("PortBindings") not in (None,{})
        or c["HostConfig"]["NetworkMode"]!=net
        or (c["Config"].get("Labels") or {}).get("r23d2b1o.lab")!="ephemeral"):
        stop("B1O_DOCKER_OWNERSHIP_FAIL")
    networks=(c.get("NetworkSettings") or {}).get("Networks") or {}
    # Docker does not necessarily attach created/stopped containers to a
    # bridge until docker start. Never mistake an empty stopped IP for drift.
    if networks and set(networks)!={net}:
        stop("B1O_DOCKER_OTHER_NETWORK_PRESENT")
    if c["State"]["Running"]:
        if set(networks)!={net} or networks[net].get("IPAddress")!=ip:
            stop("B1O_RUNNING_PEER_IP_DRIFT")
def docker_stop(name):
    c=state(name)
    if c["State"]["Running"]:
        run("docker","stop","--time","1",name,timeout=15)
def peer_veth(name,bridge):
    c=state(name)
    if not c["State"]["Running"]: stop("B1O_CLIENT_NOT_RUNNING")
    pid=c["State"]["Pid"]
    if not isinstance(pid,int) or pid<2: stop("B1O_INVALID_CLIENT_PID")
    line=run("nsenter","-t",str(pid),"-n","ip","-o","link","show","dev","eth0")
    m=re.search(r"eth0@if([1-9][0-9]*):",line)
    if not m: stop("B1O_PEER_IFINDEX_UNAVAILABLE")
    interfaces=json.loads(run("ip","-j","link","show"))
    matches=[row for row in interfaces if row.get("ifindex")==int(m.group(1))]
    if len(matches)!=1: stop("B1O_HOST_VETH_AMBIGUOUS")
    v=matches[0]
    dev=v["ifname"].split("@",1)[0]
    if not dev.startswith("veth") or v.get("master")!=bridge:
        stop("B1O_HOST_VETH_NOT_OWNED")
    return dev
def tc_armed(dev):
    # Inspect BOTH classifiers, not just qdisc presence.
    q=run("tc","qdisc","show","dev",dev)
    filters=run("tc","filter","show","dev",dev,"ingress")
    return "clsact" in q and "pref 10" in filters and "pref 20" in filters
def arm(dev,peer,server):
    if tc_armed(dev): return
    if "clsact" in run("tc","qdisc","show","dev",dev):
        stop("B1O_UNEXPECTED_EXISTING_TC_RULES")
    run("tc","qdisc","add","dev",dev,"clsact")
    run("tc","filter","add","dev",dev,"ingress","protocol","arp",
        "pref","5","matchall","action","goto","chain","1")
    run("tc","filter","add","dev",dev,"ingress","protocol","ip","pref","10",
        "u32","match","ip","src",peer+"/32","match","ip","dst",
        server+"/32","match","ip","protocol","6","0xff",
        "match","ip","dport","17420","0xffff","action","goto","chain","1")
    run("tc","filter","add","dev",dev,"ingress","protocol","all",
        "pref","20","matchall","action","drop")
    run("tc","filter","add","dev",dev,"ingress","chain","1",
        "protocol","all","pref","5","matchall","action","pass")
    if not tc_armed(dev):stop("B1O_TC_NOT_ARMED")
def refresh_optional_kernel_lease(a):
    # R23D-2B1P ONLY, isolated runner + hardcoded exact owned nft family/name.
    # nft -f submits flush+add as a SINGLE netlink transaction, so a
    # continuously refreshed 3s kernel timeout is the only permission.
    # After SIGKILL (including guardian + watchdog), no renewer exists.
    if not a.kernel_lease:
        return
    batch=(
        "flush set bridge r23d2b1p_lease authorized\\n"
        "add element bridge r23d2b1p_lease authorized "
        "{ 172.31.252.2 timeout 3s }\\n"
    )
    p=subprocess.run(["nft","-f","-"],input=batch,text=True,
                     capture_output=True,timeout=3,check=False)
    if p.returncode:
        stop("B1P_KERNEL_LEASE_REFRESH_FAILED")

def guard_loop(a):
    # Guardian owns only exact-named, labeled fake Docker containers.
    while True:
        write_heartbeat(a.guard_heartbeat)
        if not alive(a.watchdog_pid) or not fresh(a.watchdog_heartbeat):
            docker_stop(a.server)
            print("R23D2B1O_GUARDIAN_DETECTED_WATCHDOG_LOSS_STOPPED_FAKE_GATEWAY",flush=True)
            return
        try:
            gateway=state(a.server); client=state(a.client)
            verify(gateway,a.server,a.network,a.server_ip)
            verify(client,a.client,a.network,a.client_ip)
            if not client["State"]["Running"]:
                docker_stop(a.server)
                run("docker","start",a.client,timeout=25)
                client=state(a.client)
                verify(client,a.client,a.network,a.client_ip)
            dev=peer_veth(a.client,a.bridge)
            if not tc_armed(dev) and gateway["State"]["Running"]:
                docker_stop(a.server)
            arm(dev,a.client_ip,a.server_ip)
            # Renew only after BOTH watchdog freshness and real veth+Docker
            # owner checks. A nonrenewable lease expires in the kernel.
            refresh_optional_kernel_lease(a)
            if not gateway["State"]["Running"]:
                if not fresh(a.watchdog_heartbeat): stop("B1O_NO_WATCHDOG_LEASE")
                run("docker","start",a.server,timeout=25)
                verify(state(a.server),a.server,a.network,a.server_ip)
                print("R23D2B1O_AUTOMATED_REARM_BEFORE_FAKE_GATEWAY_ADMISSION",flush=True)
        except Exception as exc:
            try: docker_stop(a.server)
            except Exception: pass
            print("R23D2B1O_GUARDIAN_FAIL_CLOSED_"+type(exc).__name__,flush=True)
            return
        time.sleep(0.25)
def watchdog_loop(a):
    # Watchdog is separate OS process. Guardian PID is read from a fresh,
    # CI-owned pidfile; watchdog never launches the gateway itself.
    start=time.monotonic()
    while not Path(a.guard_pidfile).exists():
        write_heartbeat(a.watchdog_heartbeat)
        if time.monotonic()-start>12: stop("B1O_GUARDIAN_MISSING")
        time.sleep(0.2)
    pid=int(Path(a.guard_pidfile).read_text(encoding="ascii"))
    while True:
        write_heartbeat(a.watchdog_heartbeat)
        if not alive(pid) or not fresh(a.guard_heartbeat):
            try: docker_stop(a.server)
            except Exception: pass
            print("R23D2B1O_INDEPENDENT_WATCHDOG_STOPPED_FAKE_GATEWAY",flush=True)
            return
        time.sleep(0.2)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--role",choices=("guardian","watchdog"),required=True)
    p.add_argument("--server",required=True)
    p.add_argument("--client",required=True)
    p.add_argument("--network",required=True)
    p.add_argument("--bridge",required=True)
    p.add_argument("--server-ip",default="172.31.252.3")
    p.add_argument("--client-ip",default="172.31.252.2")
    p.add_argument("--guard-heartbeat",required=True)
    p.add_argument("--watchdog-heartbeat",required=True)
    p.add_argument("--guard-pidfile",required=True)
    p.add_argument("--watchdog-pid",type=int,default=0)
    p.add_argument("--kernel-lease",action="store_true")
    args=p.parse_args()
    try:
        ensure_runner()
        # Strictly constrain all command targets to the disposable test names.
        if (not re.fullmatch(r"r23d2b1o-gw-[1-9][0-9]*",args.server) or
            not re.fullmatch(r"r23d2b1o-peer-[1-9][0-9]*",args.client) or
            not re.fullmatch(r"r23d2b1o-private-[1-9][0-9]*",args.network) or
            args.bridge!="br-r23d2b1o" or
            args.server_ip!="172.31.252.3" or args.client_ip!="172.31.252.2"):
            stop("B1O_STOP_NON_LAB_DOCKER_RESOURCE")
        if args.role=="watchdog": watchdog_loop(args)
        else: guard_loop(args)
    except Exception as exc:
        print("R23D2B1O_STOP_"+type(exc).__name__,file=sys.stderr,flush=True)
        raise SystemExit(3) from None
