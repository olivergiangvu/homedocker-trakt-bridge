#!/usr/bin/env python3
"""Exact-name HomeDocker RC7 canary resource reclamation; dry-run by default.

Never removes arbitrary Docker containers, volumes, networks, bind mounts,
Portainer stacks, Bridge production, AIOStreams, nginx, or Bridge SQLite.
All actions run on the HomeDocker HOST via SSH with local Docker privileges.
"""
import argparse
import json
import re
import subprocess
import sys

CANARIES = (
    ("homedocker-nextup-canary", "homedocker-nextup-gateway:canary-96f229afcfcd", "17131"),
    ("homedocker-nextup-auth-canary", "homedocker-nextup-gateway:canary-auth-87103bdbd6a8", "17132"),
    ("homedocker-nextup-c4fix-canary", "homedocker-nextup-gateway:c4fix-6d91cd02de5c", "17133"),
    ("homedocker-resume-contract-canary", "homedocker-nextup-gateway:resume-05a514c0b50e", "17134"),
)
PRODUCTION = ("trakt-bridge", "aiostreams")
CONFIRM = "REMOVE_EXACT_RC7_CANARIES_ONLY"

def cmd(*args, allow_failure=False):
    result = subprocess.run(
        list(args), capture_output=True, text=True, timeout=40,
    )
    if result.returncode and not allow_failure:
        raise RuntimeError("Docker command failed: " + " ".join(args[:2]))
    return result

def docker(*args):
    return cmd("docker", *args).stdout

def inspect(name):
    proc = cmd("docker", "inspect", name, allow_failure=True)
    if proc.returncode:
        return None
    rows = json.loads(proc.stdout)
    return rows[0] if len(rows) == 1 else None

def image_info(tag):
    p = cmd("docker", "image", "inspect", tag, allow_failure=True)
    if p.returncode:
        return None
    rows = json.loads(p.stdout)
    return rows[0] if len(rows) == 1 else None

def nginx_has_canary_routes():
    p = cmd("nginx", "-T", allow_failure=True)
    if p.returncode:
        raise RuntimeError("nginx -T failed; no cleanup is allowed")
    # Only source-controlled RC7 canary listener ports may trigger this gate.
    return bool(re.search(r"(?<!\d)1713[1-4](?!\d)", p.stdout))

def validate(container, expected_image, expected_port):
    if container is None:
        return "absent"
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    bindings = host.get("PortBindings", {}).get("7101/tcp")
    expected_binding = [{"HostIp": "127.0.0.1", "HostPort": expected_port}]
    checks = {
        "image_tag": config.get("Image") == expected_image,
        "port_exact": bindings == expected_binding,
        "network_exact": host.get("NetworkMode") == "media_core_net",
        "restart_disabled": host.get("RestartPolicy", {}).get("Name") == "no",
        "rootfs_readonly": host.get("ReadonlyRootfs") is True,
        "unprivileged": host.get("Privileged") is False,
        "not_bridge": container.get("Name", "").lstrip("/") not in PRODUCTION,
        "no_writable_mounts": not any(
            m.get("RW", True) for m in container.get("Mounts", [])
        ),
    }
    failures = [key for key, ok in checks.items() if not ok]
    if failures:
        raise RuntimeError("CANARY_PREFLIGHT_FAILED:" + ",".join(failures))
    return "running" if container.get("State", {}).get("Running") else "stopped"

def production_state():
    result = {}
    for name in PRODUCTION:
        obj = inspect(name)
        if obj is None or obj.get("State", {}).get("Running") is not True:
            raise RuntimeError("PRODUCTION_NOT_RUNNING:" + name)
        result[name] = (obj["Id"], obj["Image"], True)
    return result

def inventory(include_latest):
    selected = CANARIES if include_latest else CANARIES[:3]
    targets = []
    for name, tag, port in selected:
        container = inspect(name)
        state = validate(container, tag, port)
        img = image_info(tag)
        if container is not None:
            if img is None or img["Id"] != container["Image"]:
                raise RuntimeError("CANARY_IMAGE_ID_MISMATCH:" + name)
        targets.append({
            "name": name, "image_tag": tag, "port": port,
            "status": state,
            "image_id": img["Id"] if img else None,
            "image_bytes": img.get("Size", 0) if img else 0,
        })
    return targets

def running_memory():
    try:
        output = cmd("docker", "stats", "--no-stream", "--format",
                     "{{.Name}}|{{.MemUsage}}").stdout
    except (RuntimeError, subprocess.TimeoutExpired):
        return {}
    return dict(row.split("|", 1) for row in output.splitlines()
                if "|" in row)

def all_container_image_ids():
    proc = cmd("docker", "ps", "-aq")
    ids = proc.stdout.splitlines()
    if not ids:
        return set()
    return {json.loads(cmd("docker", "inspect", "-f",
                           "{{json .Image}}", identifier).stdout)
            for identifier in ids}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Perform allowlisted cleanup")
    parser.add_argument("--confirm", default="", help="Required exact confirmation string")
    parser.add_argument("--include-latest", action="store_true",
                        help="Also remove latest C12 17134 canary; use only after pilot/rollback approval")
    args = parser.parse_args()

    if args.confirm and not args.apply:
        parser.error("--confirm is only valid with --apply")
    if args.apply and args.confirm != CONFIRM:
        parser.error("--apply requires --confirm " + CONFIRM)

    before = production_state()
    if nginx_has_canary_routes():
        raise RuntimeError("NGINX_CANARY_ROUTING_PRESENT: refuse cleanup")
    targets = inventory(args.include_latest)
    memory = running_memory()

    print("=== RC7 RESOURCE RECOVERY PREFLIGHT ===", flush=True)
    print(json.dumps({
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "nginx_canary_routes": False,
        "production_image_and_container_pins": "verified",
        "latest_17134_included": args.include_latest,
        "targets": [{
            "name": t["name"],
            "port": t["port"],
            "status": t["status"],
            "approx_memory": memory.get(t["name"], "not_available"),
            "image_size_mib": round(t["image_bytes"] / 1048576, 1),
        } for t in targets],
        "volume_network_or_bind_mount_deletions": 0,
        "broad_prune": False,
        "removals": 0 if not args.apply else "pending",
    }, indent=2), flush=True)
    if not args.apply:
        print("DRY_RUN_PASS=TRUE")
        return

    # Final race guard: no new nginx canary route since preflight.
    if nginx_has_canary_routes():
        raise RuntimeError("NGINX_ROUTE_CHANGED_ABORT")
    for t in targets:
        name = t["name"]
        container = inspect(name)
        validate(container, t["image_tag"], t["port"])
        if container is not None and t["status"] == "absent":
            raise RuntimeError("CANARY_APPEARED_SINCE_PREFLIGHT:" + name)

    removed_containers = []
    removed_image_tags = []
    for t in targets:
        if t["status"] == "absent":
            continue
        name = t["name"]
        if inspect(name)["State"].get("Running"):
            cmd("docker", "stop", "--time", "10", name)
        cmd("docker", "rm", name)  # Explicitly NO -v or -f.
        removed_containers.append(name)

    # Image removal only for verified exact canary tags, after ensuring there
    # are NO other containers (including stopped) using these image IDs.
    for t in targets:
        img = image_info(t["image_tag"])
        if not img:
            continue
        if t["image_id"] != img["Id"]:
            raise RuntimeError("IMAGE_TAG_CHANGED_REFUSE_DELETE")
        if img["Id"] in all_container_image_ids():
            print("IMAGE_RETAINED_IN_USE=" + t["name"])
            continue
        cmd("docker", "image", "rm", t["image_tag"])
        removed_image_tags.append(t["image_tag"])

    if production_state() != before:
        raise RuntimeError("STOP: PRODUCTION_CHANGED_AFTER_CLEANUP")
    print(json.dumps({
        "cleanup": "DONE",
        "removed_container_names": removed_containers,
        "removed_exact_image_tags": removed_image_tags,
        "production_image_and_container_pins_unchanged": True,
        "volumes_deleted": 0,
        "networks_deleted": 0,
        "backup_files_deleted": 0,
    }, indent=2))
    print("RC7_EXACT_CLEANUP=PASS")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError,
            json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        sys.exit(1)
