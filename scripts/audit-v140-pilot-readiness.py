#!/usr/bin/env python3
"""R21 read-only v1.4 integrated canary topology and rollback readiness.

Checks only exact Docker container names, published ports, network membership
and production Bridge SQLite quick_check in mode=ro/query_only. Never prints
environment variables, credentials, paths, database content, IP addresses or
media identities. NEVER creates a snapshot, Docker container, network or
Gateway listener; a backup must be made and VERIFIED in a separately approved
step before any actual pilot.
"""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from contextlib import closing

RC6_IMAGE = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
    "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")
AIO_TAG = "ghcr.io/viren070/aiostreams:latest"
EXPECTED = ("trakt-bridge", "aiostreams", "homedocker-resume-contract-canary")
CANDIDATE_PORTS = (17041, 17141)

class PreflightStop(Exception):
    pass

def run(*args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=25,
                       check=False)
    if p.returncode:
        raise PreflightStop("local inspection command failed")
    return p.stdout

def inspect(name):
    try:
        data = json.loads(run("docker", "container", "inspect", name))
    except (ValueError, TypeError):
        raise PreflightStop("unexpected Docker inspection response") from None
    if len(data) != 1 or data[0].get("Name") != "/" + name:
        raise PreflightStop("required exact Docker container unavailable")
    return data[0]

def host_bound_port(container, internal, port):
    binds = (container.get("HostConfig") or {}).get("PortBindings") or {}
    return any(b.get("HostIp") == "127.0.0.1"
               and str(b.get("HostPort")) == str(port)
               for b in binds.get(internal, []) or [])

def live_socket_ports():
    output = run("ss", "-H", "-ltn")
    occupied = set()
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 4:
            continue
        address = columns[3]
        match = re.search(r":([0-9]{1,5})$", address)
        if match:
            occupied.add(int(match.group(1)))
    return occupied

def docker_reserved_ports():
    # Includes stopped containers: they can later be restarted by another
    # stack. Inspect only HostConfig.PortBindings, never Env/mount secrets.
    names = run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    reserved = set()
    for name in names:
        obj = inspect(name)
        for binding in ((obj.get("HostConfig") or {}).get("PortBindings") or {}).values():
            for row in binding or []:
                try:
                    reserved.add(int(row.get("HostPort")))
                except (TypeError, ValueError):
                    pass
    return reserved

def sqlite_quick_check(bridge):
    mounts = [m for m in bridge.get("Mounts") or []
              if m.get("Destination") == "/app/data"
              and m.get("Type") in ("volume", "bind")]
    if len(mounts) != 1:
        raise PreflightStop("Bridge persistent data mount missing or ambiguous")
    db_path = Path(mounts[0].get("Source", "")) / "bridge.db"
    if db_path.is_symlink() or not db_path.is_file():
        raise PreflightStop("Bridge database missing or not a regular file")
    try:
        with closing(sqlite3.connect(
            db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )) as db:
            db.execute("PRAGMA query_only=ON")
            check = db.execute("PRAGMA quick_check(1)").fetchone()
            return check is not None and check[0] == "ok"
    except sqlite3.Error:
        raise PreflightStop("read-only Bridge SQLite quick_check failed") from None

def audit():
    bridge, aio, legacy = (inspect(name) for name in EXPECTED)
    if not all(c.get("State", {}).get("Running") for c in (bridge, aio, legacy)):
        raise PreflightStop("a required production or retained canary is not running")
    cfg_bridge = bridge.get("Config") or {}
    cfg_aio = aio.get("Config") or {}
    if cfg_bridge.get("Image") != RC6_IMAGE or cfg_aio.get("Image") != AIO_TAG:
        raise PreflightStop("expected production RC6 digest or AIO latest reference changed")
    bn = (bridge.get("NetworkSettings") or {}).get("Networks") or {}
    an = (aio.get("NetworkSettings") or {}).get("Networks") or {}
    if "homedocker-trakt-bridge_default" not in bn or "media_core_net" in bn:
        raise PreflightStop("production Bridge network isolation changed")
    if "media_core_net" not in an:
        raise PreflightStop("AIO no longer on expected media network")
    if not host_bound_port(bridge, "7000/tcp", 7000):
        raise PreflightStop("production Bridge loopback ingress has changed")
    if not host_bound_port(aio, "3000/tcp", 3240):
        raise PreflightStop("production AIO loopback ingress has changed")
    if not host_bound_port(legacy, "7101/tcp", 17134):
        raise PreflightStop("retained RC7 canary loopback ingress has changed")
    check = sqlite_quick_check(bridge)
    if not check:
        raise PreflightStop("production Bridge SQLite quick_check non-ok")
    reserved = docker_reserved_ports()
    listeners = live_socket_ports()
    conflicts = sorted(p for p in CANDIDATE_PORTS
                       if p in reserved or p in listeners)
    return {
        "audit": "R21_V140_ISOLATED_PILOT_PREFLIGHT",
        "production_bridge_rc6_digest_unchanged": True,
        "production_aio_latest_image_reference_unchanged": True,
        "required_containers_running": len(EXPECTED),
        "bridge_separate_from_media_core_net": True,
        "aio_on_media_core_net": True,
        "production_ingress_loopback_verified": True,
        "retained_17134_loopback_verified": True,
        "bridge_sqlite_mode": "READ_ONLY_QUERY_ONLY",
        "bridge_sqlite_quick_check": "ok",
        "proposed_canary_ports": list(CANDIDATE_PORTS),
        "proposed_ports_available_at_snapshot": not conflicts,
        "candidate_port_conflict_numbers": conflicts,
        "recovery_image_reference_retained": True,
        "private_backup_created_or_verified_by_this_audit": False,
        "canary_deployment_authorized": False,
        "backup_and_rollback_gate": "PENDING_INDEPENDENT_BACKUP_AND_RESTORE_VERIFICATION",
        "production_db_writes": 0,
        "docker_nginx_or_network_mutations": 0,
        "trakt_or_aio_http_calls": 0,
        "secrets_paths_or_profiles_printed": False,
    }

if __name__ == "__main__":
    try:
        output = audit()
        print(json.dumps(output, indent=2))
        if not output["proposed_ports_available_at_snapshot"]:
            raise PreflightStop("candidate ports currently in use; no changes made")
        print("R21_READ_ONLY_PREFLIGHT_PASS")
    except (PreflightStop, OSError, ValueError, TypeError,
            subprocess.TimeoutExpired):
        print("STOP: R21 host pilot readiness not verified; no changes made",
              file=sys.stderr)
        sys.exit(1)
