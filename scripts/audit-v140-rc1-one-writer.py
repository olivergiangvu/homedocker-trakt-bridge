#!/usr/bin/env python3
"""G3 one-writer handoff: strictly read-only production Docker metadata audit.

This is NOT a deployment, Docker image load, backup, or migration tool.
Run once immediately before separately authorized RC.1 cutover.
No env/labels/mount paths/credentials are printed.
"""
import json
import subprocess

RC6 = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
       "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")


def docker_json(*args):
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=20,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("DOCKER_READ_UNAVAILABLE")
    return json.loads(proc.stdout)


def assess(bridge, image, running):
    ref_ok = bridge.get("Config", {}).get("Image") == RC6
    local_id = bridge.get("Image")
    image_ok = bool(local_id and image.get("Id") == local_id
                    and RC6 in image.get("RepoDigests", []))
    mounts = [x for x in bridge.get("Mounts", [])
              if x.get("Destination") == "/app/data"]
    data_ok = (len(mounts) == 1 and
               mounts[0].get("Type") in ("volume", "bind") and
               bool(mounts[0].get("Source")))
    data_source = mounts[0].get("Source") if data_ok else None
    own_id = bridge.get("Id")
    # Check ALL running Docker containers, not merely names starting with
    # trakt: two writers may use different Compose projects/container names.
    peers = [c for c in running if c.get("Id") != own_id
             and any(m.get("Source") == data_source
                     for m in c.get("Mounts", []))] if data_source else []
    labels = bridge.get("Config", {}).get("Labels") or {}
    compose_ok = bool(labels.get("com.docker.compose.project")
                      and labels.get("com.docker.compose.project.config_files")
                      and labels.get("com.docker.compose.project.working_dir")
                      and labels.get("com.docker.compose.service") == "trakt-bridge")
    env = dict(item.split("=", 1) for item in
               bridge.get("Config", {}).get("Env", []) if "=" in item)
    config_ok = (env.get("PULL_IDENTITY_MODE") == "trakt"
                 and env.get("AIO_RECONCILER_MODE") == "compare"
                 and env.get("CANONICAL_ON_DEMAND_VERIFY", "false").lower()
                 == "false")
    running_ok = bridge.get("State", {}).get("Running") is True
    passed = (ref_ok and image_ok and running_ok and data_ok and not peers
              and compose_ok and config_ok)
    return {
        "audit": "V140_RC1_ONE_WRITER_READ_ONLY",
        "rc6_exact_attested": ref_ok and image_ok and running_ok,
        "compose_managed": compose_ok,
        "bridge_mount_identified": data_ok,
        "other_running_containers_sharing_bridge_data": len(peers),
        "canonical_writer_flags": config_ok,
        "one_writer_preflight": "PASS" if passed else "STOP_REVIEW",
        "backup_freshness": "SEPARATE_SCHEDULED_JOB",
        "rc1_artifact": "SEPARATE_CHECKSUM_CHECK",
        "authorizes_cutover": False,
        "production_writes": 0,
    }


def main():
    try:
        c = docker_json("container", "inspect", "trakt-bridge")
        if len(c) != 1:
            raise RuntimeError("EXACT_BRIDGE_NOT_FOUND")
        bridge = c[0]
        images = docker_json("image", "inspect", bridge["Image"])
        if len(images) != 1:
            raise RuntimeError("IMAGE_NOT_FOUND")
        running_ids = subprocess.run(
            ["docker", "ps", "-q", "--no-trunc"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if running_ids.returncode:
            raise RuntimeError("RUNNING_INVENTORY_UNAVAILABLE")
        ids = [x for x in running_ids.stdout.splitlines() if x]
        # Docker inspect without identifiers in report; no peer credentials
        # are ever printed or persisted.
        running = docker_json("container", "inspect", *ids) if ids else []
        report = assess(bridge, images[0], running)
    except (RuntimeError, KeyError, ValueError, subprocess.TimeoutExpired):
        report = {"audit": "V140_RC1_ONE_WRITER_READ_ONLY",
                  "one_writer_preflight": "STOP_REVIEW",
                  "production_writes": 0, "authorizes_cutover": False}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["one_writer_preflight"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
