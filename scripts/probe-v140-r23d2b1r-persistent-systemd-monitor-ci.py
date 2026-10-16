#!/usr/bin/env python3
"""B1R disposable persistent-systemd Docker admission monitor; NOT production."""
import argparse
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SPEC=importlib.util.spec_from_file_location(
    "b1q", ROOT/"scripts/probe-v140-r23d2b1q-systemd-admission-ci.py")
b1q=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b1q)

def require_ephemeral():
    if not (os.geteuid()==0 and os.getenv("GITHUB_ACTIONS")=="true"
            and os.getenv("CI")=="true" and os.getenv("RUNNER_OS")=="Linux"
            and os.getenv("RUNNER_ENVIRONMENT")=="github-hosted"
            and os.getenv("R23D2B1R_EPHEMERAL_ONLY")=="true"):
        raise RuntimeError("NOT_DISPOSABLE_RUNNER")
def notify(message):
    dest=os.getenv("NOTIFY_SOCKET","")
    if not dest:return
    if dest.startswith("@"):dest="\x00"+dest[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode("utf8"),dest)
def docker(*a):
    try:
        p=subprocess.run(["docker",*a],capture_output=True,text=True,timeout=8)
    except (OSError,subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode==0 else None
def owned(c,name,network):
    h=c.get("HostConfig") or {}
    labels=(c.get("Config") or {}).get("Labels") or {}
    return (
        c.get("Name")=="/"+name
        and labels.get("r23d2b1q.lab")=="disposable"
        and (h.get("RestartPolicy") or {}).get("Name")=="no"
        and h.get("Privileged") is False
        and h.get("ReadonlyRootfs") is True
        and h.get("PortBindings") in ({},None)
    )
def check_fences(client,network):
    try:
        c=b1q.inspect(client)
        nets=(c.get("NetworkSettings") or {}).get("Networks") or {}
        if not owned(c,client,network) or not c["State"]["Running"]:
            return False
        if set(nets)!={network} or nets[network]["IPAddress"]!=b1q.IP_CLIENT:
            return False
        pid=c["State"]["Pid"]
        if not isinstance(pid,int) or pid<2:return False
        l=b1q.call("nsenter","-t",str(pid),"-n",
                   "ip","-o","link","show","dev","eth0")
        m=re.search(r"eth0@if([1-9][0-9]*):",l)
        if not m:return False
        interfaces=json.loads(b1q.call("ip","-j","link","show"))
        matches=[i for i in interfaces if i.get("ifindex")==int(m.group(1))]
        if len(matches)!=1:return False
        row=matches[0]
        dev=row["ifname"].split("@",1)[0]
        if not dev.startswith("veth") or row.get("master")!=b1q.BRIDGE:
            return False
        q=b1q.call("tc","qdisc","show","dev",dev)
        fl=b1q.call("tc","filter","show","dev",dev,"ingress")
        if not all(x in q+fl for x in ("clsact","pref 10","pref 20")):
            return False
        table=b1q.call("nft","list","table","bridge",b1q.TABLE)
        timed=b1q.call("nft","list","set","bridge",b1q.TABLE,"authorized")
        if not all(x in table for x in (
                "hook forward","iifname","oifname","@authorized",
                "drop",b1q.IP_CLIENT,b1q.IP_SERVER)):
            return False
        if not all(x in timed for x in ("type ipv4_addr","timeout 20s")):
            return False
        return True
    except (b1q.Stop,ValueError,KeyError,IndexError,TypeError,OSError):
        return False
def cut(server,network):
    # On fence degradation disconnect the specifically labeled fake server
    # even if it remains a live process. Extra stop is defence in depth.
    raw=docker("container","inspect",server)
    if raw is None:return
    try:
        entries=json.loads(raw)
        if len(entries)!=1 or not owned(entries[0],server,network):return
        nets=(entries[0].get("NetworkSettings") or {}).get("Networks") or {}
        if network in nets:
            docker("network","disconnect","-f",network,server)
        if entries[0]["State"]["Running"]:
            docker("stop","--time","1",server)
    except (ValueError,KeyError,TypeError):
        return
def loop(suffix):
    server="r23d2b1q-server-"+suffix
    client="r23d2b1q-client-"+suffix
    network="r23d2b1q-private-"+suffix
    if not re.fullmatch(r"[1-9][0-9]*",suffix):
        raise RuntimeError("INVALID_LAB_SUFFIX")
    first=True
    while True:
        safe=check_fences(client,network)
        raw=docker("container","inspect",server)
        try:
            rows=json.loads(raw) if raw else []
            valid=len(rows)==1 and owned(rows[0],server,network)
        except (ValueError,TypeError):
            valid=False
        if not safe or not valid:
            if valid:
                cut(server,network)
            if first:
                print("R23D2B1R_CONTROLLER_INITIAL_HOLD_NO_VALID_FENCES",flush=True)
                first=False
        else:
            c=rows[0]
            live=c["State"]["Running"]
            nets=(c.get("NetworkSettings") or {}).get("Networks") or {}
            if live and set(nets)=={network}:
                # Supervise the connected peer, never assert this is a
                # cryptographic verification of nft/tc exact semantics.
                pass
            elif not live and set(nets).issubset({"none"}):
                # Reuse B1Q one-shot admission after fresh evidence, not an
                # unguarded Docker start/network connect.
                args=argparse.Namespace(suffix=suffix,admit=True)
                try:
                    b1q.verify(args)
                    b1q.call("nft","add","element","bridge",b1q.TABLE,
                             "authorized","{ "+b1q.IP_CLIENT+" timeout 20s }")
                    if "none" in nets:
                        b1q.call("docker","network","disconnect","none",server)
                    b1q.call("docker","network","connect","--ip",b1q.IP_SERVER,network,server)
                    b1q.call("docker","start",server,timeout=30)
                    print("R23D2B1R_CONTROLLER_REATTESTED_AND_AUTO_ADMITTED",flush=True)
                except (b1q.Stop,OSError,ValueError):
                    cut(server,network)
            elif network not in nets:
                if live:cut(server,network)
            else:
                cut(server,network)
        if first:
            print("R23D2B1R_CONTROLLER_PERSISTENT_SERVICE_RUNNING",flush=True)
            first=False
        notify("READY=1\nWATCHDOG=1\nSTATUS=monitoring exact disposable Docker peer")
        time.sleep(0.18)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--suffix",required=True)
    args=parser.parse_args()
    require_ephemeral()
    loop(args.suffix)
if __name__=="__main__":
    try:main()
    except Exception as e:
        print("R23D2B1R_MONITOR_STOP_"+type(e).__name__,file=sys.stderr,flush=True)
        raise SystemExit(5) from None
