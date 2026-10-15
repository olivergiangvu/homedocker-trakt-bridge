#!/usr/bin/env python3
"""Read-only live Bridge legacy snapshot → actual v1.4 JS importer IN MEMORY.

Uses host Node.js >= 20 by default; explicit --runtime isolated-docker\nuses the already pinned, locally running RC6 image in an ephemeral offline container (no image pulls).
Reads only ONE connected profile's legacy identity mapping through SQLite
mode=ro+query_only, verifies expiry and absence of an existing graph. Pipes
legacy mappings directly to the JS importer via stdin (never command-line
arguments, tempfile or stdout). JS fake cache uses only RAM. Does not expose
OAuth token values, names, credentials, IMDb IDs or raw identity rows.

No database/other file writes or Trakt/AIO network. The optional Docker\nrunner transiently creates a tightly isolated --rm container, then removes it.
Success does NOT authorize future production migration, Gateway activation
or media history changes.
"""
import argparse
from contextlib import closing
import importlib.util
import json
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PREVIEW = ROOT / "preview-v140-legacy-import.py"
RUNNER = ROOT / "verify-v140-legacy-import-memory.mjs"
MAX_PAYLOAD = 1024 * 1024

def load_readonly_preview():
    spec = importlib.util.spec_from_file_location("v140_legacy_preview", PREVIEW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def load_snapshot(db_path):
    mod = load_readonly_preview()
    target = Path(db_path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("bridge_database_unavailable")
    with closing(sqlite3.connect(
        target.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
    )) as con:
        con.execute("PRAGMA query_only=ON")
        # BEGIN creates a consistent snapshot without modifying source SQLite.
        con.execute("BEGIN")
        rows = con.execute(
            "SELECT cache_key,payload_json,expires_at FROM media_cache "
            "WHERE cache_key LIKE 'identity-alias:v1:%' "
            "OR cache_key LIKE 'identity-graph:v1:%' LIMIT 1001"
        ).fetchall()
        if len(rows) > 1000:
            raise ValueError("identity_cache_scope_exceeded")
        legacy = [r for r in rows if r[0].startswith("identity-alias:v1:")]
        graphs = [r for r in rows if r[0].startswith("identity-graph:v1:")]
        if len(legacy) != 1 or graphs:
            raise ValueError("scope_or_graph_precondition_failed")
        key, payload, expires_at = legacy[0]
        profile = key.removeprefix("identity-alias:v1:")
        if not mod.PROFILE.fullmatch(profile):
            raise ValueError("invalid_profile_mapping")
        connected = con.execute(
            "SELECT 1 FROM profiles WHERE id=? "
            "AND access_token_enc IS NOT NULL LIMIT 1", (profile,)
        ).fetchone()
        if not connected:
            raise ValueError("missing_connected_profile")
        if expires_at is None or int(expires_at) < int(time.time()):
            raise ValueError("legacy_cache_expired")
        raw = json.loads(payload)
        state, shows, aliases = mod.proposed_graph(raw)
        if state != "DRY_RUN_READY":
            raise ValueError("legacy_preflight_not_ready")
        result = {
            "profileId": profile,
            "legacy": raw,
            "expected": {"shows": shows, "aliases": aliases},
        }
        if len(json.dumps(result).encode("utf-8")) > MAX_PAYLOAD:
            raise ValueError("legacy_evidence_exceeds_limit")
        return result

def docker_runner():
    # Explicit opt-in, ephemeral offline container, using the image ID of
    # the ALREADY running RC6 container. No pulls, production bind mounts,
    # secrets, ports, network or persistent storage.
    inspected = subprocess.run(
        ["docker", "inspect", "--format",
         "{{.Image}}|{{.State.Running}}", "trakt-bridge"],
        text=True, capture_output=True, timeout=15, check=False,
    )
    if inspected.returncode != 0:
        raise RuntimeError("production_image_inspection_unavailable")
    line = inspected.stdout.strip().split("|")
    if len(line) != 2 or not re.fullmatch(r"sha256:[0-9a-f]{64}", line[0]) \
            or line[1] != "true":
        raise RuntimeError("production_runtime_not_qualified")
    image_id = line[0]
    return [
        "docker", "run", "--rm", "-i",
        "--name", "trakt-bridge-r20-offline-check",
        "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "1000:1000", "--pids-limit", "32", "--memory", "128m",
        "--cpus", "1",
        "--mount", "type=bind,source=" + str(ROOT.parent)
                   + ",target=/audit,readonly",
        "--mount", "type=tmpfs,destination=/app/data,tmpfs-size=1048576",
        "--entrypoint", "/usr/local/bin/node",
        image_id, "/audit/scripts/verify-v140-legacy-import-memory.mjs",
    ]

def audit(db_path, node=None, runtime="host-node"):
    if runtime not in ("host-node", "isolated-docker"):
        raise ValueError("unsupported_offline_runtime")
    if not RUNNER.is_file() or RUNNER.is_symlink():
        raise RuntimeError("missing_offline_js_runner")
    if runtime == "host-node":
        node = node or shutil.which("node")
        if not node:
            raise RuntimeError("node_runtime_missing_use_explicit_docker_runtime")
        cmd = [node, str(RUNNER)]
    else:
        cmd = docker_runner()
    evidence = load_snapshot(db_path)
    process = subprocess.run(
        cmd, input=json.dumps(evidence),
        text=True, capture_output=True, timeout=45, check=False
    )
    if process.returncode != 0:
        # Do not echo stderr because any third-party diagnostic may carry IDs.
        raise RuntimeError("actual_js_memory_import_verification_failed")
    result = json.loads(process.stdout)
    expected = evidence["expected"]
    if (result.get("status") != "OFFLINE_MEMORY_IMPORT_PASS"
        or result.get("imported_show_mappings") != expected["shows"]
        or result.get("alias_groups") != expected["aliases"]
        or result.get("verified_alias_count") != expected["aliases"]
        or result.get("migration_persisted_to_production") is not False
        or result.get("sqlite_writes") != 0
        or result.get("http_or_trakt_calls") != 0
        or result.get("repeat_import_idempotent") is not True):
        raise RuntimeError("offline_import_output_integrity_failed")
    # Exact safe fields, no Node stdout pass-through.
    keys = (
        "audit", "status", "actual_v140_javascript_importer_executed",
        "connected_profile_count", "imported_show_mappings",
        "alias_groups", "verified_alias_count", "conflict_count",
        "identity_graph_revision", "repeat_import_idempotent",
        "legacy_source_unchanged_in_memory",
        "migration_persisted_to_production", "sqlite_writes",
        "aio_watch_state_writes", "http_or_trakt_calls",
        "tokens_ids_or_history_printed",
        "gateway_activated_or_client_routed",
    )
    return {key: result[key] for key in keys}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-db", default=(
        "/var/lib/docker/volumes/"
        "homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db"
    ))
    parser.add_argument("--runtime", choices=("host-node", "isolated-docker"),
                        default="host-node",
                        help="isolated-docker starts a temporary --rm --network none "
                             "container from the already running RC6 image; "
                             "no other Docker changes")
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.bridge_db, runtime=args.runtime), indent=2))
    except (sqlite3.Error, OSError, ValueError, RuntimeError,
            subprocess.TimeoutExpired, json.JSONDecodeError):
        print("STOP: R20 offline memory audit unavailable; no production changes",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
