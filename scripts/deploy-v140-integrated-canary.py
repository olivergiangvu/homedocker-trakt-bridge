#!/usr/bin/env python3
"""R23A: build and start ONE integrated v1.4 Bridge/Gateway canary offline.

Dry-run by default. APPLY ONLY with explicit operator confirmation. Creates
exactly one uniquely named image, internal Docker bridge network and container;
does NOT alter production Compose, AIO, nginx, any production volume/DB, or
the private R22 backup. The new Bridge uses only an empty 32MiB tmpfs database,
fake OAuth/API secrets, and deliberately unreachable upstream loopback, so
this smoke does NOT establish client compatibility/normalization.

R23B real AIO authenticated GET/identity projection requires separate approval.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time

EXPECTED_SHA_LENGTH = 40
NAME = "homedocker-v140-integrated-canary"
NETWORK = "homedocker_v140_canary_internal"
PORTS = ((17041, 7000), (17141, 7101))
R22_ROOT = Path("/var/backups/homedocker-trakt-bridge/r22")
CONFIRM = "START_LOCAL_ISOLATED_V140_CANARY_ONLY"
RC6 = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
       "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")

class Stop(Exception):
    pass

def execute(*argv, timeout=1200):
    p = subprocess.run(argv, capture_output=True, text=True,
                       timeout=timeout, check=False)
    if p.returncode:
        raise Stop("Docker/Git step failed; inspect local canary manually, do not touch RC6")
    return p.stdout.strip()

def safe_exec(*argv, timeout=25):
    p = subprocess.run(argv, capture_output=True, text=True,
                       timeout=timeout, check=False)
    return p

def image_tag(sha):
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise Stop("unverified commit SHA")
    return "homedocker-trakt-bridge:v140-r23a-" + sha[:12]

def source_sha(root):
    p = safe_exec("git", "-C", str(root), "rev-parse", "HEAD")
    if p.returncode or not re.fullmatch("[a-f0-9]{40}", p.stdout.strip()):
        raise Stop("checked out Git SHA not verified")
    return p.stdout.strip()

def container_meta(name):
    p = safe_exec("docker", "container", "inspect", name)
    if p.returncode:
        return None
    data = json.loads(p.stdout)
    return data[0] if len(data) == 1 else None

def available_ports():
    existing = execute("docker", "ps", "-a", "--format", "{{.Names}}",
                       timeout=20).splitlines()
    reserved = set()
    for name in existing:
        c = container_meta(name)
        if c is None:
            raise Stop("existing Docker container could not be inspected")
        ports = (c.get("HostConfig") or {}).get("PortBindings") or {}
        for value in ports.values():
            for row in value or []:
                try:
                    reserved.add(int(row.get("HostPort")))
                except (ValueError, TypeError):
                    pass
    for port, _ in PORTS:
        if port in reserved:
            raise Stop("candidate port reserved by Docker container")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise Stop("candidate localhost port occupied") from exc

def check_r22():
    if not R22_ROOT.is_dir() or R22_ROOT.is_symlink():
        raise Stop("R22 root-only rollback snapshot not found")
    eligible = 0
    for folder in R22_ROOT.iterdir():
        if folder.is_symlink() or not folder.is_dir():
            continue
        if folder.stat().st_mode & 0o077:
            continue
        manifest = folder / "manifest.json"
        sql = folder / "bridge.db"
        env = folder / "compose.env.rc6"
        if any(not x.is_file() or x.is_symlink()
               or x.stat().st_mode & 0o077 for x in (manifest, sql, env)):
            continue
        try:
            if manifest.stat().st_size > 4096:
                continue
            state = json.loads(manifest.read_text(encoding="utf-8"))
            if (state.get("image_ref") == RC6
                    and state.get("restore_rehearsal")
                    == "isolated_sqlite_backup_and_integrity_pass"
                    and state.get("sqlite_snapshot", {}).get("profile_rows", 0) >= 1):
                eligible += 1
        except (ValueError, OSError):
            continue
    if eligible == 0:
        raise Stop("no qualifying private R22 snapshot/recovery manifest")
    return eligible

def preflight(root):
    if os.geteuid() != 0:
        raise Stop("sudo/root required to read R22 backup and Docker metadata")
    if (root / "Dockerfile").is_symlink() or not (root / "Dockerfile").is_file():
        raise Stop("exact checked-out build context missing Dockerfile")
    sha = source_sha(root)
    tag = image_tag(sha)
    if container_meta(NAME):
        raise Stop("R23A container already exists; never replace implicitly")
    if safe_exec("docker", "network", "inspect", NETWORK).returncode == 0:
        raise Stop("R23A network name already exists; never reuse implicitly")
    if safe_exec("docker", "image", "inspect", tag).returncode == 0:
        raise Stop("R23A image tag already exists; refuse overwrite")
    current = container_meta("trakt-bridge")
    aio = container_meta("aiostreams")
    legacy = container_meta("homedocker-resume-contract-canary")
    if not all(c and c.get("State", {}).get("Running") for c in (current,aio,legacy)):
        raise Stop("production or retained comparator unavailable")
    if (current.get("Config") or {}).get("Image") != RC6:
        raise Stop("RC6 image reference changed")
    if (aio.get("Config") or {}).get("Image") != "ghcr.io/viren070/aiostreams:latest":
        raise Stop("AIO image reference changed")
    if "media_core_net" in ((current.get("NetworkSettings") or {}).get("Networks") or {}):
        raise Stop("RC6 isolated production network changed")
    available_ports()
    snapshots = check_r22()
    return {"sha": sha, "tag": tag, "qualified_local_snapshot_count": snapshots}

def docker_env():
    # ALL credentials are temporary fabricated strings with NO relation to
    # production Trakt, admin keys, AIO credentials or Bridge secrets.
    return {
        "PUBLIC_BASE_URL": "http://127.0.0.1:17041",
        "PORT": "7000",
        "DATA_DIR": "/app/data",
        "TRAKT_CLIENT_ID": "r23a-pilot-invalid-client",
        "TRAKT_CLIENT_SECRET": secrets.token_urlsafe(32),
        "BRIDGE_SECRET_KEY": secrets.token_urlsafe(32),
        "ADMIN_KEY": secrets.token_urlsafe(32),
        "JELLYFIN_GATEWAY_ENABLED": "true",
        "JELLYFIN_GATEWAY_PORT": "7101",
        "JELLYFIN_GATEWAY_UPSTREAM_URL": "http://127.0.0.1:9999/jellyfin",
        "JELLYFIN_GATEWAY_AIO_USER_ID": "CANARY_PREVIEW_NO_MATCH_USER",
        "JELLYFIN_GATEWAY_PROFILE_ID": "c"*24,
        "JELLYFIN_GATEWAY_LEGACY_IMPORT": "off",
        "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH": "false",
        "AIO_RECONCILER_MODE": "off",
        "AIO_UNPLAYED_ECHO_GUARD": "false",
        "AIO_HISTORY_ECHO_GUARD": "false",
        "CANONICAL_ON_DEMAND_VERIFY": "false",
        "LOG_LEVEL": "warn",
    }

def launch(root, state):
    # Docker may acquire build-only base layers if not already cached;
    # no production container/images or Compose references are modified.
    print("R23A_IMAGE_BUILD_STARTED", flush=True)
    execute("docker", "build", "--pull=false", "--tag", state["tag"], str(root),
            timeout=1500)
    print("R23A_IMAGE_BUILD_COMPLETED", flush=True)
    print("R23A_CREATING_PRIVATE_INTERNAL_NETWORK", flush=True)
    execute("docker", "network", "create", "--driver", "bridge",
            "--internal", "--label", "homedocker.phase=r23a",
            NETWORK, timeout=30)

    env = docker_env()
    argv = ["docker", "run", "-d", "--name", NAME, "--restart", "no",
            "--network", NETWORK, "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "192m", "--cpus", "1",
            "--tmpfs", "/app/data:rw,size=33554432,uid=1000,gid=1000,mode=0700",
            "--label", "homedocker.phase=r23a"]
    for host_port, container_port in PORTS:
        argv.extend(["--publish",
                     f"127.0.0.1:{host_port}:{container_port}"])
    for key, value in sorted(env.items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.append(state["tag"])
    print("R23A_STARTING_SINGLE_INTEGRATED_CONTAINER", flush=True)
    execute(*argv, timeout=35)

    # Health check from *inside* container works even if Docker internal
    # bridge host port publishing is restricted on this daemon.
    for _ in range(20):
        if (safe_exec("docker", "exec", NAME, "wget", "-q", "-O",
                      "/dev/null", "http://127.0.0.1:7000/health",
                      timeout=5).returncode == 0
            and safe_exec("docker", "exec", NAME, "wget", "-q", "-O",
                          "/dev/null", "http://127.0.0.1:7101/health",
                          timeout=5).returncode == 0):
            break
        time.sleep(1)
    else:
        raise Stop("canary health not ready; retain diagnostic container; production untouched")
    obj = container_meta(NAME)
    if not obj or not obj.get("State", {}).get("Running"):
        raise Stop("canary unexpectedly stopped")
    if not obj.get("HostConfig", {}).get("ReadonlyRootfs"):
        raise Stop("canary must have read-only root filesystem")
    if ((obj.get("NetworkSettings") or {}).get("Networks") or {}).keys() != {NETWORK}:
        raise Stop("canary attached to an unexpected Docker network")
    if any(m.get("Type") in ("bind", "volume") for m in obj.get("Mounts") or []):
        raise Stop("unexpected persistent/bind mount in canary")
    return {
        "audit": "R23A_V140_INTEGRATED_BRIDGE_CANARY",
        "status": "ISOLATED_INTEGRATED_LISTENERS_HEALTHY",
        "docker_image_built": True,
        "new_containers_running": 1,
        "separate_internal_network": True,
        "dashboard_internal_health": "200",
        "gateway_internal_health": "200",
        "host_loopback_candidate_ports": [p for p,_ in PORTS],
        "host_loopback_reachability": "NOT_TESTED",
        "aio_or_trakt_external_api_access": "DISABLED_BY_NETWORK_ISOLATION",
        "real_bridge_trakt_credentials": False,
        "production_db_or_volume_mounted": False,
        "secrets_are_synthetic": True,
        "ephemeral_canary_database": True,
        "identity_normalization_qualified": False,
        "single_client_routing_enabled": False,
        "production_containers_restarted": 0,
        "production_nginx_or_network_modified": False,
        "isolated_canary_created": True,
        "rollback_container": NAME,
        "rollback_network": NETWORK,
        "snapshot_unmodified": True,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        state = preflight(root)
        if not args.apply:
            print(json.dumps({
                "audit": "R23A_INTEGRATED_CANARY_PREFLIGHT",
                "status": "DRY_RUN_READY",
                "source_commit_verified": True,
                "private_bridge_rollback_snapshots_found": state["qualified_local_snapshot_count"],
                "container_or_network_name_conflict": False,
                "candidate_ports_available_now": True,
                "production_creds_or_database_required": False,
                "would_build_new_dedicated_image": True,
                "would_create_internal_network_and_one_canary": True,
                "docker_changes": 0,
                "requires_explicit_apply": True,
            }, indent=2))
            print("R23A_DRY_RUN_PASS")
            return
        if args.confirm != CONFIRM:
            raise Stop("explicit canary launch confirmation missing")
        print(json.dumps(launch(root, state), indent=2))
        print("R23A_OFFLINE_INTEGRATED_CANARY_PASS")
    except (Stop, ValueError, OSError, TypeError, subprocess.TimeoutExpired):
        print("STOP: R23A canary preflight/launch incomplete; do not change production",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
