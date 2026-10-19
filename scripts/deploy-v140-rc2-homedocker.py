#!/usr/bin/env python3
"""RC1 -> RC2 ONE-BRIDGE HomeDocker promotion. Read-only unless explicitly --apply.

No GHCR push/tag, no additional writers, no backup re-audit, no AIO/nginx
change, no Gateway auto-enablement. On post-change failure, attempt
automatic restoration of the original RC1 Compose .env and service.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CONFIRM = "AUTHORIZE_V140_RC2_SINGLE_BRIDGE_CUTOVER"
RC1 = re.compile(r"^homedocker-trakt-bridge:v1\.4\.0-rc\.1-ci-[a-f0-9]{12}$")
RC2_VERSION = "1.4.0-rc.2"


def import_ops(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  HERE / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


previous_ops = import_ops("deploy-v140-rc1-homedocker.py")
old = previous_ops.old


class Stop(Exception):
    pass


def artifact(run_id, expected_head):
    if not re.fullmatch(r"[0-9]{8,14}", run_id):
        raise Stop("INVALID_RC2_RUN_ID")
    if not re.fullmatch(r"[a-f0-9]{40}", expected_head):
        raise Stop("INVALID_RC2_SOURCE_HEAD")
    root = Path.home() / ".local/share/homedocker/rc2-staging" / run_id
    if root.is_symlink() or not root.is_dir() or (root.stat().st_mode & 0o077):
        raise Stop("RC2_PRIVATE_STAGING_MISSING")
    for name in ("bridge-rc2.docker.tar", "SHA256SUMS", "IMAGE-METADATA.txt"):
        p = root / name
        if p.is_symlink() or not p.is_file() or not stat.S_ISREG(p.stat().st_mode):
            raise Stop("RC2_ARTIFACT_INVALID")
    image = root / "bridge-rc2.docker.tar"
    if not 0 < image.stat().st_size <= 210 * 1024 * 1024:
        raise Stop("RC2_ARTIFACT_SIZE_INVALID")
    sums = (root / "SHA256SUMS").read_text(encoding="utf8").strip()
    m = re.fullmatch(r"([a-f0-9]{64})  bridge-rc2\.docker\.tar", sums)
    if not m:
        raise Stop("RC2_CHECKSUM_FORMAT_INVALID")
    h = hashlib.sha256()
    with image.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    if h.hexdigest() != m.group(1):
        raise Stop("RC2_CHECKSUM_MISMATCH")
    meta = {}
    for line in (root / "IMAGE-METADATA.txt").read_text("utf8").splitlines():
        if "=" not in line:
            raise Stop("RC2_METADATA_INVALID")
        key, value = line.split("=", 1)
        if key in meta:
            raise Stop("RC2_METADATA_DUPLICATE")
        meta[key] = value
    source_checkout = meta.get("source_checkout_sha", "")
    tag = meta.get("image_tag", "")
    local_id = meta.get("local_image_id", "")
    if (meta.get("source_pr_head_sha") != expected_head
        or not re.fullmatch(r"[a-f0-9]{40}", source_checkout)
        or tag != "homedocker-trakt-bridge:v1.4.0-rc.2-ci-" + source_checkout[:12]
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", local_id)
        or meta.get("stable_release_hold") != "ACTIVE"
        or meta.get("publication") != "NONE"
        or meta.get("registry_manifest_digest") != "NOT_AVAILABLE_UNPUBLISHED"):
        raise Stop("RC2_PROVENANCE_MISMATCH")
    return {"archive": image, "tag": tag, "image_id": local_id}


def exact_rc1_one_writer():
    try:
        prior, cmd, env, files, db_dir = old.inspect_stack()
        ref = prior.get("Config", {}).get("Image", "")
        if not RC1.fullmatch(ref) or not prior.get("State", {}).get("Running"):
            raise Stop("RC1_RUNTIME_NOT_QUALIFIED")
        image = json.loads(old.docker("image", "inspect", ref))[0]
        if image.get("Id") != prior.get("Image"):
            raise Stop("RC1_IMAGE_ID_CHANGED")
        if not previous_ops.runtime_ready("1.4.0-rc.1"):
            raise Stop("RC1_NOT_READY")
        old.check_flags(prior)
        previous_ops.assert_gateway_off(cmd)
        sources = [m.get("Source") for m in prior.get("Mounts", [])
                   if m.get("Destination") == "/app/data"]
        if len(sources) != 1 or not sources[0]:
            raise Stop("RC1_VOLUME_UNQUALIFIED")
        ids = old.docker("ps", "-q", "--no-trunc").splitlines()
        running = json.loads(old.docker("container", "inspect", *ids)) if ids else []
        other = any(c.get("Id") != prior.get("Id")
                    and any(m.get("Source") == sources[0]
                            for m in c.get("Mounts", [])) for c in running)
        if other:
            raise Stop("ANOTHER_CONTAINER_SHARES_BRIDGE_DB")
        aio = old.container("aiostreams")
        if (aio.get("State", {}).get("Running") is not True
            or aio.get("Config", {}).get("Image") != "ghcr.io/viren070/aiostreams:latest"):
            raise Stop("AIO_IMAGE_OR_STATE_CHANGED")
    except (old.Stop, IndexError, ValueError, KeyError) as e:
        raise Stop("RC1_INSPECTION_FAILED") from e
    return prior, cmd, env, aio["Image"]


def rollback(saved, env, cmd, expected_ref, expected_id):
    try:
        old.run("sudo", "-n", "cp", "-p", str(saved), str(env))
        old.run(*cmd, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not previous_ops.runtime_ready("1.4.0-rc.1", retries=30):
            raise Stop("RC1_ROLLBACK_NOT_READY")
        recovered = old.container("trakt-bridge")
        if (recovered["Config"]["Image"] != expected_ref
            or recovered["Image"] != expected_id):
            raise Stop("RC1_ROLLBACK_IMAGE_CHANGED")
        print("RC1_ROLLBACK=HEALTHY")
    except (Stop, old.Stop, KeyError, subprocess.SubprocessError) as e:
        print("RC1_ROLLBACK=MANUAL_RECOVERY_REQUIRED", file=sys.stderr)
        raise Stop("AUTOMATIC_RC1_ROLLBACK_FAILED") from e


def apply(info, prior, cmd, env, aio_id):
    old.run("docker", "load", "-i", str(info["archive"]))
    checked = json.loads(old.docker("image", "inspect", info["tag"]))
    if len(checked) != 1 or checked[0].get("Id") != info["image_id"]:
        raise Stop("RC2_LOADED_IMAGE_MISMATCH")
    print("RC2_IMAGE_LOAD=VERIFIED")
    root = Path.home() / ".local/share/homedocker/release-backups/rc2-cutover"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    protected = root / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    protected.mkdir(mode=0o700)
    saved = protected / "compose.env.rc1"
    old.run("sudo", "-n", "cp", "-p", str(env), str(saved))
    old.run("sudo", "-n", "chmod", "600", str(saved))
    original_ref, original_id = prior["Config"]["Image"], prior["Image"]
    try:
        # Rollback eligibility starts BEFORE atomic .env replacement.
        old.set_image(env, info["tag"])
        parsed = json.loads(old.run(*cmd, "config", "--format", "json"))
        if parsed["services"]["trakt-bridge"]["image"] != info["tag"]:
            raise Stop("RC2_COMPOSE_IMAGE_MISMATCH")
        previous_ops.assert_gateway_off(cmd)
        old.run(*cmd, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not previous_ops.runtime_ready(RC2_VERSION, retries=30):
            raise Stop("RC2_READINESS_FAILED")
        now = old.container("trakt-bridge")
        if (now["Config"]["Image"] != info["tag"]
            or now["Image"] != info["image_id"]
            or old.container("aiostreams")["Image"] != aio_id):
            raise Stop("RC2_IMAGE_OR_AIO_CHANGED")
        old.check_flags(now)
        previous_ops.assert_gateway_off(cmd)
    except Exception as exc:
        print("RC2_CUTOVER_FAILED=ATTEMPT_RC1_ROLLBACK")
        rollback(saved, env, cmd, original_ref, original_id)
        raise Stop("RC2_CUTOVER_FAILED_RC1_RESTORED") from exc
    print("RC2_CUTOVER=HEALTHY")
    print("APP_VERSION=1.4.0-rc.2")
    print("GATEWAY=OFF")
    print("AIO_CHANGED=false")
    print("STABLE_RELEASE_HOLD=ACTIVE")
    print("RC1_ROLLBACK_ENV_COPY=PRIVATE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.apply and args.confirm != CONFIRM:
        raise Stop("EXPLICIT_RC2_CUTOVER_APPROVAL_REQUIRED")
    if not args.apply and args.confirm:
        raise Stop("CONFIRM_NOT_ALLOWED_FOR_READ_ONLY")
    image = artifact(args.run_id, args.expected_head)
    previous, cmd, env, aio_id = exact_rc1_one_writer()
    print("RC2_ARTIFACT_SHA256=PASS")
    print("RC1_SINGLE_WRITER_PREFLIGHT=PASS")
    if not args.apply:
        print("MODE=READ_ONLY_PREFLIGHT")
        print("PRODUCTION_CHANGES=0")
        return
    if not previous_ops.runtime_ready("1.4.0-rc.1"):
        raise Stop("RC1_READINESS_CHANGED")
    apply(image, previous, cmd, env, aio_id)


if __name__ == "__main__":
    try:
        main()
    except (Stop, old.Stop, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print("STOP: " + str(e), file=sys.stderr)
        raise SystemExit(1)
