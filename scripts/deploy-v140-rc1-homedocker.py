#!/usr/bin/env python3
"""HomeDocker Bridge 1.4.0-rc.1 controlled *single-writer* upgrade.

Default: read-only preflight; no image load, backups or writes.
--apply: changes ONLY existing Bridge Compose image reference after an exact
authorization phrase, with private configuration rollback and automatic
RC6 restart attempt on failure. Does not deploy AIO/nginx/other stacks.
Never publish/stamp v1.4 tags or enable the experimental Gateway.
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

RUN_ID = "39118523041"
PR_HEAD = "54d17867d2fa521c577cd3601b9dd6daaee37f0f"
APPROVAL = "AUTHORIZE_V140_RC1_BRIDGE_ONLY_CUTOVER"
ARTIFACT = Path.home() / ".local/share/homedocker/rc1-staging" / RUN_ID
RC6 = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
       "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")
RC_VERSION = "1.4.0-rc.1"
SCRIPT_DIR = Path(__file__).resolve().parent


def load_script(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Proven v1.3 one-Compose deployment primitives, not its old stable/tag code.
old = load_script("deploy-v1.3.0-homedocker.py", "old_stable_ops")
writer = load_script("audit-v140-rc1-one-writer.py", "rc1_single_writer")


class Stop(Exception):
    pass


def regular_private_file(path, max_bytes):
    if path.is_symlink():
        raise Stop("ARTIFACT_SYMLINK_REJECTED")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size < 1 or st.st_size > max_bytes:
        raise Stop("ARTIFACT_FILE_INVALID")
    return st


def artifact_identity(folder=ARTIFACT):
    if folder.is_symlink() or not folder.is_dir():
        raise Stop("ARTIFACT_DIRECTORY_MISSING")
    if (folder.stat().st_mode & 0o077) != 0:
        raise Stop("ARTIFACT_DIRECTORY_NOT_PRIVATE")
    image_path = folder / "bridge-rc1.docker.tar"
    regular_private_file(image_path, 210 * 1024 * 1024)
    regular_private_file(folder / "SHA256SUMS", 1024)
    regular_private_file(folder / "IMAGE-METADATA.txt", 4096)
    content = (folder / "SHA256SUMS").read_text("utf-8").strip()
    if not re.fullmatch(r"[a-f0-9]{64}  bridge-rc1\.docker\.tar", content):
        raise Stop("ARTIFACT_CHECKSUM_FORMAT")
    want = content.split(" ", 1)[0]
    digest = hashlib.sha256()
    with image_path.open("rb") as src:
        while True:
            piece = src.read(1024 * 1024)
            if not piece:
                break
            digest.update(piece)
    if digest.hexdigest() != want:
        raise Stop("ARTIFACT_CHECKSUM_MISMATCH")
    meta = {}
    for line in (folder / "IMAGE-METADATA.txt").read_text("utf-8").splitlines():
        if "=" not in line:
            raise Stop("ARTIFACT_METADATA_INVALID")
        key, value = line.split("=", 1)
        if key in meta:
            raise Stop("ARTIFACT_METADATA_DUPLICATE")
        meta[key] = value
    tag = meta.get("image_tag", "")
    checkout = meta.get("source_checkout_sha", "")
    local_id = meta.get("local_image_id", "")
    if (meta.get("source_pr_head_sha") != PR_HEAD
        or not re.fullmatch(r"[a-f0-9]{40}", checkout)
        or tag != "homedocker-trakt-bridge:v1.4.0-rc.1-ci-" + checkout[:12]
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", local_id)
        or meta.get("publication") != "NONE"
        or meta.get("stable_release_hold") != "ACTIVE"
        or meta.get("registry_manifest_digest") != "NOT_AVAILABLE_UNPUBLISHED"):
        raise Stop("ARTIFACT_PROVENANCE_MISMATCH")
    return {"tag": tag, "image_id": local_id, "archive": image_path}


def docker_json(*args):
    try:
        rows = json.loads(old.docker(*args))
    except (old.Stop, KeyError, ValueError) as exc:
        raise Stop("DOCKER_READ_FAILED") from exc
    return rows


def single_writer():
    try:
        bridge = old.container("trakt-bridge")
        image = docker_json("image", "inspect", bridge["Image"])[0]
        ids = old.docker("ps", "-q", "--no-trunc").splitlines()
        peers = docker_json("container", "inspect", *ids) if ids else []
        result = writer.assess(bridge, image, peers)
        if result["one_writer_preflight"] != "PASS":
            raise Stop("ONE_WRITER_STOP_REVIEW")
        return bridge
    except (old.Stop, KeyError, IndexError) as exc:
        raise Stop("ONE_WRITER_INVENTORY_UNAVAILABLE") from exc


def assert_gateway_off(compose_command):
    try:
        rendered = json.loads(old.run(*compose_command, "config", "--format", "json"))
        service = (rendered.get("services") or {}).get("trakt-bridge")
        if not isinstance(service, dict):
            raise Stop("BRIDGE_SERVICE_NOT_RENDERED")
        env = service.get("environment") or {}
        if isinstance(env, list):
            env = dict(v.split("=", 1) for v in env if isinstance(v, str) and "=" in v)
        if not isinstance(env, dict):
            raise Stop("BRIDGE_ENVIRONMENT_INVALID")
        for k, allowed in (
            ("JELLYFIN_GATEWAY_ENABLED", ("", "0", "false", "no", "off")),
            ("JELLYFIN_GATEWAY_LEGACY_IMPORT", ("", "off")),
            ("JELLYFIN_GATEWAY_RESUME_PASSTHROUGH", ("", "0", "false", "no", "off")),
        ):
            if str(env.get(k) or "").strip().lower() not in allowed:
                raise Stop("UNSAFE_GATEWAY_CONFIG")
    except (old.Stop, KeyError, ValueError, TypeError) as exc:
        if isinstance(exc, Stop):
            raise
        raise Stop("COMPOSE_RENDER_UNAVAILABLE") from exc


def runtime_ready(version, retries=1):
    for i in range(retries):
        try:
            old.check_ready(version)
            return True
        except old.Stop:
            if i + 1 < retries:
                time.sleep(2)
    return False


def preflight():
    artifact = artifact_identity()
    try:
        previous, command, envfile, paths, data_source = old.inspect_stack()
        observed = single_writer()
        if previous["Id"] != observed["Id"]:
            raise Stop("BRIDGE_REPLACED_SINCE_INSPECT")
        if previous.get("Config", {}).get("Image") != RC6:
            raise Stop("RC6_IMAGE_CHANGED")
        aio = old.container("aiostreams")
        if aio.get("State", {}).get("Running") is not True:
            raise Stop("AIO_NOT_RUNNING")
        if aio.get("Config", {}).get("Image") != "ghcr.io/viren070/aiostreams:latest":
            raise Stop("AIO_REFERENCE_CHANGED")
        if not runtime_ready("1.3.0-rc.6"):
            raise Stop("RC6_NOT_READY")
        assert_gateway_off(command)
        # A rollback must not depend on pulling or replacing a mutable tag.
        docker_json("image", "inspect", RC6)
    except old.Stop as exc:
        raise Stop("RC6_STACK_PREFLIGHT_FAILED") from exc
    return artifact, previous, command, envfile, aio["Image"]


def verify_loaded_image(artifact):
    rows = docker_json("image", "inspect", artifact["tag"])
    if len(rows) != 1 or rows[0].get("Id") != artifact["image_id"]:
        raise Stop("LOADED_RC_IMAGE_ID_MISMATCH")


def rollback(old_env, envfile, command):
    try:
        old.run("sudo", "-n", "cp", "-p", str(old_env), str(envfile))
        old.run(*command, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not runtime_ready("1.3.0-rc.6", retries=30):
            raise Stop("RC6_ROLLBACK_READINESS_FAILED")
        if old.container("trakt-bridge")["Config"]["Image"] != RC6:
            raise Stop("RC6_ROLLBACK_IMAGE_MISMATCH")
        print("RC6_ROLLBACK=HEALTHY")
    except (old.Stop, Stop, KeyError, subprocess.SubprocessError) as exc:
        print("RC6_ROLLBACK=MANUAL_RECOVERY_REQUIRED", file=sys.stderr)
        raise Stop("AUTOMATIC_ROLLBACK_FAILED") from exc


def apply(artifact, previous, command, envfile, aio_id):
    # Only after human approval; Docker load does not start containers.
    old.run("docker", "load", "-i", str(artifact["archive"]))
    verify_loaded_image(artifact)
    print("RC1_DOCKER_LOAD=CHECKED_ONLY")
    # Never start two Bridge writers: force-recreate the existing service
    # under the same Compose project, rather than starting a second stack.
    backup_root = Path.home() / ".local/share/homedocker/release-backups/rc1-cutover"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    folder = backup_root / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    folder.mkdir(mode=0o700)
    old_env = folder / "compose.env.rc6"
    old.run("sudo", "-n", "cp", "-p", str(envfile), str(old_env))
    old.run("sudo", "-n", "chmod", "600", str(old_env))
    # Short-lived rollback copy of Compose/.env is not a second scheduled
    # backup or another Restic/OneDrive audit.
    mutated = False
    try:
        old.set_image(envfile, artifact["tag"])
        mutated = True
        rendered = json.loads(old.run(*command, "config", "--format", "json"))
        if rendered["services"]["trakt-bridge"]["image"] != artifact["tag"]:
            raise Stop("COMPOSE_RC_IMAGE_MISMATCH")
        assert_gateway_off(command)
        old.run(*command, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not runtime_ready(RC_VERSION, retries=30):
            raise Stop("RC1_READINESS_FAILED")
        now = old.container("trakt-bridge")
        if (now["Config"]["Image"] != artifact["tag"]
            or now["Image"] != artifact["image_id"]
            or old.container("aiostreams")["Image"] != aio_id):
            raise Stop("RC1_IMAGE_OR_AIO_CHANGED")
        old.check_flags(now)
        assert_gateway_off(command)
    except Exception as exc:
        if mutated:
            print("CUTOVER_FAILED=RC6_ROLLBACK_ATTEMPT")
            rollback(old_env, envfile, command)
        raise Stop("CUTOVER_FAILED_AND_RC6_RESTORED") from exc
    print("RC1_CUTOVER=HEALTHY")
    print("APP_VERSION=1.4.0-rc.1")
    print("GATEWAY=OFF")
    print("AIO_CHANGED=false")
    print("STABLE_RELEASE_HOLD=ACTIVE")
    print("ROLLBACK_ENV_COPY=PRIVATE")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args()
    if args.apply and args.confirm != APPROVAL:
        raise Stop("EXPLICIT_CUTOVER_APPROVAL_REQUIRED")
    if not args.apply and args.confirm:
        raise Stop("APPROVAL_ONLY_VALID_WITH_APPLY")
    artifact, previous, command, envfile, aio_id = preflight()
    print("RC1_ARTIFACT_CHECKSUM=PASS")
    print("RC6_ONE_WRITER_PREFLIGHT=PASS")
    print("RC1_GATEWAY_DISABLED=TRUE")
    if not args.apply:
        print("MODE=PREFLIGHT_READ_ONLY")
        print("CUTOVER_AUTHORIZED=false")
        return
    # Check existing exact RC6 readiness once more immediately before the
    # first mutating action; no offline playback/scrobble test is requested.
    if not runtime_ready("1.3.0-rc.6"):
        raise Stop("RC6_READINESS_CHANGED")
    apply(artifact, previous, command, envfile, aio_id)


if __name__ == "__main__":
    try:
        main()
    except (Stop, old.Stop, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
