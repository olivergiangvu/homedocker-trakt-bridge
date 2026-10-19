#!/usr/bin/env python3
"""Provision RC2 internal Gateway transport; read-only default, guarded apply.

- One existing Bridge service/writer and unchanged image, SQLite, AIO, nginx.
- Adds a second *external* Docker network via an explicit Compose override;
  leaves the root-owned original compose.yml and .env byte-for-byte unchanged.
- Enables the internal Gateway listener only. UI identity filtering remains OFF.
- No published 7101 port, client reroute, token handling or media-state writes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
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
from urllib.request import build_opener, ProxyHandler, Request

APP = "trakt-bridge"
AIO = "aiostreams"
BASE_NETWORK = "homedocker-trakt-bridge_default"
AIO_NETWORK = "media_core_net"
MAPPING = Path("/etc/homedocker-trakt-bridge/nextup-auth/jellyfin-user.env")
OVERRIDE_NAME = "compose.gateway.yml"
CONFIRM = "AUTHORIZE_INTERNAL_V140_RC2_GATEWAY_LISTENER"
IMAGE_PATTERN = re.compile(r"homedocker-trakt-bridge:v1\.4\.0-rc\.2-ci-[a-f0-9]{12}\Z")
USER_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
PROFILE_PATTERN = re.compile(r"[a-f0-9]{24}\Z")
UPSTREAM = "http://aiostreams:3000/jellyfin"
PROXY_FREE = build_opener(ProxyHandler({}))


class Stop(Exception):
    pass


def cmd(*argv, timeout=90):
    completed = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if completed.returncode:
        # Never echo rendered Compose, docker inspect or credentials.
        raise Stop("COMMAND_FAILED:" + " ".join(argv[:2]))
    return completed.stdout


def docker_json(*args):
    return json.loads(cmd("docker", *args))


def compose_cmd(project, wd, files):
    result = ["docker", "compose", "-p", project, "--project-directory", str(wd)]
    for f in files:
        result.extend(["-f", str(f)])
    return result


def rendered(project, wd, files):
    return json.loads(cmd(*compose_cmd(project, wd, files), "config", "--format", "json"))


def http_json(path):
    request = Request("http://127.0.0.1:7000/" + path,
                      headers={"Accept": "application/json"})
    with PROXY_FREE.open(request, timeout=5) as r:
        return json.loads(r.read(32768))


def is_ready():
    try:
        health = http_json("health")
        state = http_json("readiness")
        return (health.get("status") == "ok"
                and health.get("version") == "1.4.0-rc.2"
                and state.get("ready") is True
                and state.get("schemaVersion") == 1
                and state.get("connectedProfiles") == 1)
    except Exception:
        return False


def require_regular(path):
    if path.is_symlink() or not path.is_file():
        raise Stop("PROTECTED_FILE_MISSING_OR_SYMLINK")
    return path.stat()


def load_mapping():
    st = require_regular(MAPPING)
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) & 0o077:
        raise Stop("UNSAFE_MAPPING_FILE_PERMISSIONS")
    ids = []
    for line in MAPPING.read_text(encoding="utf8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("JELLYFIN_GATEWAY_USER_ID",
                                    "JELLYFIN_GATEWAY_AIO_USER_ID"):
            ids.append(value.strip().strip("'\""))
    if len(ids) != 1 or not USER_PATTERN.fullmatch(ids[0]):
        raise Stop("MAPPING_ID_NOT_UNIQUE_OR_VALID")
    return ids[0]


def graph_identity(db_path, user):
    if db_path.is_symlink() or not db_path.is_file():
        raise Stop("BRIDGE_DB_NOT_REGULAR")
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        profiles = db.execute(
            "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
        ).fetchall()
        if len(profiles) != 1 or not PROFILE_PATTERN.fullmatch(profiles[0][0]):
            raise Stop("BRIDGE_PROFILE_MAPPING_INVALID")
        profile = profiles[0][0]
        row = db.execute(
            "SELECT payload_json FROM media_cache WHERE cache_key=? "
            "AND expires_at > strftime('%s','now')",
            ("identity-graph:v1:" + profile,),
        ).fetchone()
        if row is None:
            raise Stop("VERIFIED_IDENTITY_GRAPH_MISSING")
        try:
            graph = json.loads(row[0])
            shows = graph["shows"]
            aliases = sum(1 for v in shows.values() if v.get("aliases"))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Stop("VERIFIED_IDENTITY_GRAPH_MALFORMED") from exc
        if graph.get("revision") != 1 or len(shows) != 17 or aliases != 1:
            raise Stop("VERIFIED_IDENTITY_GRAPH_COUNTS_CHANGED")
        scope = r"\x1f".join((profile, "aiostreams", UPSTREAM, user))
        hash_part = hashlib.sha256(scope.encode()).hexdigest()[:24]
        key = "gateway-filter:v1:" + profile + ":" + hash_part
        preference = db.execute(
            "SELECT payload_json FROM media_cache WHERE cache_key=? "
            "AND expires_at > strftime('%s','now')", (key,),
        ).fetchone()
        if preference is not None:
            try:
                if json.loads(preference[0]).get("enabled") is True:
                    raise Stop("FILTER_ALREADY_ON_REQUIRES_SEPARATE_OFF")
            except (ValueError, TypeError, AttributeError) as exc:
                raise Stop("GATEWAY_PREFERENCE_UNREADABLE") from exc
        return profile
    finally:
        db.close()


def override_yaml(user, profile):
    env = {
        "JELLYFIN_GATEWAY_ENABLED": "true",
        "JELLYFIN_GATEWAY_PORT": "7101",
        "JELLYFIN_GATEWAY_SOURCE_KIND": "aiostreams",
        "JELLYFIN_GATEWAY_UPSTREAM_URL": UPSTREAM,
        "JELLYFIN_GATEWAY_USER_ID": user,
        "JELLYFIN_GATEWAY_PROFILE_ID": profile,
        "JELLYFIN_GATEWAY_LEGACY_IMPORT": "off",
        "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH": "false",
        "JELLYFIN_GATEWAY_FIRST_PAGE_MODE": "short_first_page_only",
    }
    body = ("# HomeDocker RC2 internal Gateway only. Explicit -f required; "
            "never publish 7101.\n"
            "services:\n  trakt-bridge:\n"
            "    networks:\n      default: {}\n      media_core: {}\n"
            "    environment:\n")
    body += "".join("      " + k + ": " + json.dumps(v) + "\n"
                    for k, v in env.items())
    body += "networks:\n  media_core:\n    external: true\n    name: media_core_net\n"
    return body, env


def validate_composed(base, candidate, expected):
    old_services = base["services"]
    new_services = candidate["services"]
    if set(old_services) != set(new_services) or APP not in old_services:
        raise Stop("SERVICE_SET_CHANGED")
    for key in old_services:
        if key != APP and old_services[key] != new_services[key]:
            raise Stop("UNRELATED_SERVICE_CHANGED")
    old, new = old_services[APP], new_services[APP]
    for key in set(old) | set(new):
        if key not in ("networks", "environment") and old.get(key) != new.get(key):
            raise Stop("BRIDGE_COMPOSE_FIELD_CHANGED")
    oldenv = old.get("environment") or {}
    newenv = new.get("environment") or {}
    if not isinstance(oldenv, dict) or not isinstance(newenv, dict):
        raise Stop("INVALID_COMPOSE_ENV_FORMAT")
    for key, value in oldenv.items():
        if key not in expected and newenv.get(key) != value:
            raise Stop("EXISTING_BRIDGE_ENV_CHANGED")
        if key in expected and str(value).lower() not in ("", "false", "off", "0"):
            # Never clobber a prior, already-provisioned Gateway.
            raise Stop("GATEWAY_ENV_ALREADY_CONFIGURED")
    if any(str(newenv.get(k)) != v for k, v in expected.items()):
        raise Stop("EXPECTED_GATEWAY_CONFIG_NOT_RENDERED")
    oldnet = old.get("networks") or {}
    newnet = new.get("networks") or {}
    if set(oldnet) != {"default"} or set(newnet) != {"default", "media_core"}:
        raise Stop("UNEXPECTED_BRIDGE_NETWORK_DIFF")
    if newnet["default"] != oldnet["default"]:
        raise Stop("DEFAULT_NETWORK_CHANGED")
    if candidate.get("networks", {}).get("media_core", {}).get("name") != AIO_NETWORK:
        raise Stop("WRONG_EXTERNAL_NETWORK")
    if "7101" in json.dumps(new.get("ports") or []):
        raise Stop("GATEWAY_PUBLIC_PORT_FORBIDDEN")
    if candidate.get("volumes") != base.get("volumes"):
        raise Stop("TOP_LEVEL_VOLUMES_CHANGED")


def inspect():
    if os.geteuid() != 0:
        raise Stop("ROOT_REQUIRED")
    if os.environ.get("TRAKT_BRIDGE_IMAGE"):
        raise Stop("SHELL_IMAGE_OVERRIDE_UNSAFE")
    bridge = docker_json("inspect", APP)[0]
    aio = docker_json("inspect", AIO)[0]
    label = bridge["Config"].get("Labels") or {}
    project = label.get("com.docker.compose.project")
    wd = Path(label.get("com.docker.compose.project.working_dir") or "/not-found")
    config_paths = (label.get("com.docker.compose.project.config_files") or "").split(",")
    if (project != "homedocker-trakt-bridge"
            or len(config_paths) != 1
            or config_paths[0] != str(wd / "compose.yml")
            or label.get("com.docker.compose.service") != APP):
        raise Stop("PRODUCTION_COMPOSE_NOT_EXPECTED_SINGLE_FILE")
    base_file = Path(config_paths[0])
    envfile = wd / ".env"
    require_regular(base_file)
    require_regular(envfile)
    output = wd / OVERRIDE_NAME
    if output.exists() or output.is_symlink():
        raise Stop("GATEWAY_OVERRIDE_ALREADY_EXISTS")
    if not IMAGE_PATTERN.fullmatch(bridge["Config"]["Image"]):
        raise Stop("BRIDGE_NOT_RC2_CI")
    if not bridge["State"]["Running"] or not is_ready():
        raise Stop("BRIDGE_NOT_READY")
    if aio["Config"]["Image"] != "ghcr.io/viren070/aiostreams:latest" or not aio["State"]["Running"]:
        raise Stop("AIO_IMAGE_OR_STATE_CHANGED")
    network = docker_json("network", "inspect", AIO_NETWORK)[0]
    if network.get("Driver") != "bridge" or aio["Id"] not in network.get("Containers", {}):
        raise Stop("AIO_EXTERNAL_NETWORK_NOT_VERIFIED")
    actual_nets = set(bridge["NetworkSettings"]["Networks"])
    if actual_nets != {BASE_NETWORK}:
        raise Stop("BRIDGE_NETWORK_RUNTIME_DRIFT")
    mounts = [m for m in bridge["Mounts"] if m["Destination"] == "/app/data"]
    if len(mounts) != 1:
        raise Stop("BRIDGE_DATA_MOUNT_AMBIGUOUS")
    data_path = mounts[0]["Source"]
    running_ids = cmd("docker", "ps", "-q", "--no-trunc").splitlines()
    running = docker_json("container", "inspect", *running_ids) if running_ids else []
    if any(c["Id"] != bridge["Id"]
           and any(m.get("Source") == data_path for m in c.get("Mounts", []))
           for c in running):
        raise Stop("SECOND_BRIDGE_DATA_WRITER")
    user = load_mapping()
    profile = graph_identity(Path(data_path) / "bridge.db", user)
    base = rendered(project, wd, [base_file])
    if base["services"][APP]["image"] != bridge["Config"]["Image"]:
        raise Stop("COMPOSE_IMAGE_DRIFT")
    body, expected = override_yaml(user, profile)
    with tempfile.TemporaryDirectory(prefix="homedocker-gateway-check-") as folder:
        preview = Path(folder) / "compose.gateway.yml"
        preview.write_text(body, encoding="utf8")
        preview.chmod(0o600)
        candidate = rendered(project, wd, [base_file, preview])
    validate_composed(base, candidate, expected)
    print("RC2_BRIDGE=READY")
    print("AIO_NETWORK=VERIFIED")
    print("JELLYFIN_USER_MAPPING=VALID_FORMAT")
    print("VERIFIED_GRAPH=17_SHOWS_1_ALIAS_GROUP")
    print("COMPOSE_OVERRIDE_DIFF=NETWORK_AND_GATEWAY_ENV_ONLY")
    print("LISTENER_EXPOSURE=DOCKER_INTERNAL_ONLY")
    print("IDENTITY_FILTER=OFF")
    return dict(project=project, wd=wd, base_file=base_file, envfile=envfile,
                output=output, body=body, current=bridge, aio_id=aio["Image"],
                data_path=data_path)


def container_gateway_health():
    js = (
        "const checks=[['gateway','http://127.0.0.1:7101/health'],"
        "['aio','http://aiostreams:3000/jellyfin/System/Info/Public']];"
        "let good=true; for(const [name,url] of checks){try{"
        "const r=await fetch(url,{signal:AbortSignal.timeout(5000)});"
        "if(r.status!==200)good=false;"
        "console.log(name.toUpperCase()+'_HTTP='+r.status);}"
        "catch(e){good=false;console.log(name.toUpperCase()+'_ERROR='+"
        "(e.cause?.code||e.name));}}if(!good)process.exitCode=1;"
    )
    return cmd("docker", "exec", APP, "node", "--input-type=module",
               "-e", js, timeout=20)


def wait_ready(before):
    for _ in range(30):
        try:
            bridge = docker_json("inspect", APP)[0]
            if (bridge["State"]["Running"] and bridge["Image"] == before["Image"]
                    and bridge["Config"]["Image"] == before["Config"]["Image"]
                    and is_ready()):
                return bridge
        except Exception:
            pass
        time.sleep(2)
    raise Stop("BRIDGE_POST_RECREATE_NOT_READY")


def backup_files(state):
    backup_dir = Path("/var/backups/homedocker-trakt-bridge/gateway-provision")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    folder = backup_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(mode=0o700)
    for source, name in ((state["base_file"], "compose.yml.before"),
                         (state["envfile"], "compose.env.before")):
        shutil.copy2(source, folder / name)
        (folder / name).chmod(0o600)
    (folder / "rollback-note.txt").write_text(
        "Rollback: docker compose -p homedocker-trakt-bridge "
        "--project-directory " + str(state["wd"]) + " -f "
        + str(state["base_file"])
        + " up -d --no-deps --no-build --pull never --force-recreate trakt-bridge\n",
        encoding="utf8",
    )
    return folder


def apply(state):
    folder = backup_files(state)
    # Recheck baseline after backups, immediately before any Compose change.
    if not is_ready():
        raise Stop("BRIDGE_READINESS_CHANGED_BEFORE_APPLY")
    override = state["output"]
    fd = os.open(override, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as out:
        out.write(state["body"])
    original = compose_cmd(state["project"], state["wd"], [state["base_file"]])
    extended = compose_cmd(state["project"], state["wd"], [state["base_file"], override])
    try:
        cmd(*extended, "up", "-d", "--no-deps", "--no-build", "--pull",
            "never", "--force-recreate", APP, timeout=150)
        now = wait_ready(state["current"])
        if set(now["NetworkSettings"]["Networks"]) != {BASE_NETWORK, AIO_NETWORK}:
            raise Stop("PROVISIONED_BRIDGE_NETWORKS_MISMATCH")
        if ([m["Source"] for m in now["Mounts"] if m["Destination"] == "/app/data"]
                != [state["data_path"]]):
            raise Stop("PROVISIONED_BRIDGE_DB_MOUNT_CHANGED")
        if docker_json("inspect", AIO)[0]["Image"] != state["aio_id"]:
            raise Stop("AIO_IMAGE_CHANGED")
        if any("7101" in k for k in now["NetworkSettings"].get("Ports", {})
               if now["NetworkSettings"]["Ports"][k]):
            raise Stop("GATEWAY_PORT_PUBLISHED")
        print(container_gateway_health().strip())
    except Exception as exc:
        print("GATEWAY_PROVISION_FAILED=ATTEMPTING_BASE_COMPOSE_ROLLBACK")
        try:
            cmd(*original, "up", "-d", "--no-deps", "--no-build", "--pull",
                "never", "--force-recreate", APP, timeout=150)
            restored = wait_ready(state["current"])
            if set(restored["NetworkSettings"]["Networks"]) != {BASE_NETWORK}:
                raise Stop("ROLLBACK_NETWORKS_MISMATCH")
            override.unlink()
            print("BASE_COMPOSE_ROLLBACK=HEALTHY")
        except Exception as rollback_error:
            print("BASE_COMPOSE_ROLLBACK=MANUAL_RECOVERY_REQUIRED", file=sys.stderr)
            raise Stop("GATEWAY_PROVISION_AND_ROLLBACK_FAILED") from rollback_error
        raise Stop("GATEWAY_PROVISION_FAILED_ROLLED_BACK") from exc
    print("GATEWAY_PROVISION=HEALTHY")
    print("GATEWAY_LISTENER=INTERNAL_7101")
    print("IDENTITY_FILTER=OFF")
    print("CLIENT_ROUTING_CHANGED=NO")
    print("AIO_CHANGED=NO")
    print("BASE_COMPOSE_AND_ENV=UNCHANGED")
    print("ROLLBACK_SNAPSHOT=PRIVATE")
    print("STABLE_RELEASE_HOLD=ACTIVE")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if options.apply and options.confirm != CONFIRM:
        raise Stop("APPLY_REQUIRES_EXPLICIT_CONFIRMATION")
    if not options.apply and options.confirm:
        raise Stop("CONFIRM_REQUIRES_APPLY")
    state = inspect()
    if not options.apply:
        print("MODE=READ_ONLY_PREFLIGHT")
        print("PRODUCTION_CHANGES=0")
        return
    apply(state)


if __name__ == "__main__":
    try:
        main()
    except (Stop, OSError, KeyError, ValueError, TypeError,
            sqlite3.DatabaseError, subprocess.TimeoutExpired) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        sys.exit(1)
