#!/usr/bin/env python3
"""G1C: operator-authenticated GET-only direct Gateway -> AIO acceptance.

--mode preflight ONLY: inspect pinned RC6 + AIO Docker topology, read Bridge
identity cache SQLite mode=ro/query_only, AIO unauth public health GET.
--mode existing-token: interactive hidden AIO token (GET-only AIO calls).
--mode login: ONE interactive AIO login POST, then GET-only.

Executes actual v1.4 Gateway in ONE ephemeral Docker container sharing only
AIO's existing network namespace (no port publishing, no nginx/routing edits).
The token *must* enter the ephemeral Node process via bounded STDIN RAM; never
logged, saved, included in argv/env. No database mounts into the container.
Node probes its own loopback Gateway, which GETs real AIO localhost:3000.
No playback/history/Trakt writes or production graph import. This does not
prove real-world account↔profile ownership or real-client path qualification.
"""
import argparse
import getpass
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "probe-v140-g1c-direct-aio.mjs"
AIO_NAME = "aiostreams"
AIO_REF = "ghcr.io/viren070/aiostreams:latest"
TEMP = "homedocker-v140-g1c-direct-probe"
CONFIRM_TOKEN = "V140_G1C_DIRECT_AIO_TOKEN_GET_ONLY"
CONFIRM_LOGIN = "V140_G1C_DIRECT_AIO_ONE_LOGIN_POST_THEN_GET"
MAX_INPUT = 5 * 1024 * 1024


class Stop(Exception):
    pass


def reject(reason):
    raise Stop(reason)


def include(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def docker_inspect(container_name):
    r = subprocess.run(
        ["docker", "container", "inspect", container_name],
        capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        reject("REQUIRED_DOCKER_CONTAINER_NOT_FOUND")
    items = json.loads(r.stdout)
    if len(items) != 1:
        reject("DOCKER_INSPECTION_SCOPE_CHANGED")
    return items[0]


def aio_topology():
    aio = docker_inspect(AIO_NAME)
    if not aio.get("State", {}).get("Running"):
        reject("AIO_NOT_RUNNING")
    if aio.get("Config", {}).get("Image") != AIO_REF:
        reject("AIO_IMAGE_CHANGED_REVIEW_REQUIRED")
    network = aio.get("HostConfig", {}).get("NetworkMode", "")
    if network in ("host", "none") or str(network).startswith("container:"):
        reject("AIO_NETWORK_NAMESPACE_INCOMPATIBLE")
    bindings = aio.get("HostConfig", {}).get("PortBindings", {})
    matches = bindings.get("3000/tcp") or []
    if not any(x.get("HostIp") == "127.0.0.1"
               and str(x.get("HostPort")) == "3240" for x in matches):
        reject("AIO_LOOPBACK_HOST_MAPPING_CHANGED")
    return True


def temp_absent():
    r = subprocess.run(
        ["docker", "container", "inspect", TEMP],
        capture_output=True, timeout=15)
    if r.returncode == 0:
        reject("G1C_TEMP_CONTAINER_NAME_IN_USE")
    # Docker returns nonzero for absence and daemon errors alike. The
    # independent required-container inspect above proves daemon access.


def authenticate(audit, mode):
    if not sys.stdin.isatty():
        reject("INTERACTIVE_SSH_TTY_REQUIRED")
    if mode == "existing-token":
        token = getpass.getpass("Existing AIO Jellyfin token (hidden): ").strip()
        if not audit.TOKEN.fullmatch(token):
            reject("INVALID_SESSION_TOKEN")
        expected = None
        auth_posts = 0
    else:
        token, expected = audit.login_session()
        auth_posts = 1
    user = audit.aio_request("/Users/Me", token).get("Id")
    if not isinstance(user, str) or not audit.USER.fullmatch(user):
        reject("AIO_USER_ID_UNVERIFIED")
    if expected is not None and expected != user:
        reject("AIO_LOGIN_USER_MISMATCH")
    return token, user, auth_posts


def run_probe(image, record):
    if not SCRIPT.is_file() or SCRIPT.is_symlink():
        reject("G1C_NODE_RUNNER_UNAVAILABLE")
    temp_absent()
    raw = json.dumps(record, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_INPUT:
        reject("G1C_INPUT_TOO_LARGE")
    # Sharing AIO's network namespace is a bounded practical experiment,
    # NOT a claim of kernel-level egress isolation. It touches neither AIO
    # filesystem nor its Docker object and publishes no listener on the host.
    command = [
        "docker", "run", "--rm", "-i", "--name", TEMP,
        "--network", "container:" + AIO_NAME, "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "1000:1000", "--pids-limit", "32",
        "--memory", "128m", "--cpus", "1",
        "--mount", "type=bind,source=" + str(ROOT.parent)
          + ",target=/audit,readonly",
        "--entrypoint", "/usr/local/bin/node",
        image, "/audit/scripts/probe-v140-g1c-direct-aio.mjs",
    ]
    try:
        result = subprocess.run(
            command, input=raw, text=True,
            capture_output=True, timeout=90, check=False)
    except subprocess.TimeoutExpired:
        reject("G1C_TIMEOUT_VERIFY_TEMP_CONTAINER_MANUALLY")
    if result.returncode != 0:
        # Never echo Node stderr, which might someday contain secrets.
        reject("DIRECT_GATEWAY_PROBE_FAILED")
    try:
        status = json.loads(result.stdout)
    except (ValueError, TypeError):
        reject("G1C_SANITIZED_REPORT_INVALID")
    must = {
        "status": "DIRECT_AIO_GATEWAY_GET_PASS",
        "actual_v140_gateway_authenticated_aio_get": True,
        "exact_configured_user_matched_users_me": True,
        "gateway_preserved_real_nextup": True,
        "source_stable_during_gateway_probe": True,
        "independent_user_to_profile_ownership_proven": False,
        "real_client_qualified": False,
        "production_sqlite_writes": 0,
        "watch_history_or_playback_writes_requested": 0,
        "trakt_http_calls": 0,
        "no_public_port_or_nginx_route": True,
        "user_id_profile_id_imdb_ids_or_token_printed": False,
        "g1_complete": False,
    }
    if any(status.get(k) != v for k, v in must.items()):
        reject("G1C_PROOF_REPORT_INTEGRITY_FAILED")
    if status.get("aio_http_methods") != ["GET"]:
        reject("G1C_AIO_METHOD_BOUNDARY_FAILED")
    if status.get("verified_alias_groups_in_ram") != 1:
        reject("G1C_ALIAS_SCOPE_CHANGED")
    temp_absent()
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=("preflight", "existing-token", "login"))
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.mode == "existing-token" and args.confirm != CONFIRM_TOKEN:
        reject("EXPLICIT_TOKEN_SESSION_APPROVAL_REQUIRED")
    if args.mode == "login" and args.confirm != CONFIRM_LOGIN:
        reject("EXPLICIT_NEW_AIO_LOGIN_APPROVAL_REQUIRED")
    if args.mode == "preflight" and args.confirm:
        reject("PREFLIGHT_REJECTS_AUTH_CONFIRM")
    aio_topology()
    g1a = include("g1a", "audit-v140-g1-live-inputs.py")
    g1b = include("g1b", "audit-v140-g1b-isolated-replay.py")
    image, db = g1b.inspect_production()
    legacy = g1b.snapshot(db, g1a)
    g1a.aio_request("/System/Info/Public")
    temp_absent()
    if args.mode == "preflight":
        return {
            "audit": "G1C_DIRECT_AIO_GATEWAY_PREFLIGHT",
            "status": "PREFLIGHT_READY_NO_AUTH_NO_CONTAINER",
            "bridge_rc6_image_verified": True,
            "aio_loopback_and_netns_verified": True,
            "connected_legacy_shows": legacy["shows"],
            "verified_alias_groups": legacy["groups"],
            "new_login_sessions": 0,
            "authenticated_aio_nextup_gets": 0,
            "new_containers": 0,
            "production_writes": 0,
            "g1_complete": False,
        }
    token, user, posts = authenticate(g1a, args.mode)
    try:
        data = {
            "profile": legacy["profile"],
            "legacy": legacy["legacy"],
            "user": user,
            "token": token,
        }
        proof = run_probe(image, data)
        proof["operator_aio_login_post_count"] = posts
        proof["temporary_container_exited_and_removed"] = True
        return proof
    finally:
        # No disk file/env/argv ever holds the session credential.
        del token


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, sort_keys=True))
    except (Stop, OSError, TypeError, ValueError, KeyError,
            subprocess.TimeoutExpired, KeyboardInterrupt, EOFError) as e:
        enum = str(e) if isinstance(e, Stop) else ""
        safe = enum if re.fullmatch(r"[A-Z0-9_]{3,100}", enum) else "AUDIT_UNAVAILABLE"
        print("G1C_STOP_" + safe, file=sys.stderr)
        raise SystemExit(5) from None
