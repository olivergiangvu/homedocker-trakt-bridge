#!/usr/bin/env python3
"""G1A: identity and live AIO NextUp *input* audit; NO Gateway deployment.

Default --mode preflight: only Docker inspection, SQLite mode=ro/query_only,
and AIO public GET. With explicit --mode existing-token: an interactive,
hidden token enables AIO /Users/Me, /Shows/NextUp and bounded /Items/series
GETs. Explicit --mode login adds ONE AIO AuthenticateByName POST, which can
create a session. Never write watch state, Trakt, SQLite, nginx or Docker.

No user IDs, profile IDs, IMDb IDs, media IDs, credentials, episode details,
auth bodies or source JSON are printed or stored. A valid AIO session and a
single connected Bridge profile are NOT proof of their administrative mapping.
No canary graph migration; no Gateway listener/normalization; G1 stays OPEN.
"""
import argparse
from contextlib import closing
import getpass
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib import error, request

sys.dont_write_bytecode = True
AIO = "http://127.0.0.1:3240/jellyfin"
CONTAINER = "trakt-bridge"
MOUNT = "/app/data"
IMDB = re.compile(r"tt[0-9]{7,12}\Z")
PROFILE = re.compile(r"[a-f0-9]{24}\Z")
USER = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
SERIES = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TOKEN = re.compile(r"[^\s\x00-\x1f\x7f]{16,1024}\Z")
MAX_READ = 1024 * 1024
MAX_NEXTUP = 20
MAX_SHOWS = 10000
LOGIN_CONFIRM = "V140_G1_ONE_AIO_LOGIN_READONLY_NEXTUP"
TOKEN_CONFIRM = "V140_G1_AIO_EXISTING_TOKEN_GET_ONLY"
AUTH_HEADER = ('MediaBrowser Client="HomeDockerG1AReadOnly",'
               'Device="SSH",DeviceId="g1a-audit",Version="1.0"')


class Stop(Exception):
    pass


class DenyRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = request.build_opener(request.ProxyHandler({}), DenyRedirect())


def stop(code):
    raise Stop(code)


def load_legacy(raw):
    """Full conservative legacy scope, and alias relations in process memory."""
    if not isinstance(raw, dict) or not isinstance(raw.get("shows"), dict):
        stop("INVALID_LEGACY_EVIDENCE")
    shows = raw["shows"]
    if not 1 <= len(shows) <= MAX_SHOWS:
        stop("LEGACY_SHOW_COUNT_UNQUALIFIED")
    owners = {}
    pairs = {}
    for key, row in shows.items():
        if not isinstance(key, str) or not key.isdecimal() or not isinstance(row, dict):
            stop("LEGACY_ROW_INVALID")
        num = int(key)
        if not 0 < num <= 9007199254740991:
            stop("LEGACY_SHOW_ID_INVALID")
        old = row.get("preferredMetaId")
        canon = row.get("traktImdb")
        stamp = row.get("updatedAt")
        if (not isinstance(old, str) or not isinstance(canon, str)
            or not IMDB.fullmatch(old.lower())
            or not IMDB.fullmatch(canon.lower())
            or type(stamp) not in (float, int) or not float(stamp).is_integer()
            or not 0 < stamp <= 9007199254740991):
            stop("LEGACY_ROW_UNVERIFIED")
        canon, old = canon.lower(), old.lower()
        for spelling in {canon, old}:
            if spelling in owners and owners[spelling] != num:
                stop("CROSS_SHOW_IDENTITY_CONFLICT")
            owners[spelling] = num
        if canon != old:
            pairs[num] = frozenset((canon, old))
    if not pairs:
        stop("LEGACY_HAS_NO_VERIFIED_ALIASES")
    return len(shows), pairs, owners


def db_input():
    proc = subprocess.run(["docker", "container", "inspect", CONTAINER],
                          capture_output=True, text=True, timeout=15)
    if proc.returncode:
        stop("PRODUCTION_BRIDGE_UNAVAILABLE")
    containers = json.loads(proc.stdout)
    if len(containers) != 1 or containers[0].get("State", {}).get("Running") is not True:
        stop("PRODUCTION_BRIDGE_NOT_RUNNING")
    c = containers[0]
    mounts = [m for m in c.get("Mounts", [])
              if m.get("Destination") == MOUNT and m.get("Type") in ("bind", "volume")]
    if len(mounts) != 1:
        stop("BRIDGE_DATA_MOUNT_AMBIGUOUS")
    target = Path(mounts[0]["Source"]) / "bridge.db"
    if target.is_symlink() or not target.is_file():
        stop("BRIDGE_DATABASE_UNAVAILABLE")
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro",
                                uri=True, timeout=5)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")  # consistent SQLite read transaction; no DB mutation
        rows = con.execute(
            "SELECT cache_key, payload_json, expires_at FROM media_cache "
            "WHERE cache_key LIKE 'identity-alias:v1:%' "
            "OR cache_key LIKE 'identity-graph:v1:%' LIMIT 1001"
        ).fetchall()
        if len(rows) > 1000:
            stop("BRIDGE_IDENTITY_SCOPE_EXCEEDED")
        legacy = [row for row in rows if row[0].startswith("identity-alias:v1:")]
        graphs = [row for row in rows if row[0].startswith("identity-graph:v1:")]
        if len(legacy) != 1 or graphs:
            stop("BRIDGE_GRAPH_SCOPE_CHANGED_REVIEW_REQUIRED")
        name, payload, expires_at = legacy[0]
        p = name.removeprefix("identity-alias:v1:")
        if not PROFILE.fullmatch(p):
            stop("BRIDGE_PROFILE_ID_INVALID")
        linked = con.execute(
            "SELECT 1 FROM profiles WHERE id=? "
            "AND access_token_enc IS NOT NULL LIMIT 1", (p,)
        ).fetchone()
        if not linked:
            stop("BRIDGE_PROFILE_NOT_CONNECTED")
        if expires_at is None or int(expires_at) < int(time.time()):
            stop("BRIDGE_LEGACY_CACHE_EXPIRED")
        count, pairs, owners = load_legacy(json.loads(payload))
        return {"shows": count, "aliases": len(pairs),
                "pairs": pairs, "owners": owners}


def aio_request(path, token=None, *, method="GET", data=None):
    # Absolute fixed loopback origin; no proxies, redirects, arbitrary targets.
    if path not in ("/System/Info/Public", "/Users/Me",
                    "/Shows/NextUp?Limit=20", "/Users/AuthenticateByName") \
            and not (path.startswith("/Items/") and SERIES.fullmatch(path[7:])):
        stop("DISALLOWED_AIO_API_PATH")
    if method == "POST" and path != "/Users/AuthenticateByName":
        stop("DISALLOWED_HTTP_WRITE_METHOD")
    if method not in ("GET", "POST"):
        stop("DISALLOWED_HTTP_METHOD")
    if method == "GET" and path == "/Users/AuthenticateByName":
        stop("DISALLOWED_AUTH_GET")
    if token is not None and not TOKEN.fullmatch(token):
        stop("INVALID_SESSION_TOKEN")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if token is not None:
        headers["X-Emby-Token"] = token
        headers["X-Emby-Authorization"] = AUTH_HEADER
    if method == "POST":
        headers["Content-Type"] = "application/json"
        headers["X-Emby-Authorization"] = AUTH_HEADER
    req = request.Request(AIO + path, method=method, headers=headers, data=data)
    try:
        with OPENER.open(req, timeout=10) as resp:
            if resp.status != 200:
                stop("AIO_UNEXPECTED_HTTP_STATUS")
            body = resp.read(MAX_READ + 1)
            if len(body) > MAX_READ:
                stop("AIO_RESPONSE_TOO_LARGE")
    except error.HTTPError as exc:
        # Never echo upstream bodies, URLs, user identities or token.
        label = str(exc.code) if exc.code in (400, 401, 403, 404, 405, 429, 500, 502, 503) else "OTHER"
        stop("AIO_HTTP_" + label)
    except (error.URLError, OSError, TimeoutError):
        stop("AIO_LOCAL_UNAVAILABLE")
    try:
        obj = json.loads(body)
    except (ValueError, TypeError, UnicodeError):
        stop("AIO_INVALID_JSON")
    if not isinstance(obj, dict):
        stop("AIO_INVALID_RESPONSE_SHAPE")
    return obj


def login_session():
    if not sys.stdin.isatty():
        stop("INTERACTIVE_SSH_REQUIRED")
    username = getpass.getpass("AIO config UUID/alias (hidden): ")
    password = getpass.getpass("AIO config password/PIN (hidden): ")
    if (not username or len(username) > 256
        or not password or len(password) > 1024
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in username + password)):
        stop("INVALID_CREDENTIAL_INPUT")
    body = json.dumps({"Username": username, "Pw": password},
                      ensure_ascii=False).encode("utf-8")
    del password, username
    if len(body) > 8192:
        stop("AUTH_PAYLOAD_TOO_LARGE")
    result = aio_request("/Users/AuthenticateByName", method="POST", data=body)
    token, session_user = result.get("AccessToken"), result.get("User")
    if (not isinstance(token, str) or not TOKEN.fullmatch(token)
        or not isinstance(session_user, dict)
        or not isinstance(session_user.get("Id"), str)
        or not USER.fullmatch(session_user["Id"])):
        stop("AUTH_TOKEN_OR_USER_MISSING")
    return token, session_user["Id"]


def classify_nextup(rows, imdb_by_series, owners):
    """Only classify SOURCE evidence. Never perform filtering or claim effect."""
    groups = {}
    for row in rows:
        spelling = imdb_by_series.get(row["SeriesId"])
        owner = owners.get(spelling)
        if owner is not None:
            groups.setdefault(owner, []).append((row, spelling))
    pairs = 0
    safe_potential = 0
    ambiguous = 0
    for matches in groups.values():
        if len(matches) != 2 or len({x[1] for x in matches}) != 2:
            continue
        if matches[0][0]["SeriesId"] == matches[1][0]["SeriesId"]:
            continue
        pairs += 1
        first, second = (entry[0] for entry in matches)
        a, b = (int(x.get("UserData", {}).get("PlaybackPositionTicks") or 0)
                for x in (first, second))
        if a > 0 and b > 0:
            ambiguous += 1
        elif (a > 0) != (b > 0):
            safe_potential += 1
        else:
            x = first.get("UserData", {}).get("LastPlayedDate")
            y = second.get("UserData", {}).get("LastPlayedDate")
            # No comparison of dates in this observational tool; Gate G1
            # qualification of the actual JS filter remains separate.
            ambiguous += 1
    return {"live_alias_pair_candidates": pairs,
            "live_active_resume_winner_candidates": safe_potential,
            "live_ambiguous_or_unproven_pairs": ambiguous}


def live_inputs(legacy, mode):
    if mode == "existing-token":
        if not sys.stdin.isatty():
            stop("INTERACTIVE_SSH_REQUIRED")
        token = getpass.getpass("Existing AIO Jellyfin token (hidden): ").strip()
        if not TOKEN.fullmatch(token):
            stop("INVALID_HIDDEN_SESSION_TOKEN")
        expected = None
        post_count = 0
    else:
        token, expected = login_session()
        post_count = 1
    try:
        me = aio_request("/Users/Me", token)
        user = me.get("Id")
        if not isinstance(user, str) or not USER.fullmatch(user):
            stop("AIO_AUTH_USER_ID_INVALID")
        if expected is not None and user != expected:
            stop("AIO_LOGIN_SESSION_ID_MISMATCH")
        source = aio_request("/Shows/NextUp?Limit=20", token)
        rows = source.get("Items")
        if (not isinstance(rows, list) or len(rows) > MAX_NEXTUP
            or any(not isinstance(x, dict)
                   or not SERIES.fullmatch(str(x.get("SeriesId") or ""))
                   for x in rows)):
            stop("AIO_NEXTUP_SHAPE_UNQUALIFIED")
        lookup = {}
        for series in sorted({x["SeriesId"] for x in rows}):
            try:
                detail = aio_request("/Items/" + series, token)
            except Stop:
                stop("AIO_SERIES_METADATA_UNAVAILABLE")
            if detail.get("Type") != "Series" or detail.get("Id") != series:
                stop("AIO_SERIES_METADATA_USER_OR_ID_MISMATCH")
            spelling = detail.get("ProviderIds", {}).get("Imdb")
            lookup[series] = (spelling.lower() if isinstance(spelling, str)
                              and IMDB.fullmatch(spelling.lower()) else None)
        unchanged = source == aio_request("/Shows/NextUp?Limit=20", token)
        result = (classify_nextup(rows, lookup, legacy["owners"])
                  if unchanged else {"live_alias_pair_candidates": 0,
                                     "live_active_resume_winner_candidates": 0,
                                     "live_ambiguous_or_unproven_pairs": 0})
        # This is not an identity binding: Bridge has no stored AIO User ID
        # in RC6. A separate scoped candidate must be configured and tested.
        status = ("SOURCE_CHANGED_INCONCLUSIVE" if not unchanged
                  else "LIVE_PAIR_PRESENT_NEEDS_CANDIDATE_GATEWAY" if result["live_alias_pair_candidates"]
                  else "NO_LIVE_PAIR_IN_SAMPLE_SYNTHETIC_POSITIVE_STILL_REQUIRED")
        return {
            "audit": "G1A_REAL_AIO_IDENTITY_AND_NEXTUP_INPUTS",
            "status": status,
            "aio_session_user_authenticated": True,
            "one_connected_bridge_legacy_profile": True,
            "profile_identity_binding_verified": False,
            "active_candidate_identity_graph_verified": False,
            "legacy_verified_shows": legacy["shows"],
            "legacy_verified_alias_groups": legacy["aliases"],
            "source_nextup_items": len(rows),
            "source_stable_during_probe": unchanged,
            **result,
            "authentication_post_requests": post_count,
            "playback_or_history_writes_requested": 0,
            "trakt_calls": 0,
            "gateway_or_nginx_routing_modified": False,
            "production_sqlite_writes": 0,
            "g1_complete": False,
        }
    finally:
        del token


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("preflight", "existing-token", "login"),
                   required=True)
    p.add_argument("--confirm", default="")
    a = p.parse_args()
    if a.mode == "existing-token" and a.confirm != TOKEN_CONFIRM:
        stop("EXPLICIT_EXISTING_TOKEN_CONFIRM_REQUIRED")
    if a.mode == "login" and a.confirm != LOGIN_CONFIRM:
        stop("EXPLICIT_NEW_AIO_LOGIN_CONFIRM_REQUIRED")
    if a.mode == "preflight" and a.confirm:
        stop("PREFLIGHT_CANNOT_CONFIRM_LOGIN")
    legacy = db_input()
    aio_request("/System/Info/Public")
    if a.mode == "preflight":
        result = {
            "audit": "G1A_REAL_AIO_IDENTITY_AND_NEXTUP_INPUTS",
            "status": "PREFLIGHT_READY_NO_AUTH",
            "legacy_verified_shows": legacy["shows"],
            "legacy_verified_alias_groups": legacy["aliases"],
            "one_connected_bridge_legacy_profile": True,
            "authentication_post_requests": 0,
            "aio_nextup_or_history_requested": False,
            "sqlite_writes": 0,
            "g1_complete": False,
        }
    else:
        result = live_inputs(legacy, a.mode)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Stop, sqlite3.Error, json.JSONDecodeError, OSError,
            ValueError, subprocess.TimeoutExpired, EOFError,
            KeyboardInterrupt) as exc:
        # Never print arbitrary exception text or upstream response.
        print("G1A_STOP_" + (str(exc) if isinstance(exc, Stop)
                             and re.fullmatch(r"[A-Z0-9_]{3,90}", str(exc))
                             else "AUDIT_UNAVAILABLE"), file=sys.stderr)
        raise SystemExit(5) from None
