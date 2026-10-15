#!/usr/bin/env python3
"""R23B-1: replace ONLY R23A's offline canary, to qualify localhost ingress.

Default is READ ONLY. APPLY explicitly stops (but does not delete) the existing
R23A canary, creates a distinct non-internal Docker bridge with outbound
masquerade disabled, starts ONE new isolated integrated Bridge+Gateway canary
using the exact already-built R23A image, and probes localhost health.

A failure after stopping R23A removes ONLY newly created R23B artifacts and
restarts the original R23A canary. R23A is left stopped as rollback on PASS.

NO AIO credentials, production Trakt secrets, database copies or real AIO
connectivity are used in R23B-1. R23B-2 will separately qualify a host-mediated
read-only AIO API relay after exact auth/identity/allowlist review.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler

HERE = Path(__file__).resolve().parent
MODULE = HERE / "deploy-v140-integrated-canary.py"
spec = importlib.util.spec_from_file_location("r23a_deployer", MODULE)
r23a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r23a)

ORIGINAL = r23a.NAME
ORIGINAL_NETWORK = r23a.NETWORK
NEW = "homedocker-v140-r23b-canary"
NETWORK = "homedocker_v140_r23b_local"
CONFIRM = "REPLACE_OFFLINE_CANARY_LOCALHOST_ONLY"
EXPECTED_BASE_IMAGE = "homedocker-trakt-bridge:v140-r23a-e442ce461209"
PORTS = ((17041, 7000), (17141, 7101))

class Stop(Exception):
    pass

def execute(*args, timeout=30):
    r = subprocess.run(args, text=True, capture_output=True,
                       timeout=timeout, check=False)
    if r.returncode:
        raise Stop("required Docker operation failed (details remain on host)")
    return r.stdout.strip()

def inspect(name):
    try:
        data = json.loads(execute("docker", "container", "inspect", name))
        if len(data) != 1 or data[0].get("Name") != "/" + name:
            raise Stop("unexpected Docker container identity")
        return data[0]
    except (ValueError, KeyError, TypeError):
        raise Stop("cannot validate Docker container metadata") from None

def check_ports(container):
    binds = (container.get("HostConfig") or {}).get("PortBindings") or {}
    return all(any(b.get("HostIp") == "127.0.0.1"
                   and b.get("HostPort") == str(host)
                   for b in (binds.get(str(inner) + "/tcp") or []))
               for host, inner in PORTS)

def state():
    if os.geteuid() != 0:
        raise Stop("sudo/root needed for Docker and private R22 inventory")
    if not MODULE.is_file() or MODULE.is_symlink():
        raise Stop("R23A reviewed safety module absent")
    original = inspect(ORIGINAL)
    if not original.get("State", {}).get("Running"):
        raise Stop("R23A must still be running before controlled replacement")
    if original.get("Config", {}).get("Image") != EXPECTED_BASE_IMAGE:
        raise Stop("R23A original image provenance changed")
    image_id = original.get("Image")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(image_id)):
        raise Stop("immutable R23A image ID unavailable")
    if not original.get("HostConfig", {}).get("ReadonlyRootfs"):
        raise Stop("R23A read-only rootfs no longer enforced")
    if not check_ports(original):
        raise Stop("R23A loopback port bindings changed")
    nets = (original.get("NetworkSettings") or {}).get("Networks") or {}
    if set(nets) != {ORIGINAL_NETWORK}:
        raise Stop("R23A not on its only expected isolated network")
    net = json.loads(execute("docker", "network", "inspect", ORIGINAL_NETWORK))[0]
    if net.get("Internal") is not True:
        raise Stop("R23A rollback network unexpectedly non-internal")
    if any(m.get("Type") in ("volume", "bind") for m in original.get("Mounts") or []):
        raise Stop("R23A gained a persistent source mount")
    for name in ("trakt-bridge", "aiostreams", "homedocker-resume-contract-canary"):
        c = inspect(name)
        if not c.get("State", {}).get("Running"):
            raise Stop("required production or retained comparator not running")
        if name == "trakt-bridge" and c.get("Config", {}).get("Image") != r23a.RC6:
            raise Stop("RC6 production image changed")
        if name == "aiostreams" and c.get("Config", {}).get("Image") != "ghcr.io/viren070/aiostreams:latest":
            raise Stop("AIO latest image reference changed")
    if subprocess.run(["docker", "container", "inspect", NEW],
                      capture_output=True, timeout=15).returncode == 0:
        raise Stop("R23B target container already exists")
    if subprocess.run(["docker", "network", "inspect", NETWORK],
                      capture_output=True, timeout=15).returncode == 0:
        raise Stop("R23B network name already exists")
    if r23a.check_r22() < 1:
        raise Stop("private rollback snapshot unavailable")

    # The R23A container owns the two candidate ports. Do not fail preflight
    # on those; reject any other Docker container binding either port.
    names = execute("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    for name in names:
        if name == ORIGINAL:
            continue
        other = inspect(name)
        binds = (other.get("HostConfig") or {}).get("PortBindings") or {}
        for rows in binds.values():
            if any(int(b.get("HostPort") or 0) in (17041,17141) for b in rows or []):
                raise Stop("another Docker container has candidate port reserved")

    image_inspect = subprocess.run(["docker","image","inspect",image_id],
                       capture_output=True, timeout=15)
    if image_inspect.returncode:
        raise Stop("R23A immutable image ID not present locally")
    return {"image_id": image_id, "r22_verified_by_manifest": True}

def env():
    # Copy only known fake knobs, not the running R23A Config.Env:
    # never inherit any environment value or credential from an old container.
    values = r23a.docker_env()
    values["TRAKT_CLIENT_ID"] = "r23b-localhost-fake-no-trakt"
    values["TRAKT_CLIENT_SECRET"] = secrets.token_urlsafe(32)
    values["BRIDGE_SECRET_KEY"] = secrets.token_urlsafe(32)
    values["ADMIN_KEY"] = secrets.token_urlsafe(32)
    values["JELLYFIN_GATEWAY_LEGACY_IMPORT"] = "off"
    return values

def health(port):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=4) as response:
            return response.status
    except (HTTPError, URLError, OSError, TimeoutError):
        return None

def rollback(created_container, created_network, old_stopped):
    # Only touch newly created R23B resources and the original offline canary.
    # Never issue a broad Docker prune / remove anything else.
    if created_container:
        subprocess.run(["docker","rm","-f",NEW],capture_output=True,timeout=30)
    if created_network:
        subprocess.run(["docker","network","rm",NETWORK],capture_output=True,timeout=30)
    if old_stopped:
        subprocess.run(["docker","start",ORIGINAL],capture_output=True,timeout=30)

def apply(preflight):
    network_created = False
    old_stopped = False
    container_created = False
    try:
        print("R23B1_CREATING_SEPARATE_LOCAL_BRIDGE",flush=True)
        execute("docker","network","create","--driver","bridge",
                "--opt","com.docker.network.bridge.enable_ip_masquerade=false",
                "--label","homedocker.phase=r23b1", NETWORK)
        network_created = True
        net = json.loads(execute("docker", "network","inspect",NETWORK))[0]
        if net.get("Internal") is not False:
            raise Stop("R23B local bridge incorrectly internal")
        if (net.get("Options") or {}).get("com.docker.network.bridge.enable_ip_masquerade") != "false":
            raise Stop("R23B outbound masquerade disablement was not applied")

        print("R23B1_STOPPING_ONLY_R23A_OFFLINE_CANARY",flush=True)
        execute("docker","stop",ORIGINAL)
        old_stopped = True

        options = ["docker","run","-d","--name",NEW,
            "--restart","no","--network",NETWORK,
            "--read-only","--cap-drop","ALL",
            "--security-opt","no-new-privileges",
            "--pids-limit","64","--memory","192m","--cpus","1",
            "--tmpfs","/app/data:rw,size=33554432,uid=1000,gid=1000,mode=0700",
            "--label","homedocker.phase=r23b1"]
        for host, inner in PORTS:
            options.extend(["--publish",f"127.0.0.1:{host}:{inner}"])
        for key, value in sorted(env().items()):
            options.extend(["--env",f"{key}={value}"])
        options.append(preflight["image_id"])
        print("R23B1_STARTING_SINGLE_REPLACEMENT_CANARY",flush=True)
        execute(*options)
        container_created = True

        for _ in range(15):
            if health(17041) == 200 and health(17141) == 200:
                break
            time.sleep(1)
        else:
            raise Stop("R23B host localhost health not reachable")

        canary = inspect(NEW)
        original = inspect(ORIGINAL)
        if (not canary["State"]["Running"] or original["State"]["Running"]
            or not check_ports(canary)
            or set((canary.get("NetworkSettings") or {}).get("Networks") or {}) != {NETWORK}
            or not canary["HostConfig"]["ReadonlyRootfs"]
            or any(m.get("Type") in ("bind","volume") for m in canary.get("Mounts") or [])):
            raise Stop("R23B post-start isolation or rollback check failed")
        return {
            "audit":"R23B1_HOST_LOOPBACK_INTEGRATED_REPLACEMENT",
            "status":"LOCALHOST_TWO_LISTENERS_PASS",
            "host_dashboard_17041":200,
            "host_gateway_17141":200,
            "new_runtime_count":1,
            "old_r23a_stopped_retained_for_rollback":True,
            "noninternal_dedicated_bridge":True,
            "bridge_ip_masquerade_disabled":True,
            "media_core_net_attached":False,
            "production_db_or_creds_mounted":False,
            "temporary_canary_db_only":True,
            "real_aio_or_trakt_access_qualified":False,
            "alias_normalization_proven":False,
            "production_container_restarts":0,
            "production_nginx_modified":False,
            "rollback_preserved":True,
            "network_egress_firewall_audited":False,
            "comment":"No real AIO user authentication or upstream GETs in R23B-1.",
        }
    except (Stop, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("R23B1_ROLLBACK_ATTEMPTED_ON_ISOLATED_CANARY_ONLY",flush=True)
        rollback(container_created,network_created,old_stopped)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply",action="store_true")
    p.add_argument("--confirm",default="")
    args=p.parse_args()
    try:
        ready=state()
        if not args.apply:
            print(json.dumps({
                "audit":"R23B1_LOCALHOST_REPLACEMENT_PREFLIGHT",
                "status":"DRY_RUN_READY",
                "original_r23a_canary_retained":True,
                "rc6_and_aio_unmodified":True,
                "backup_manifest_present":ready["r22_verified_by_manifest"],
                "immutable_image_reused":True,
                "candidate_ports_owned_only_by_old_canary":True,
                "proposed_docker_network":"NONINTERNAL_BRIDGE_WITH_MASQUERADE_DISABLED",
                "docker_changes":0,
                "apply_stops_but_never_deletes_r23a":True,
                "aio_api_contact":False,
            },indent=2))
            print("R23B1_DRY_RUN_PASS")
            return
        if args.confirm!=CONFIRM:
            raise Stop("explicit R23B canary replacement confirmation absent")
        print(json.dumps(apply(ready),indent=2))
        print("R23B1_LOCALHOST_CANARY_PASS")
    except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("STOP: R23B-1 not verified; leave RC6/AIO/nginx untouched",file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()
