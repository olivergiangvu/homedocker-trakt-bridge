#!/usr/bin/env python3
"""Read-only audit of untagged Docker images, retaining all rollback candidates.

Reports current container references, age, disk size, a restricted OCI title,
and nearest-running-image shared-layer similarity. DOES NOT remove anything.
"""
import argparse
import json
import subprocess
import sys


def docker(*args):
    cmd = subprocess.run(["docker", *args], capture_output=True, text=True)
    if cmd.returncode:
        raise RuntimeError("Docker read failed: " + " ".join(args[:3]))
    return cmd.stdout


def images_from_json(raw):
    data = json.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError("Docker inspect must return an array")
    return data


def layer_similarity(one, another):
    a = (one.get("RootFS") or {}).get("Layers") or []
    b = (another.get("RootFS") or {}).get("Layers") or []
    same = 0
    for x, y in zip(a, b):
        if x != y:
            break
        same += 1
    return same, len(a), len(b)


def classify(candidate, containers, active_images):
    image_id = candidate["Id"]
    users = sorted(c["Name"].lstrip("/") for c in containers if c["Image"] == image_id)
    if users:
        return "IN_USE_NEVER_DELETE", ",".join(users), ""
    closest = ""
    best = 0
    current = 0
    for image in active_images:
        n, count, _ = layer_similarity(candidate, image)
        if n > best:
            best = n
            current = count
            closest = ",".join(sorted(
                c["Name"].lstrip("/") for c in containers if c["Image"] == image["Id"]
            ))
    # Shared *base* layers are not proof of belonging to the same application.
    hint = f"{closest}:shared_base_layers={best}/{current}" if best >= 3 else "none"
    return "UNREFERENCED_REVIEW_ROLLBACK", "-", hint


def collect():
    ids = docker("image", "ls", "--filter", "dangling=true",
                 "--no-trunc", "--quiet").splitlines()
    ids = list(dict.fromkeys(x for x in ids if x.startswith("sha256:")))
    containers = docker("ps", "-aq").splitlines()
    con = images_from_json(docker("container", "inspect", *containers)) if containers else []
    active_ids = list(dict.fromkeys(c["Image"] for c in con))
    active_images = images_from_json(docker("image", "inspect", *active_ids)) if active_ids else []
    dangling = images_from_json(docker("image", "inspect", *ids)) if ids else []
    return dangling, con, active_images


def report(dangling, containers, active_images):
    print("HOMEDOCKER_DANGLING_IMAGE_OWNER_AUDIT_V1")
    print("MODE=READ_ONLY")
    print("NO_REMOVAL_OR_PULL=true")
    print("DANGLING_COUNT=" + str(len(dangling)))
    for image in sorted(dangling, key=lambda x: x.get("Created", ""), reverse=True):
        state, users, similarity = classify(image, containers, active_images)
        title = str((image.get("Config") or {}).get("Labels", {}).get(
            "org.opencontainers.image.title") or "unknown")
        title = "".join(ch for ch in title if ch.isprintable())[:80]
        print("IMAGE_ID=%s SIZE_MIB=%.1f CREATED=%s STATUS=%s" %
              (image["Id"][:19], image["Size"] / 1048576,
               str(image.get("Created", ""))[:20], state))
        print("  CONTAINER_REFS=%s OCI_TITLE=%s SIMILARITY_HINT=%s" %
              (users, title, similarity))
    print("NOTE=UNREFERENCED means no current container attachment; it is NOT an approval to delete a rollback image.")
    print("NOTE=Image sizes share layers; do not sum sizes to estimate recovered disk.")


def main():
    try:
        images, containers, active = collect()
        report(images, containers, active)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        sys.exit("STOP: no deletion attempted: " + str(exc))


if __name__ == "__main__":
    main()
