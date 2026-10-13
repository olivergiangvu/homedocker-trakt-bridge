#!/usr/bin/env python3
"""Read-only rolling-upstream AIOStreams compatibility contract.

Checks HomeDocker Bridge readiness, the *current* AIO Jellyfin shelves and
known provider aliases. Uses HTTP GET only, local DB read-only, no Trakt calls.
AIO GET handlers may themselves refresh AIO's normal watch-state cache.
No image pin, no Docker mutation, no auto-fix of any watched/history.
"""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

AIO_BASE = "http://127.0.0.1:3240/jellyfin"
BRIDGE_BASE = "http://127.0.0.1:7000"
MAX_BODY = 2_000_000
KNOWN_AIO = "tt44094505"
KNOWN_TRAKT = "tt44051354"


class ContractError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_loopback(raw, path):
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path.rstrip("/") != path
            or not parsed.port or parsed.port > 65535):
        raise ContractError("Only exact HTTP 127.0.0.1 localhost service paths permitted")
    return raw.rstrip("/")


def token_from_file(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise ContractError("API key file must be a regular file, mode 600, not symlink")
    token = p.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9._~+\-]{16,1024}", token):
        raise ContractError("Temporary API key format invalid")
    return token


def http_json(opener, url, token=None):
    headers = {"Accept": "application/json",
               "User-Agent": "homedocker-rolling-aio-contract/1"}
    if token:
        headers["X-Emby-Token"] = token
    req = Request(url, method="GET", headers=headers)
    try:
        with opener.open(req, timeout=12) as response:
            if response.status != 200:
                raise ContractError("HTTP endpoint did not return 200")
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ContractError("HTTP response exceeds safe JSON limit")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ContractError("Unexpected HTTP JSON response shape")
            return data
    except HTTPError as e:
        raise ContractError("Local GET returned HTTP " + str(e.code)) from None
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError):
        raise ContractError("Local GET failed or returned invalid JSON") from None


def packed_series_imdb(item):
    """AIO's public a1 Jellyfin packed SeriesId, not an opaque b2 guess."""
    value = item.get("SeriesId") if isinstance(item, dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        return None
    b = bytes.fromhex(value)
    if b[0] != 0xA1 or b[1] >> 4 != 2 or b[1] & 0x0f != 1 or b[2] != 2:
        return None
    provider_id = int.from_bytes(b[3:9], "big")
    return "tt" + str(provider_id).zfill(7)


def shelf_items(data, name):
    items = data.get("Items")
    if not isinstance(items, list) or len(items) > 50:
        raise ContractError(name + " must supply up to 50 JSON Items")
    if any(not isinstance(x, dict) or not isinstance(x.get("Id"), str)
           for x in items):
        raise ContractError(name + " contains an invalid Jellyfin item")
    return items


def inspect_shelves(resume, nextup, alias_pairs):
    aliases = [(a, b) for _, a, b, _ in alias_pairs]
    matched = set()
    conflicts = []
    kinds = {"resume": set(), "nextup": set()}
    opaque = 0
    for shelf_name, rows in (("resume", resume), ("nextup", nextup)):
        for item in rows:
            ref = packed_series_imdb(item)
            if ref is None:
                opaque += 1
                continue
            kinds[shelf_name].add(ref)
    for first, second in aliases:
        observed = {x for x in (first, second) if x in kinds["nextup"]}
        if observed:
            matched.update(observed)
        if len(observed) == 2:
            conflicts.append((first, second))
    known_present = any(
        a in kinds["nextup"] or b in kinds["nextup"]
        for a, b in aliases
    )
    return {
        "resume_items": len(resume),
        "nextup_items": len(nextup),
        "undecodable_item_series_ids": opaque,
        "verified_alias_groups": len(aliases),
        "alias_nextup_groups_observed": sum(
            a in kinds["nextup"] or b in kinds["nextup"] for a, b in aliases
        ),
        "alias_double_nextup_groups": len(conflicts),
        "known_fixture_in_nextup": (
            KNOWN_AIO in kinds["nextup"] or KNOWN_TRAKT in kinds["nextup"]
        ),
        "known_fixture_double_nextup": (
            KNOWN_AIO in kinds["nextup"] and KNOWN_TRAKT in kinds["nextup"]
        ),
        "observation": (
            "DOUBLE_SERIES_ID_IN_NEXTUP" if conflicts
            else "NO_DOUBLE_IN_CAPTURE" if known_present
            else "ALIAS_NOT_IN_NEXTUP_PAGE"
        ),
        # Ids/episode titles deliberately not returned.
    }


def bridge_contract(health, ready):
    return {
        "health_ok": health.get("status") == "ok",
        "readiness_ok": ready.get("ready") is True and ready.get("status") == "ready"
        and ready.get("database") == "ok"
        and ready.get("schemaVersion") == ready.get("schemaExpected")
        and int(ready.get("connectedProfiles") or 0) == 1,
        "bridge_version": str(health.get("version") or "unknown")[:64],
    }


def import_monitor(path):
    if not path.is_file() or path.is_symlink():
        raise ContractError("Identity drift monitor must be placed beside this script")
    spec = importlib.util.spec_from_file_location("home_identity_drift", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_contract(aio_db, bridge_db, monitor_path, bridge_base, aio_base,
                   token_file, opener=None):
    bridge_base = require_loopback(bridge_base, "")
    aio_base = require_loopback(aio_base, "/jellyfin")
    token = token_from_file(token_file)
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    monitor = import_monitor(Path(monitor_path))
    # Exact active sink / Bridge profile and all known aliases. Fail closed on DB changes.
    try:
        with closing(monitor.ro(aio_db)) as aio, closing(monitor.ro(bridge_db)) as bridge:
            local = monitor.audit(aio, bridge)
            profile, _ = monitor.scope_and_profile(aio, bridge, monitor.SINK_NAME,
                                                    monitor.SINK_INSTANCE)
            pairs = monitor.verified_alias_pairs(bridge, profile)
    except (sqlite3.Error, monitor.AuditError, OSError, ValueError) as e:
        raise ContractError("Local watch-state read contract changed: " + str(e)) from None

    h = bridge_contract(
        http_json(opener, bridge_base + "/health"),
        http_json(opener, bridge_base + "/readiness")
    )
    resume = shelf_items(http_json(opener, aio_base + "/UserItems/Resume?Limit=50", token),
                         "Resume")
    nextup = shelf_items(
        http_json(opener, aio_base + "/Shows/NextUp?Limit=50&EnableResumable=true",
                  token), "NextUp"
    )
    shelves = inspect_shelves(resume, nextup, pairs)
    drift_risk = int(local.get("attention", 0))
    compatible = (h["health_ok"] and h["readiness_ok"] and drift_risk == 0
                  and shelves["alias_double_nextup_groups"] == 0)
    result = {
        "monitor": "homedocker-rolling-aio-contract-v1",
        "policy": "AIOSTREAMS_LATEST_BRIDGE_INDEPENDENT",
        "mode": "LOCAL_SQLITE_RO_AND_LOOPBACK_GET",
        "bridge": h,
        "identity": {
            "alias_pairs": local["pair_count"],
            "attention": drift_risk,
            "observed_mitigations": sum(
                x["status"] == "MITIGATED_BY_DROP" for x in local["pairs"]
            ),
        },
        "jellyfin": shelves,
        "contract": "PASS_CAPTURED_CHECKS" if compatible else "REVIEW_REQUIRED",
        "limitations": [
            "NextUp is a max-50 page snapshot, not a complete client UI guarantee",
            "AIO GET handlers may perform normal server-side watch-state refresh",
            "Does not inspect external/native-Trakt clients",
            "AIO remains latest; no image/version pinning or production update",
        ],
        "http_methods": "GET_ONLY",
        "script_db_writes": 0,
        "trakt_api_calls": 0,
    }
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aio-db", default="/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite")
    ap.add_argument("--bridge-db", default="/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db")
    ap.add_argument("--monitor-file", default=str(Path(__file__).with_name("monitor-identity-drift.py")))
    ap.add_argument("--bridge-base", default=BRIDGE_BASE)
    ap.add_argument("--aio-base", default=AIO_BASE)
    ap.add_argument("--token-file", required=True)
    args = ap.parse_args()
    try:
        result = check_contract(args.aio_db, args.bridge_db, args.monitor_file,
                                args.bridge_base, args.aio_base, args.token_file)
    except (ContractError, OSError, ValueError) as exc:
        raise SystemExit("STOP: rolling compatibility contract unavailable: " + str(exc)) from None
    print(json.dumps(result, sort_keys=True))
    if result["contract"] != "PASS_CAPTURED_CHECKS":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
