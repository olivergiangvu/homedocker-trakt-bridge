#!/usr/bin/env python3
"""G1B: capture real authenticated AIO snapshot, replay in offline JS Gateway.

Host: only mode=ro/query_only production Bridge SQLite and bounded AIO GETs
(or ONE explicit opt-in authentication POST). No credentials printed or saved.
In mode replay ONLY: start one ephemeral, unprivileged --network none Docker
container from locally running pinned RC6 image, read-only source checkout,
pass verified legacy cache + real AIO NextUp + metadata + AIO user ID via
STDIN RAM. Never pass AIO token, password, Trakt OAuth or Bridge encryption key
to Docker. Node runs real JS importer and HTTP Gateway against injected memory
fetch, tests zero live change, synthetic positive 2=>1, dual resume fail-open
and cross-user fail-open. This is NOT real AIO<->Gateway network cutover.
"""
import argparse
from contextlib import closing
import getpass
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
LOCAL_AUDIT = ROOT / "audit-v140-g1-live-inputs.py"
RUNNER = ROOT / "replay-v140-g1b-candidate.mjs"
IMAGE_PIN = (
    "ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
    "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2"
)
TEMP = "homedocker-v140-g1b-offline-replay"
PREP_CONFIRM = "V140_G1B_SNAPSHOT_EXISTING_TOKEN_MEMORY_ONLY"
LOGIN_CONFIRM = "V140_G1B_ONE_AIO_LOGIN_MEMORY_ONLY"
MAX_INPUT = 8 * 1024 * 1024


class Stop(Exception):
    pass


def stop(reason):
    raise Stop(reason)


def import_audit():
    spec = importlib.util.spec_from_file_location("g1a_audit", LOCAL_AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inspect_production():
    p = subprocess.run(["docker", "container", "inspect", "trakt-bridge"],
                       text=True, capture_output=True, timeout=15)
    if p.returncode:
        stop("PRODUCTION_CONTAINER_INSPECTION_FAILED")
    containers = json.loads(p.stdout)
    if len(containers) != 1:
        stop("AMBIGUOUS_BRIDGE")
    c = containers[0]
    if c.get("State", {}).get("Running") is not True:
        stop("BRIDGE_NOT_RUNNING")
    if c.get("Config", {}).get("Image") != IMAGE_PIN:
        stop("RC6_IMAGE_REFERENCE_CHANGED")
    image = c.get("Image")
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        stop("INVALID_RUNNING_RC6_IMAGE_ID")
    mounts = [m for m in c.get("Mounts", [])
              if m.get("Destination") == "/app/data"
              and m.get("Type") in ("bind", "volume")]
    if len(mounts) != 1:
        stop("AMBIGUOUS_BRIDGE_DATA_MOUNT")
    db = Path(mounts[0]["Source"]) / "bridge.db"
    if db.is_symlink() or not db.is_file():
        stop("BRIDGE_DB_UNAVAILABLE")
    return image, db


def snapshot(db, g1a):
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro",
                                uri=True, timeout=5)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        rows = con.execute(
            "SELECT cache_key,payload_json,expires_at FROM media_cache "
            "WHERE cache_key LIKE 'identity-alias:v1:%' "
            "OR cache_key LIKE 'identity-graph:v1:%' LIMIT 1001"
        ).fetchall()
        if len(rows) > 1000:
            stop("IDENTITY_SCOPE_EXCEEDED")
        legacy = [r for r in rows if r[0].startswith("identity-alias:v1:")]
        graphs = [r for r in rows if r[0].startswith("identity-graph:v1:")]
        if len(legacy) != 1 or graphs:
            stop("IDENTITY_GRAPH_SCOPE_CHANGED")
        key, raw, expiry = legacy[0]
        profile = key.removeprefix("identity-alias:v1:")
        if not g1a.PROFILE.fullmatch(profile):
            stop("PROFILE_FORMAT_INVALID")
        connected = con.execute(
            "SELECT 1 FROM profiles WHERE id=? AND "
            "access_token_enc IS NOT NULL LIMIT 1", (profile,)
        ).fetchone()
        if connected is None:
            stop("PROFILE_NO_LONGER_CONNECTED")
        if expiry is None or int(expiry) < int(time.time()):
            stop("LEGACY_IDENTITY_EXPIRED")
        original = json.loads(raw)
        shows, pairs, _ = g1a.load_legacy(original)
        # G1A returns a private {Trakt ID: frozenset(Imdb aliases)} mapping.
        # Never print or JSON-serialize that identity mapping in preflight.
        # The operator output needs only the numeric verified-group count.
        return {
            "profile": profile,
            "legacy": original,
            "shows": shows,
            "groups": len(pairs),
        }


def replay_snapshot(g1a, source, mode):
    if not sys.stdin.isatty():
        stop("INTERACTIVE_SSH_REQUIRED")
    if mode == "existing-token":
        token = getpass.getpass("Existing AIO Jellyfin token (hidden): ").strip()
        if not g1a.TOKEN.fullmatch(token):
            stop("INVALID_EXISTING_AIO_TOKEN")
        expected = None
        auth_posts = 0
    else:
        token, expected = g1a.login_session()
        auth_posts = 1

    try:
        me = g1a.aio_request("/Users/Me", token)
        user = me.get("Id")
        if not isinstance(user, str) or not g1a.USER.fullmatch(user):
            stop("AUTHENTICATED_AIO_USER_UNVERIFIED")
        if expected is not None and user != expected:
            stop("AIO_LOGIN_IDENTITY_MISMATCH")

        before = g1a.aio_request("/Shows/NextUp?Limit=20", token)
        items = before.get("Items")
        if (not isinstance(items, list) or len(items) > 20
            or any(not isinstance(x, dict)
                   or not g1a.SERIES.fullmatch(str(x.get("SeriesId") or ""))
                   for x in items)):
            stop("AIO_NEXTUP_ITEMS_OUT_OF_BOUNDS")
        details = {}
        for sid in sorted({item["SeriesId"] for item in items}):
            d = g1a.aio_request("/Items/" + sid, token)
            if d.get("Type") != "Series" or d.get("Id") != sid:
                stop("AIO_SERIES_ID_OR_AUTH_UNVERIFIED")
            # Only required identity facts; no metadata art/titles are copied.
            provider = d.get("ProviderIds") or {}
            if not isinstance(provider, dict):
                stop("AIO_PROVIDER_IDS_INVALID")
            imdb = provider.get("Imdb")
            details[sid] = {
                "Id": sid,
                "Type": "Series",
                "ProviderIds": {"Imdb": imdb if isinstance(imdb, str) else None},
            }
        after = g1a.aio_request("/Shows/NextUp?Limit=20", token)
        if before != after:
            stop("AIO_SOURCE_CHANGED_DURING_CAPTURE")
        return {"user": user, "nextup": before, "details": details,
                "auth_posts": auth_posts}
    finally:
        del token


def run_offline(image, payload):
    if not RUNNER.is_file() or RUNNER.is_symlink():
        stop("OFFLINE_RUNNER_UNAVAILABLE")
    name = subprocess.run(["docker", "container", "inspect", TEMP],
                          capture_output=True, timeout=15)
    if name.returncode == 0:
        stop("G1B_CONTAINER_NAME_ALREADY_OCCUPIED")
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", TEMP,
        "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "1000:1000", "--pids-limit", "32",
        "--memory", "128m", "--cpus", "1",
        "--mount", "type=bind,source=" + str(ROOT.parent)
          + ",target=/audit,readonly",
        "--mount", "type=tmpfs,destination=/app/data,tmpfs-size=1048576",
        "--entrypoint", "/usr/local/bin/node",
        image, "/audit/scripts/replay-v140-g1b-candidate.mjs",
    ]
    serialized = json.dumps(payload, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_INPUT:
        stop("G1B_MEMORY_INPUT_TOO_LARGE")
    try:
        done = subprocess.run(cmd, input=serialized, text=True,
                              capture_output=True, timeout=65)
    except subprocess.TimeoutExpired:
        stop("G1B_CONTAINER_TIMEOUT_CHECK_EXACT_NAME")
    if done.returncode:
        stop("G1B_OFFLINE_NODE_PROOF_FAILED")
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        stop("G1B_NODE_OUTPUT_INVALID")
    if not (
        result.get("status") == "OFFLINE_SNAPSHOT_GATEWAY_REPLAY_PASS"
        and result.get("actual_v140_js_importer_and_http_gateway_executed") is True
        and result.get("synthetic_positive_filter_pass") is True
        and result.get("synthetic_alias_before") == 2
        and result.get("synthetic_alias_after") == 1
        and result.get("live_snapshot_unchanged") is True
        and result.get("two_active_resumes_passthrough") is True
        and result.get("mismatched_user_passthrough") is True
        and result.get("production_db_writes") == 0
        and result.get("real_aio_or_trakt_calls_from_container") == 0
        and result.get("g1_complete") is False
    ):
        stop("G1B_NODE_PROOF_INTEGRITY_FAILED")
    check = subprocess.run(["docker", "container", "inspect", TEMP],
                           capture_output=True, timeout=15)
    if check.returncode == 0:
        stop("G1B_TEMP_CONTAINER_REMAINS")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", required=True,
                   choices=("preflight", "existing-token", "login"))
    p.add_argument("--confirm", default="")
    a = p.parse_args()
    if a.mode == "existing-token" and a.confirm != PREP_CONFIRM:
        stop("EXPLICIT_EXISTING_TOKEN_CONSENT_REQUIRED")
    if a.mode == "login" and a.confirm != LOGIN_CONFIRM:
        stop("EXPLICIT_AIO_LOGIN_CONSENT_REQUIRED")
    if a.mode == "preflight" and a.confirm:
        stop("PREFLIGHT_CANNOT_ENABLE_AUTH")
    g1a = import_audit()
    image, db = inspect_production()
    legacy = snapshot(db, g1a)
    g1a.aio_request("/System/Info/Public")
    if a.mode == "preflight":
        return {
            "audit": "G1B_ISOLATED_GATEWAY_REPLAY",
            "status": "PREFLIGHT_READY_NO_AUTH_OR_DOCKER_RUN",
            "legacy_shows": legacy["shows"],
            "legacy_alias_groups": legacy["groups"],
            "rc6_image_verified": True,
            "docker_containers_started": 0,
            "aio_login_or_nextup_requested": False,
            "production_sqlite_writes": 0,
            "g1_complete": False,
        }
    captured = replay_snapshot(g1a, legacy, a.mode)
    record = {
        "profile": legacy["profile"],
        "legacy": legacy["legacy"],
        "user": captured["user"],
        "nextup": captured["nextup"],
        "details": captured["details"],
    }
    result = run_offline(image, record)
    # Only sanitized, whitelisted fields returned by reviewed Node runner.
    result["aio_authentication_post_requests"] = captured["auth_posts"]
    result["host_source_nextup_verified"] = True
    result["user_and_media_identifiers_printed"] = False
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, sort_keys=True))
    except (Stop, OSError, ValueError, TypeError, KeyError,
            sqlite3.Error, subprocess.TimeoutExpired,
            json.JSONDecodeError, KeyboardInterrupt, EOFError) as exc:
        value = str(exc) if isinstance(exc, Stop) else ""
        safe = value if re.fullmatch(r"[A-Z0-9_]{3,100}", value) else "AUDIT_UNAVAILABLE"
        print("G1B_STOP_" + safe, file=sys.stderr)
        raise SystemExit(5) from None
