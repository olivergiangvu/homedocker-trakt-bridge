#!/usr/bin/env python3
"""RC2 -> v1.4.0 immutable-GHCR single-Bridge upgrade, preserving Gateway Compose.

Default is read-only. --apply requires an exact operator confirmation.
Production secrets are never printed; snapshot copies are root-only. An exact
RC2 rollback is attempted on any post-mutation error. No AIO/Trakt/nginx writes.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler

APP = "trakt-bridge"
PROJECT = "homedocker-trakt-bridge"
DIR = Path("/opt/homedocker/stacks/homedocker-trakt-bridge")
FILES = (DIR / "compose.yml", DIR / "compose.gateway.yml")
ENV = DIR / ".env"
PREVIOUS_REF = "homedocker-trakt-bridge:v1.4.0-rc.2-ci-a9defdd6c899"
PREVIOUS_ID = "sha256:91ac17af1edc15297d45caad466b76d07f2edb4091314e68260694b6b87c417c"
STABLE_REF = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
              "sha256:61c28bd22155950cacfd7ac47e5d5259a74e55c5b99c3dc4b63d95be524f2c77")
CONFIRM = "AUTHORIZE_V140_STABLE_BRIDGE_ONLY_RC2_ROLLBACK"
BACKUP_ROOT = Path("/var/backups/homedocker-trakt-bridge/stable-v1.4.0")
LOCK = Path("/run/lock/homedocker-trakt-bridge-stable-v1.4.0.lock")
HTTP = build_opener(ProxyHandler({}))
EXPECTED_GATEWAY = {
    "JELLYFIN_GATEWAY_ENABLED": "true",
    "JELLYFIN_GATEWAY_LEGACY_IMPORT": "off",
    "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH": "false",
    "JELLYFIN_GATEWAY_FIRST_PAGE_MODE": "short_first_page_only",
}


class Stop(Exception):
    pass


def run(*argv, timeout=120):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise Stop("COMMAND_TIMEOUT_OR_UNAVAILABLE") from exc
    if p.returncode:
        # Never expose stderr: Docker/Compose may contain credentials.
        raise Stop("COMMAND_FAILED:" + str(argv[0]) + ":" + str(p.returncode))
    return p.stdout


def docker(*args, timeout=45):
    return run("docker", *args, timeout=timeout)


def inspect(name):
    result = json.loads(docker("inspect", name))
    if len(result) != 1:
        raise Stop("INSPECT_COUNT_INVALID")
    return result[0]


def compose_command():
    return ["docker", "compose", "-p", PROJECT, "--project-directory", str(DIR),
            "-f", str(FILES[0]), "-f", str(FILES[1])]


def rendered():
    return json.loads(run(*compose_command(), "config", "--format", "json", timeout=45))


def regular_file(path):
    if path.is_symlink() or not path.is_file():
        raise Stop("FILE_MISSING_OR_SYMLINK:" + path.name)
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or (s.st_mode & 0o022):
        raise Stop("FILE_UNSAFE_MODE:" + path.name)


def env_pairs(info):
    return dict(s.split("=", 1) for s in info["Config"]["Env"] if "=" in s)


def no_other_writer(source, own_id):
    ids = docker("ps", "-q", "--no-trunc").splitlines()
    others = json.loads(docker("inspect", *ids)) if ids else []
    if any(c["Id"] != own_id
           and any(m.get("Source") == source for m in c.get("Mounts", []))
           for c in others):
        raise Stop("SECOND_CONTAINER_SHARES_BRIDGE_DATA")


def assert_http(version, *, attempts=1):
    for i in range(attempts):
        try:
            def request(endpoint):
                with HTTP.open(Request("http://127.0.0.1:7000/" + endpoint,
                                       headers={"Accept": "application/json"}),
                               timeout=5) as response:
                    if response.status != 200:
                        raise Stop("HTTP_NOT_200")
                    return json.loads(response.read(32768))
            h, r = request("health"), request("readiness")
            if (h.get("status") == "ok" and h.get("version") == version
                    and r.get("status") == "ready" and r.get("ready") is True
                    and r.get("database") == "ok"
                    and r.get("schemaVersion") == 1
                    and r.get("schemaExpected") == 1
                    and r.get("connectedProfiles") == 1):
                return
        except Exception:
            pass
        if i + 1 < attempts:
            time.sleep(2)
    raise Stop("BRIDGE_HTTP_VERSION_OR_READINESS_MISMATCH")


def preflight():
    if os.geteuid() != 0:
        raise Stop("ROOT_REQUIRED")
    if "TRAKT_BRIDGE_IMAGE" in os.environ or "COMPOSE_FILE" in os.environ:
        raise Stop("SHELL_COMPOSE_OVERRIDE_PRESENT")
    for p in (*FILES, ENV):
        regular_file(p)
    bridge = inspect(APP)
    aio = inspect("aiostreams")
    labels = bridge["Config"].get("Labels") or {}
    if (labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.service") != APP
            or labels.get("com.docker.compose.project.working_dir") != str(DIR)
            or labels.get("com.docker.compose.project.config_files")
            != ",".join(map(str, FILES))):
        raise Stop("COMPOSE_FILES_OR_PROJECT_CHANGED")
    if (bridge["Config"]["Image"] != PREVIOUS_REF
            or bridge["Image"] != PREVIOUS_ID
            or not bridge["State"]["Running"]
            or (bridge["State"].get("Health") or {}).get("Status") != "healthy"):
        raise Stop("RC2_IMAGE_OR_HEALTH_DRIFT")
    if not aio["State"]["Running"] or aio["Config"]["Image"] != "ghcr.io/viren070/aiostreams:latest":
        raise Stop("AIO_STATE_CHANGED")
    mounts = [m for m in bridge["Mounts"] if m.get("Destination") == "/app/data"]
    if len(mounts) != 1 or mounts[0].get("Type") not in ("volume", "bind"):
        raise Stop("BRIDGE_DATA_MOUNT_INVALID")
    data_dir = Path(mounts[0]["Source"])
    if not data_dir.is_absolute() or data_dir.is_symlink() or not data_dir.is_dir():
        raise Stop("BRIDGE_DATA_DIR_INVALID")
    regular_file(data_dir / "bridge.db")
    no_other_writer(str(data_dir), bridge["Id"])
    env_text = ENV.read_text(encoding="utf-8")
    if len(re.findall(r"(?m)^\s*TRAKT_BRIDGE_IMAGE\s*=", env_text)) != 1:
        raise Stop("COMPOSE_IMAGE_ENV_NOT_UNIQUE")
    if not any(re.search(r"\$\{TRAKT_BRIDGE_IMAGE(?=[:}?])", p.read_text(encoding="utf-8"))
               for p in FILES):
        raise Stop("COMPOSE_IMAGE_NOT_INTERPOLATED")
    config = rendered()
    if config.get("services", {}).get(APP, {}).get("image") != PREVIOUS_REF:
        raise Stop("COMPOSE_IMAGE_RUNTIME_DRIFT")
    settings = env_pairs(bridge)
    if any(settings.get(key, "").lower() != val for key, val in EXPECTED_GATEWAY.items()):
        raise Stop("GATEWAY_ENV_CHANGED")
    if (settings.get("PULL_IDENTITY_MODE") != "trakt"
            or settings.get("AIO_RECONCILER_MODE") != "compare"
            or settings.get("CANONICAL_ON_DEMAND_VERIFY", "false").lower() != "false"):
        raise Stop("TRAKT_WRITER_POLICY_CHANGED")
    assert_http("1.4.0-rc.2")
    networks = set(bridge["NetworkSettings"]["Networks"])
    if networks != {"homedocker-trakt-bridge_default", "media_core_net"}:
        raise Stop("GATEWAY_NETWORKS_CHANGED")
    if any("7101" in port and binding
           for port, binding in bridge["NetworkSettings"].get("Ports", {}).items()):
        raise Stop("GATEWAY_PORT_PUBLISHED")
    return dict(bridge=bridge, aio=aio, source=str(data_dir),
                config=config, networks=networks, settings=settings)


def protected_backup(state):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUP_ROOT.chmod(0o700)
    folder = BACKUP_ROOT / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    folder.mkdir(mode=0o700)
    for src, name in ((ENV, "compose.env.before"),
                      (FILES[0], "compose.yml.before"),
                      (FILES[1], "compose.gateway.yml.before")):
        shutil.copy2(src, folder / name)
        (folder / name).chmod(0o600)
    old_path = Path(state["source"]) / "bridge.db"
    backup_path = folder / "bridge.db.online-backup"
    source = sqlite3.connect(old_path.as_uri() + "?mode=ro", uri=True, timeout=20)
    target = sqlite3.connect(str(backup_path))
    try:
        if source.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise Stop("BRIDGE_SCHEMA_UNEXPECTED")
        source.backup(target, pages=256, sleep=0.05)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Stop("SQLITE_ONLINE_BACKUP_CORRUPT")
        if target.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise Stop("SQLITE_BACKUP_SCHEMA_CHANGED")
    finally:
        target.close()
        source.close()
    backup_path.chmod(0o600)
    (folder / "manifest.txt").write_text(
        "FROM_IMAGE=" + PREVIOUS_REF + "\nTO_IMAGE=" + STABLE_REF +
        "\nBACKUP_KIND=SQLITE_ONLINE_AND_COMPOSE_ENV\n", encoding="utf-8")
    (folder / "manifest.txt").chmod(0o600)
    return folder


def replace_env_bytes(new_bytes):
    st = ENV.stat()
    fd, temp = tempfile.mkstemp(prefix=".env.stable-v140-", dir=str(DIR))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.chown(temp, st.st_uid, st.st_gid)
        os.chmod(temp, stat.S_IMODE(st.st_mode))
        os.replace(temp, ENV)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def set_image(ref):
    data = ENV.read_bytes()
    if len(re.findall(rb"(?m)^TRAKT_BRIDGE_IMAGE=.*$", data)) != 1:
        raise Stop("IMAGE_VARIABLE_NOT_EXACT")
    changed = re.sub(rb"(?m)^TRAKT_BRIDGE_IMAGE=.*$",
                     b"TRAKT_BRIDGE_IMAGE=" + ref.encode("ascii"), data)
    if changed == data:
        raise Stop("IMAGE_UPDATE_WOULD_BE_NOOP")
    replace_env_bytes(changed)


def expected_composed_image_change(old):
    """Only allow image ref and the identical env_file-backed image variable.

    Docker Compose resolves TRAKT_BRIDGE_IMAGE from .env for interpolation and
    may *also* inject that exact key into service.environment via env_file.
    A shell-only image override does not model an actual .env update.
    """
    expected = json.loads(json.dumps(old))
    service = expected["services"][APP]
    if service.get("image") != PREVIOUS_REF:
        raise Stop("COMPOSE_BASE_IMAGE_DRIFT")
    service["image"] = STABLE_REF
    env = service.get("environment") or {}
    if "TRAKT_BRIDGE_IMAGE" in env:
        if env["TRAKT_BRIDGE_IMAGE"] != PREVIOUS_REF:
            raise Stop("COMPOSE_ENV_IMAGE_BASELINE_DRIFT")
        env["TRAKT_BRIDGE_IMAGE"] = STABLE_REF
    return expected


def verify_composed_diff(old):
    candidate = rendered()
    expected = expected_composed_image_change(old)
    if candidate != expected:
        # Never print rendered Compose (it contains injected .env secrets).
        raise Stop("COMPOSE_DIFF_BEYOND_IMAGE_AND_OWN_IMAGE_ENV")
    return candidate


def verify_new(state, image_id, *, version, image_ref, retries=35):
    assert_http(version, attempts=retries)
    new = None
    # Docker image HEALTHCHECK can remain "starting" for up to its first
    # 30-second scheduled probe even after HTTP readiness is already 200.
    for i in range(retries):
        new = inspect(APP)
        if (new["Config"]["Image"] != image_ref or new["Image"] != image_id):
            raise Stop("RUNTIME_IMAGE_ID_UNEXPECTED")
        if (new["State"]["Running"] and
                (new["State"].get("Health") or {}).get("Status") == "healthy"):
            break
        if i + 1 < retries:
            time.sleep(2)
    else:
        raise Stop("RUNTIME_NOT_HEALTHY_AFTER_TIMEOUT")
    if set(new["NetworkSettings"]["Networks"]) != state["networks"]:
        raise Stop("GATEWAY_NETWORK_REGRESSION")
    if ([m["Source"] for m in new["Mounts"] if m.get("Destination") == "/app/data"]
            != [state["source"]]):
        raise Stop("DATABASE_MOUNT_REGRESSION")
    if any("7101" in port and binding
           for port, binding in new["NetworkSettings"].get("Ports", {}).items()):
        raise Stop("GATEWAY_PORT_EXPOSED")
    new_env = env_pairs(new)
    prior_env = state["settings"]
    if "TRAKT_BRIDGE_IMAGE" in prior_env:
        if (prior_env["TRAKT_BRIDGE_IMAGE"] != PREVIOUS_REF
                or new_env.get("TRAKT_BRIDGE_IMAGE") != image_ref):
            raise Stop("IMAGE_ENV_NOT_EXPECTED_REF")
    elif "TRAKT_BRIDGE_IMAGE" in new_env:
        raise Stop("IMAGE_ENV_UNEXPECTEDLY_ADDED")
    if any(new_env.get(key) != value
           for key, value in prior_env.items()
           if key != "TRAKT_BRIDGE_IMAGE"
           and key.startswith(("JELLYFIN_GATEWAY_", "TRAKT_", "BRIDGE_",
                               "PULL_IDENTITY_MODE", "AIO_RECONCILER_MODE",
                               "CANONICAL_ON_DEMAND_VERIFY"))):
        raise Stop("BRIDGE_ENV_CHANGED")
    aio = inspect("aiostreams")
    if aio["Id"] != state["aio"]["Id"] or aio["Image"] != state["aio"]["Image"]:
        raise Stop("AIO_CONTAINER_OR_IMAGE_CHANGED")
    no_other_writer(state["source"], new["Id"])
    return new


def apply(state):
    print("STABLE_GHCR_PULL=START", flush=True)
    docker("pull", STABLE_REF, timeout=180)
    images = json.loads(docker("image", "inspect", STABLE_REF))
    if len(images) != 1 or STABLE_REF not in images[0].get("RepoDigests", []):
        raise Stop("STABLE_IMMUTABLE_DIGEST_NOT_ATTESTED")
    stable_id = images[0]["Id"]
    if stable_id == PREVIOUS_ID:
        raise Stop("STABLE_IMAGE_UNEXPECTEDLY_RC2")
    print("STABLE_GHCR_DIGEST=VERIFIED", flush=True)
    state = preflight()
    folder = protected_backup(state)
    print("SQLITE_AND_COMPOSE_BACKUP=PASS", flush=True)
    print("PRIVATE_ROLLBACK_DIR=" + str(folder), flush=True)
    state = preflight()  # recheck before any production modification
    edited = False
    stage = "IMAGE_ENV_UPDATE"
    try:
        set_image(STABLE_REF)
        edited = True
        stage = "COMPOSE_RENDERED_DIFF"
        verify_composed_diff(state["config"])
        stage = "DOCKER_COMPOSE_RECREATE"
        run(*compose_command(), "up", "-d", "--no-deps", "--no-build",
            "--pull", "never", "--force-recreate", APP, timeout=180)
        stage = "RUNTIME_HEALTH_AND_INVARIANTS"
        verify_new(state, stable_id, version="1.4.0", image_ref=STABLE_REF)
    except Exception as original:
        print("STABLE_FAILED_STAGE=" + stage, flush=True)
        print("STABLE_FAILED_CODE=" +
              (str(original) if isinstance(original, Stop)
               else type(original).__name__), flush=True)
        if edited:
            print("STABLE_CUTOVER=FAILED_ATTEMPT_RC2_ROLLBACK", flush=True)
            try:
                # Exact old bytes; preserve current file mode and owner.
                replace_env_bytes((folder / "compose.env.before").read_bytes())
                old_cfg = rendered()
                if old_cfg != state["config"]:
                    raise Stop("ROLLBACK_COMPOSE_DIFF")
                run(*compose_command(), "up", "-d", "--no-deps", "--no-build",
                    "--pull", "never", "--force-recreate", APP, timeout=180)
                verify_new(state, PREVIOUS_ID, version="1.4.0-rc.2",
                           image_ref=PREVIOUS_REF)
                print("RC2_ROLLBACK=HEALTHY", flush=True)
            except Exception as rollback_error:
                print("RC2_ROLLBACK=MANUAL_RECOVERY_REQUIRED", flush=True)
                raise Stop("CUTOVER_AND_ROLLBACK_FAILED") from rollback_error
            raise Stop("CUTOVER_FAILED_RC2_RESTORED") from original
        raise
    print("STABLE_CUTOVER=PASS")
    print("BRIDGE_VERSION=1.4.0")
    print("SINGLE_TRAKT_WRITER=PASS")
    print("GATEWAY_NETWORKS_AND_ENV=PRESERVED")
    print("AIO_CHANGED=NO")
    print("NGINX_CLIENTS_TRAKT_HISTORY_CHANGED=NO")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    a = parser.parse_args()
    if a.apply != (a.confirm == CONFIRM):
        raise Stop("EXACT_APPLY_CONFIRMATION_REQUIRED")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = preflight()
        print("RC2_GATEWAY_COMPOSE_PREFLIGHT=PASS", flush=True)
        print("READ_ONLY_PREFLIGHT=PASS" if not a.apply else "APPLY_AUTHORIZED=YES",
              flush=True)
        if a.apply:
            apply(state)
        else:
            print("PRODUCTION_CHANGES=0", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, sqlite3.Error, BlockingIOError) as exc:
        print("STOP=" + (str(exc) if isinstance(exc, Stop)
                          else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
