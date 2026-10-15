#!/usr/bin/env python3
"""Read-only HomeDocker v1.4 resource baseline (no auth, watched history or tokens)."""
import json
import re
import subprocess
from pathlib import Path

TARGETS = (
    "trakt-bridge", "aiostreams", "remux", "trellis",
    "homedocker-nextup-canary",
    "homedocker-nextup-auth-canary",
    "homedocker-nextup-c4fix-canary",
    "homedocker-resume-contract-canary",
)

def docker(*args, timeout=30):
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True,
        timeout=timeout, check=True
    ).stdout

def host_memory():
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        matched = re.match(r"^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)\s+kB$", line)
        if matched:
            mem[matched.group(1)] = round(int(matched.group(2)) / 1024)
    return mem

def main():
    names = set(docker("ps", "--format", "{{.Names}}").splitlines())
    selected = [name for name in TARGETS if name in names]
    items = {}
    if selected:
        result = docker(
            "stats", "--no-stream", "--format", "{{json .}}", *selected,
            timeout=75,
        )
        for line in result.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            name = obj.get("Name")
            if name not in selected:
                continue
            items[name] = {
                "memory": obj.get("MemUsage", "unknown"),
                "cpu": obj.get("CPUPerc", "unknown"),
                "pids": obj.get("PIDs", "unknown"),
            }
    images = {}
    for name in selected:
        raw = docker("inspect", name, "--format",
                     "{{json .Config.Image}}|{{json .Image}}")
        parts = raw.strip().split("|")
        if len(parts) != 2:
            continue
        # Do not print hashes; only indicate image is pinned by digest.
        tag = json.loads(parts[0])
        images[name] = {
            "configured_by_digest": "@sha256:" in tag,
            "container_running": True,
        }
    out = {
        "audit": "V140_RESOURCE_BASELINE",
        "host_memory_mib": host_memory(),
        "active_target_containers": len(selected),
        "resource_snapshots": items,
        "image_provenance_indicators": images,
        "measurement": "single snapshot, not an idle/peak/p95 benchmark",
        "next": "Compare same script before and after exact canary cleanup",
        "writes": 0,
        "restart_or_stop": 0,
        "tokens_or_watch_history_read": False,
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
