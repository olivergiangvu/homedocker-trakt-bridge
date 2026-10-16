#!/usr/bin/env python3
"""READ-ONLY HomeDocker gateway readiness: network + legacy alias provenance.

Inspects Docker metadata and reads Bridge SQLite in mode=ro. No secrets,
profile IDs, tokens, IP addresses, SQLite row dumps or mutations are printed.

No AIO/Bridge container, nginx, network, Compose, env or image is changed.
"""
import json
import sqlite3
import subprocess
import time
from pathlib import Path


class Stop(Exception):
    pass


def docker_json(*args):
    p = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False,
    )
    if p.returncode:
        raise Stop("Docker inspect failed: " + " ".join(args[:2]))
    try:
        return json.loads(p.stdout)
    except ValueError as ex:
        raise Stop("Docker response was not JSON") from ex


def get_container(name):
    result = docker_json("container", "inspect", name)
    if len(result) != 1 or result[0].get("Name") != "/" + name:
        raise Stop("Expected one exact container: " + name)
    return result[0]


def valid_networks(bridge, aio):
    bn = bridge.get("NetworkSettings", {}).get("Networks") or {}
    an = aio.get("NetworkSettings", {}).get("Networks") or {}
    networks = docker_json("network", "inspect", "media_core_net")
    if len(networks) != 1:
        raise Stop("media_core_net network missing")
    network = networks[0]
    memberships = network.get("Containers") or {}
    aio_member = any(v.get("Name") == "aiostreams"
                     for v in memberships.values())
    aionet = an.get("media_core_net") or {}
    aliases = aionet.get("Aliases") or []
    image_ref = aio.get("Config", {}).get("Image")
    if image_ref != "ghcr.io/viren070/aiostreams:latest":
        raise Stop("AIO image is not configured as rolling upstream latest")
    bridge_status = bridge.get("State", {}).get("Running", False)
    aio_status = aio.get("State", {}).get("Running", False)
    if not (bridge_status and aio_status):
        raise Stop("Bridge or AIO container not running")
    ports = aio.get("NetworkSettings", {}).get("Ports") or {}
    host_bindings = ports.get("3000/tcp") or []
    aio_loopback = any(
        x.get("HostIp") == "127.0.0.1" and x.get("HostPort") == "3240"
        for x in host_bindings
    )
    return {
        "bridge_on_current_default_network": "homedocker-trakt-bridge_default" in bn,
        "bridge_already_on_media_core_net": "media_core_net" in bn,
        "aio_on_media_core_net": aio_member and "media_core_net" in an,
        "aio_has_network_dns_alias": "aiostreams" in aliases,
        "aio_internal_port": 3000,
        "aio_loopback_host_3240": aio_loopback,
        "media_network_driver": network.get("Driver"),
        "media_network_internal": network.get("Internal"),
        "canary_network_action": "ADD_BRIDGE_TO_EXISTING_MEDIA_NETWORK_ONLY_AFTER_CI_AND_BACKUP",
    }


def sqlite_status(bridge):
    mounts = [
        m for m in bridge.get("Mounts") or []
        if m.get("Destination") == "/app/data"
    ]
    if len(mounts) != 1:
        raise Stop("Bridge persistent data mount is not uniquely known")
    mount = mounts[0]
    path = Path(mount.get("Source", "")) / "bridge.db"
    if not path.is_file() or path.is_symlink():
        raise Stop("Bridge SQLite bridge.db missing/unexpected")
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as ex:
        raise Stop("Could not open Bridge SQLite read-only (try sudo)") from ex

    try:
        db.execute("PRAGMA query_only=ON")
        result = {
            "bridge_sqlite_mode": "READ_ONLY",
            "legacy_alias_known_show": False,
            "legacy_canonical_matches": False,
            "legacy_alias_matches": False,
            "legacy_alias_timestamp_valid": False,
            "legacy_cache_unexpired": False,
            "legacy_owner_connected": False,
            "verified_graph_present": False,
            "verified_graph_known_show": False,
            "verified_graph_alias_matches": False,
        }
        rows = db.execute(
            "SELECT cache_key,payload_json,expires_at FROM media_cache "
            "WHERE cache_key LIKE 'identity-alias:v1:%' OR "
            "cache_key LIKE 'identity-graph:v1:%' LIMIT 1000"
        ).fetchall()
        for key, payload_json, expires_at in rows:
            try:
                state = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            value = (state.get("shows") or {}).get("263102") or {}
            if key.startswith("identity-alias:v1:"):
                if value:
                    result["legacy_alias_known_show"] = True
                    if value.get("traktImdb") == "tt44051354":
                        result["legacy_canonical_matches"] = True
                    if value.get("preferredMetaId") == "tt44094505":
                        result["legacy_alias_matches"] = True
                    try:
                        result["legacy_alias_timestamp_valid"] = (
                            isinstance(value.get("updatedAt"), int)
                            and value.get("updatedAt") > 0
                        )
                        result["legacy_cache_unexpired"] = (
                            int(expires_at) >= int(time.time())
                        )
                        owner_id = key.split("identity-alias:v1:", 1)[1]
                        if len(owner_id) == 24 and all(
                            c in "0123456789abcdef" for c in owner_id
                        ):
                            found = db.execute(
                                "SELECT 1 FROM profiles WHERE id=? "
                                "AND access_token_enc IS NOT NULL LIMIT 1",
                                (owner_id,),
                            ).fetchone()
                            result["legacy_owner_connected"] = bool(found)
                    except (TypeError, ValueError, sqlite3.Error):
                        result["legacy_owner_connected"] = False
            elif key.startswith("identity-graph:v1:"):
                result["verified_graph_present"] = True
                if value:
                    result["verified_graph_known_show"] = True
                    if (value.get("canonicalImdb") == "tt44051354"
                            and "tt44094505" in value.get("aliases", [])):
                        result["verified_graph_alias_matches"] = True
        result["identity_records_scanned"] = len(rows)
        if len(rows) == 1000:
            result["identity_records_truncated"] = True
        return result
    except sqlite3.Error as ex:
        raise Stop("Read-only legacy identity evidence query failed") from ex
    finally:
        db.close()


def main():
    try:
        bridge = get_container("trakt-bridge")
        aio = get_container("aiostreams")
        out = {
            "audit": "gateway-readiness-v1",
            "docker_mutations": 0,
            "aio_mutations": 0,
            "sqlite_mutations": 0,
            "network": valid_networks(bridge, aio),
            "identity": sqlite_status(bridge),
        }
        print(json.dumps(out, sort_keys=True))
    except (Stop, OSError, TypeError) as ex:
        raise SystemExit("STOP: " + str(ex)) from None


if __name__ == "__main__":
    main()
