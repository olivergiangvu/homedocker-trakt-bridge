#!/usr/bin/env python3
"""Read-only Docker image-retention audit for Trakt Bridge v1.4.

Never deletes/prunes; an unreferenced image may still be a recovery asset.
Does not print private registry URLs, tags, mounts, tokens, or Docker env.
"""
import json
import subprocess
from collections import Counter

def docker(*args):
    p = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=90
    )
    if p.returncode:
        raise RuntimeError("docker audit command failed: " + args[0])
    return p.stdout.strip()

def referenced_image_ids():
    container_ids = docker("ps", "-aq").splitlines()
    if not container_ids:
        return set()
    # Request only container image IDs: never fetch environment or mounts.
    output = docker("inspect", "--format", "{{.Image}}", *container_ids)
    return {line for line in output.splitlines() if line.startswith("sha256:")}

def classify(tags):
    values = [x.lower() for x in tags if isinstance(x, str)]
    if any("trakt-bridge" in x for x in values):
        return "trakt_bridge_rollback_preserve"
    if any("homedocker-nextup" in x for x in values):
        return "rc7_image_manual_review"
    if not values:
        return "untagged_unknown_provenance"
    return "other_service_or_rollback_manual_review"

def image_inventory():
    images = sorted(set(x for x in docker(
        "image", "ls", "-a", "--no-trunc", "--format", "{{.ID}}"
    ).splitlines() if x.startswith("sha256:")))
    owners = referenced_image_ids()
    categories = Counter()
    unused = []
    for image_id in images:
        raw = docker(
            "image", "inspect", "--format",
            "{{json .RepoTags}}|{{.Size}}", image_id
        )
        tags_text, size_text = raw.rsplit("|", 1)
        tags = json.loads(tags_text) or []
        size = int(size_text)
        if image_id in owners:
            categories["attached_to_any_container"] += 1
            continue
        kind = classify(tags)
        categories[kind] += 1
        unused.append({
            "image_id_prefix": image_id[7:19],
            "role": kind,
            "virtual_size_mib": round(size / 1048576, 1),
            "has_tag": bool(tags),
            "safe_to_delete_without_review": False,
        })
    return {
        "total_image_ids": len(images),
        "attached_image_ids": sum(1 for x in images if x in owners),
        "unused_image_ids": len(unused),
        "image_categories": dict(categories),
        # Bounded and anonymized; virtual image sizes overlap via layers.
        "unused_review_candidates": sorted(
            unused, key=lambda x: x["virtual_size_mib"], reverse=True
        )[:25],
    }

def main():
    result = image_inventory()
    print(json.dumps({
        "audit": "V140_UNUSED_IMAGE_REVIEW",
        **result,
        "unused_not_equal_safe_to_delete": True,
        "virtual_sizes_not_additive": True,
        "recommended_action": "manual provenance and rollback check; no deletion",
        "docker_images_deleted": 0,
        "volumes_deleted": 0,
        "networks_deleted": 0,
        "containers_restarted": 0,
        "secret_values_printed": False,
    }, indent=2))

if __name__ == "__main__":
    main()
