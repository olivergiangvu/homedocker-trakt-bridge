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

class StageStop(Stop):
    """Fixed, nonsecret diagnostic phase/code; never surfaces raw HTTP bodies."""
    def __init__(self, phase, code):
        self.phase = phase
        self.code = code
        super().__init__(code)

SAFE_REASONS = {
    "legacy scrobble evidence changed; R19/R20 must be requalified": "RC6_LEGACY_IDENTITY_NOT_READY",
    "legacy source scope changed": "RC6_LEGACY_SCOPE_CHANGED",
    "legacy source expired or invalid": "RC6_LEGACY_EXPIRED",
    "previously verified cross-IMDb pair changed": "RC6_VERIFIED_PAIR_CHANGED",
    "unsafe AIO GET path": "DISALLOWED_AIO_PATH",
    "AIO GET returned an unexpected HTTP status": "AIO_HTTP_UNEXPECTED_STATUS",
    "AIO JSON response too large": "AIO_RESPONSE_TOO_LARGE",
    "AIO GET connection unavailable": "AIO_LOOPBACK_CONNECTION_FAILED",
    "AIO returned malformed JSON": "AIO_RESPONSE_JSON_INVALID",
    "AIO JSON response must be an object": "AIO_RESPONSE_SHAPE_INVALID",
    "canary localhost health not HTTP 200": "CANARY_HEALTH_NON_200",
    "R23B-1 localhost canary no longer reachable": "CANARY_LOOPBACK_UNREACHABLE",
    "existing Jellyfin session token missing or malformed": "SESSION_TOKEN_FORMAT_INVALID",
    "Jellyfin session token invalid": "SESSION_TOKEN_FORMAT_INVALID",
    "AIO /Users/Me session identity invalid": "AIO_USER_ID_SHAPE_INVALID",
    "AIO NextUp response shape invalid": "AIO_NEXTUP_SHAPE_INVALID",
}

def safe_reason(error):
    # Error messages originate only from our controlled code. Never copy a
    # raw urllib/socket/sqlite exception or token into user-visible output.
    if isinstance(error, StageStop):
        return error.code
    if isinstance(error, Stop):
        value = str(error)
        if value.startswith("AIO GET status "):
            status = value.removeprefix("AIO GET status ")
            if status in {"400", "401", "403", "404", "405", "429", "500", "502", "503"}:
                return "AIO_HTTP_" + status
            return "AIO_HTTP_OTHER_STATUS"
        return SAFE_REASONS.get(value, "SOURCE_AUDIT_REJECTED")
    if isinstance(error, sqlite3.Error):
        return "BRIDGE_SQLITE_READONLY_ERROR"
    return "LOCAL_PRECHECK_OR_RUNTIME_ERROR"

def run_phase(name, function):
    try:
        return function()
    except StageStop:
        raise
    except Exception as exc:
        # Includes the R19 loader's PreviewFailure imported from a separate
        # module; no exception text, path or database value ever escapes.
        raise StageStop(name, safe_reason(exc)) from None

def source_preflight(db_path):
    # No Jellyfin token and no calls to the AIO upstream are needed here.
    run_phase("CANARY_LOCAL_HEALTH", check_localhost)
    state = run_phase("RC6_LEGACY_PROVENANCE", lambda: verified_source(db_path))
    return {
        "audit": "R23B2A_SAFE_PRECHECK",
        "status": "PRECHECK_PASS",
        "canary_dashboard_and_gateway_http": 200,
        "rc6_legacy_show_mappings": state["shows"],
        "rc6_verified_alias_groups": state["alias_groups"],
        "aio_authenticated_get_attempted": False,
        "session_token_requested_or_saved": False,
        "production_changes": 0,
    }

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
    me, _ = run_phase("AIO_USERS_ME",
                      lambda: fixed_get("/Users/Me", token=token,
                                        limit=128 * 1024))
    if not USER_ID.fullmatch(str(me.get("Id") or "")):
        raise StageStop("AIO_USERS_ME", "AIO_USER_ID_SHAPE_INVALID")
    nextup, shelf_ms = run_phase(
        "AIO_NEXTUP",
        lambda: fixed_get("/Shows/NextUp?Limit=50", token=token)
    )
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
    def read_items():
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda v: item_imdb(v, token), sample))
    pairs = run_phase("AIO_SERIES_METADATA", read_items)
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
    parser.add_argument("--preflight-only", action="store_true",
                        help="verify local canary/RC6 proof, no Jellyfin session required")
    args = parser.parse_args()
    if not 0 <= args.max_series_details <= 50:
        print('{"status":"STOP","phase":"CLI","reason":"INVALID_METADATA_SAMPLE_LIMIT"}',
              file=sys.stderr)
        sys.exit(1)
    try:
        precheck = source_preflight(args.bridge_db)
        print(json.dumps(precheck, indent=2), flush=True)
        if args.preflight_only:
            print("R23B2A_SAFE_PRECHECK_PASS")
            return
        # Never ask for an existing user token until local read-only sources
        # and both canary health endpoints have been independently qualified.
        session = run_phase("SESSION_INPUT", get_existing_session)
        evidence = run_phase(
            "AIO_SOURCE_AUDIT",
            lambda: audit(session, args.bridge_db, args.max_series_details)
        )
        print(json.dumps(evidence, indent=2))
        print("R23B2A_READ_ONLY_AIO_NEXTUP_PROOF_COMPLETE")
    except StageStop as exc:
        print(json.dumps({
            "audit": "R23B2A_SAFE_DIAGNOSTIC",
            "status": "STOP",
            "phase": exc.phase,
            "reason": exc.code,
            "token_or_media_ids_printed": False,
            "api_write_requests": 0,
        }, indent=2), file=sys.stderr)
        sys.exit(1)
    except (Exception, KeyboardInterrupt, EOFError):
        print(json.dumps({
            "audit": "R23B2A_SAFE_DIAGNOSTIC",
            "status": "STOP",
            "phase": "UNEXPECTED_LOCAL_FAILURE",
            "reason": "LOCAL_PRECHECK_OR_RUNTIME_ERROR",
            "token_or_media_ids_printed": False,
            "api_write_requests": 0,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
