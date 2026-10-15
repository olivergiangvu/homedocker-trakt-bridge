#!/usr/bin/env python3
"""R23B-2A: read-only, user-authenticated AIO NextUp source/alias evidence.

This probe is NOT a Gateway cutover. It never connects to Trakt or the canary's
Gateway to normalize; it measures real *source* NextUp and verified provider
spelling seen by the SAME AIO Jellyfin session.

An EXISTING Jellyfin session token is entered via getpass on HomeDocker SSH.
The token remains in process memory only, is never saved/echoed/logged, and
is only sent to the fixed host-loopback AIO Jellyfin API. No new session
creation/login POST, write requests, AIO database edits or client routing.

WARNING: ordinary AIO GET handlers may update AIO's own server-side caches.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import getpass
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib import error, parse, request

HERE = Path(__file__).resolve().parent
AIO = "http://127.0.0.1:3240/jellyfin"
GATEWAY = "http://127.0.0.1:17141"
R19 = HERE / "preview-v140-legacy-import.py"
MAX_HTTP = 2 * 1024 * 1024
KNOWN_PAIR = ("tt44051354", "tt44094505")
KNOWN_SHOW = "263102"
ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
USER_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
SESSION = re.compile(r"^[^\s\x00-\x1f\x7f]{16,1024}$")

class Stop(Exception):
    pass

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = request.build_opener(request.ProxyHandler({}), NoRedirect())

def preview_module():
    spec = importlib.util.spec_from_file_location("r19_for_r23b", R19)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def verified_source(db_path):
    mod = preview_module()
    state = mod.preview(db_path)
    if (state["status"] != "DRY_RUN_READY"
        or state["verified_show_candidates"] != 15
        or state["alias_group_candidates"] != 1
        or state["existing_identity_graph_records"] != 0):
        raise Stop("legacy scrobble evidence changed; R19/R20 must be requalified")
    path = Path(db_path)
    with closing(sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=6
    )) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            "SELECT cache_key,payload_json,expires_at FROM media_cache "
            "WHERE cache_key LIKE 'identity-alias:v1:%' LIMIT 2"
        ).fetchall()
        if len(rows) != 1:
            raise Stop("legacy source scope changed")
        key, raw, expiry = rows[0]
        if not key.startswith("identity-alias:v1:") or int(expiry) <= int(time.time()):
            raise Stop("legacy source expired or invalid")
        show = json.loads(raw).get("shows", {}).get(KNOWN_SHOW)
        if (not isinstance(show, dict)
            or show.get("traktImdb") != KNOWN_PAIR[0]
            or show.get("preferredMetaId") != KNOWN_PAIR[1]):
            raise Stop("previously verified cross-IMDb pair changed")
    return {"verified_source": True, "shows": 15, "alias_groups": 1}

def fixed_get(path, *, token=None, limit=MAX_HTTP):
    if (not path.startswith("/") or "://" in path or "//" in path
        or "\r" in path or "\n" in path):
        raise Stop("unsafe AIO GET path")
    url = AIO + path
    headers = {
        "Accept": "application/json",
        "X-Emby-Authorization": (
            'MediaBrowser Client="HomeDockerR23BReadOnly",Device="SSH",'
            'DeviceId="homedocker-r23b-audit",Version="1.0.0"'
        ),
    }
    if token:
        headers["X-Emby-Token"] = token
    req = request.Request(url, method="GET", headers=headers)
    started = time.monotonic()
    try:
        with OPENER.open(req, timeout=12) as response:
            status = response.status
            if status != 200:
                raise Stop("AIO GET returned an unexpected HTTP status")
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise Stop("AIO JSON response too large")
    except error.HTTPError as ex:
        if ex.code == 404 and path.startswith("/Items/"):
            # A stale NextUp series may have no AIO metadata row; absence is
            # a bounded observation, NOT justification to merge identities.
            return None, int((time.monotonic() - started) * 1000)
        raise Stop("AIO GET status " + str(ex.code)) from None
    except (error.URLError, OSError, TimeoutError):
        raise Stop("AIO GET connection unavailable") from None
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError, TypeError):
        raise Stop("AIO returned malformed JSON") from None
    if not isinstance(parsed, dict):
        raise Stop("AIO JSON response must be an object")
    return parsed, int((time.monotonic() - started) * 1000)

def check_localhost():
    for path in ("http://127.0.0.1:17041/health", GATEWAY + "/health"):
        req = request.Request(path, method="GET")
        try:
            with OPENER.open(req, timeout=5) as response:
                if response.status != 200:
                    raise Stop("canary localhost health not HTTP 200")
        except (error.HTTPError, error.URLError, TimeoutError, OSError):
            raise Stop("R23B-1 localhost canary no longer reachable") from None

def get_existing_session():
    value = getpass.getpass("Existing AIO Jellyfin SESSION token (hidden): ").strip()
    if not SESSION.fullmatch(value):
        raise Stop("existing Jellyfin session token missing or malformed")
    return value

def item_imdb(item_id, token):
    if not ITEM_ID.fullmatch(item_id):
        return "invalid_id", None
    detail, _ = fixed_get("/Items/" + parse.quote(item_id, safe=""),
                          token=token, limit=128 * 1024)
    if detail is None:
        return "metadata_404", None
    if detail.get("Type") != "Series" or detail.get("Id") != item_id:
        return "invalid_detail", None
    ids = detail.get("ProviderIds")
    imdb = ids.get("Imdb") if isinstance(ids, dict) else None
    if isinstance(imdb, str) and re.fullmatch(r"tt[0-9]{7,12}", imdb.lower()):
        return "series", imdb.lower()
    return "series", None

def audit(token, db_path, max_series):
    if not SESSION.fullmatch(token):
        raise Stop("Jellyfin session token invalid")
    check_localhost()
    source = verified_source(db_path)
    me, _ = fixed_get("/Users/Me", token=token, limit=128 * 1024)
    if not USER_ID.fullmatch(str(me.get("Id") or "")):
        raise Stop("AIO /Users/Me session identity invalid")
    nextup, shelf_ms = fixed_get("/Shows/NextUp?Limit=50", token=token)
    rows = nextup.get("Items")
    if not isinstance(rows, list) or len(rows) > 50 or any(
        not isinstance(x, dict) for x in rows
    ):
        raise Stop("AIO NextUp response shape invalid")
    ids = list(dict.fromkeys(
        row.get("SeriesId") for row in rows
        if isinstance(row.get("SeriesId"), str) and row.get("SeriesId")
    ))
    sample = ids[:max_series]
    pairs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pairs = list(pool.map(lambda v: item_imdb(v, token), sample))
    spellings = [imdb for status, imdb in pairs if status == "series" and imdb]
    canonical = spellings.count(KNOWN_PAIR[0])
    alias = spellings.count(KNOWN_PAIR[1])
    both = canonical > 0 and alias > 0
    return {
        "audit": "R23B2A_AUTHENTICATED_AIO_NEXTUP_SOURCE_PROOF",
        "status": "SOURCE_DUPLICATE_PAIR_PRESENT" if both
                  else "DUPLICATE_NOT_OBSERVED_IN_SAMPLED_NEXTUP",
        "aio_session_user_verified": True,
        "legacy_verified_graph_candidates": source["shows"],
        "legacy_verified_alias_groups": source["alias_groups"],
        "aio_nextup_http": 200,
        "aio_nextup_duration_ms": shelf_ms,
        "aio_nextup_card_count": len(rows),
        "distinct_series_in_nextup": len(ids),
        "series_metadata_sample_limit": max_series,
        "series_metadata_checked": len(sample),
        "series_metadata_valid": sum(s == "series" for s, _ in pairs),
        "series_metadata_imdb_present": len(spellings),
        "known_canonical_cards_in_sample": canonical,
        "known_alias_cards_in_sample": alias,
        "both_verified_imdb_spellings_present_in_sample": both,
        "sample_exhaustive_for_returned_nextup": len(sample) == len(ids),
        "gateway_normalization_measured": False,
        "aio_api_calls": 2 + len(sample),
        "aio_http_methods": ["GET"],
        "aio_get_handlers_may_refresh_server_cache": True,
        "aio_history_or_playback_write_requests": 0,
        "trakt_requests": 0,
        "docker_or_nginx_modifications": 0,
        "credentials_user_ids_episode_ids_imdb_values_or_history_printed": False,
        "session_token_saved": False,
        "bridge_graph_migration_applied": False,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-series-details", type=int, default=20)
    parser.add_argument("--bridge-db", default=preview_module().DEFAULT_DB)
    args = parser.parse_args()
    if not 0 <= args.max_series_details <= 50:
        print("STOP: invalid metadata request limit", file=sys.stderr)
        sys.exit(1)
    try:
        evidence = audit(get_existing_session(), args.bridge_db,
                         args.max_series_details)
        print(json.dumps(evidence, indent=2))
        print("R23B2A_READ_ONLY_AIO_NEXTUP_PROOF_COMPLETE")
    except (Stop, sqlite3.Error, OSError, ValueError, TypeError,
            KeyboardInterrupt):
        print("STOP: R23B-2A source audit unavailable; no API writes attempted",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
