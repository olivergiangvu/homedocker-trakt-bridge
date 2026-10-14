#!/usr/bin/env python3
"""Read-only authenticated HomeDocker Jellyfin preview against AIO and canary.

No tokens, usernames, IDs, series names, episode IDs or response JSON printed.
Requests ONLY host-loopback AIO official Jellyfin API and opt-in gateway.
Does NOT write AIO, Bridge, watch-state, Docker or nginx.

Existing Jellyfin session token is preferred. If a session token is not
available, --auth login invokes the OFFICIAL Jellyfin login endpoint (creates
a temporary Jellyfin auth session, but no playback/watch-state changes).
The token and password stay only in process memory.

With --save-user-env OUTPUT (optional), atomically create one private new
0600 environment file containing only the returned Jellyfin User ID (NO token).
Never overwrite an existing file.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, request

AIO = "http://127.0.0.1:3240/jellyfin"
GATEWAY = "http://127.0.0.1:17131"
AGENT = ('MediaBrowser Client="HomeDockerReadOnlyAudit",Device="SSH",'
         'DeviceId="homedocker-nextup-preview",Version="1.0.0"')
IMDB = re.compile(r"^tt[0-9]{7,12}$", re.I)
UID = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")
NEXTUP_LIMIT = 50
MAX_DETAIL = 8
MAX_RESPONSE = 2 * 1024 * 1024


class Stop(Exception):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = request.build_opener(request.ProxyHandler({}), NoRedirect())


def json_http(method, url, *, token=None, payload=None, limit=MAX_RESPONSE):
    # URL is supplied ONLY by hard-coded host-loopback constants in this tool.
    headers = {"Accept": "application/json", "X-Emby-Authorization": AGENT}
    if token:
        headers["X-Emby-Token"] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf8")
    req = request.Request(url, headers=headers, data=data, method=method)
    try:
        with OPENER.open(req, timeout=15) as resp:
            status = resp.status
            if status != 200:
                return status, None
            body = resp.read(limit + 1)
            if len(body) > limit:
                raise Stop("Jellyfin reply exceeded safety size cap")
            return status, json.loads(body)
    except error.HTTPError as exc:
        # Deliberately do not copy upstream error messages, URLs or bodies.
        return exc.code, None
    except (error.URLError, TimeoutError, ConnectionError, OSError):
        raise Stop("Loopback AIO or gateway request failed") from None
    except ValueError:
        raise Stop("AIO/gateway returned malformed JSON") from None


def obtain_session(mode):
    if mode == "token":
        secret = getpass.getpass("Existing Jellyfin SESSION token (hidden): ").strip()
        if not secret:
            raise Stop("Empty session token")
        return secret
    username = getpass.getpass("AIO Jellyfin username/alias (hidden): ").strip()
    password = getpass.getpass("Jellyfin password/PIN (hidden): ")
    if not username or not password:
        raise Stop("Missing Jellyfin login credential")
    code, auth = json_http(
        "POST", AIO + "/Users/AuthenticateByName",
        payload={"Username": username, "Pw": password}, limit=128 * 1024,
    )
    if code != 200:
        raise Stop("Official Jellyfin login HTTP " + str(code))
    token = auth.get("AccessToken") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise Stop("Jellyfin login returned no session token")
    return token


def item_signature(obj):
    arr = obj.get("Items")
    if not isinstance(arr, list) or len(arr) > NEXTUP_LIMIT:
        raise Stop("Unexpected NextUp response shape or item count")
    result = []
    for item in arr:
        if not isinstance(item, dict):
            raise Stop("Unexpected NextUp item")
        ud = item.get("UserData") or {}
        if not isinstance(ud, dict):
            raise Stop("Unexpected NextUp UserData")
        result.append((
            item.get("Id"), item.get("SeriesId"), item.get("Type"),
            ud.get("PlaybackPositionTicks"),
        ))
    return result


def read_preview(token, max_detail):
    me_status, me = json_http("GET", AIO + "/Users/Me", token=token, limit=128 * 1024)
    if me_status != 200:
        raise Stop("AIO /Users/Me HTTP " + str(me_status)
                   + " (must use a user session token, not a tool/API key)")
    user_id = me.get("Id") if isinstance(me, dict) else None
    if not isinstance(user_id, str) or not UID.fullmatch(user_id):
        raise Stop("AIO /Users/Me returned an unexpected User ID shape")

    url = "/Shows/NextUp?Limit=" + str(NEXTUP_LIMIT)
    a_status, a = json_http("GET", AIO + url, token=token)
    b_status, b = json_http("GET", GATEWAY + url, token=token)
    if a_status != 200 or b_status != 200:
        raise Stop("Authenticated NextUp status AIO="
                   + str(a_status) + " gateway=" + str(b_status))
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise Stop("NextUp JSON envelope malformed")
    direct = item_signature(a)
    preview = item_signature(b)

    # Intentionally mismatched user in the running canary: content must not be
    # modified. AIO may asynchronously update rows between two requests; the
    # comparison is observational and NOT a proof of immutable state.
    parity = direct == preview
    series_ids = list(dict.fromkeys(row[1] for row in direct
                                    if isinstance(row[1], str) and row[1]))[:max_detail]
    counts = {
        "metadata_attempted": len(series_ids),
        "metadata_http_200": 0,
        "metadata_series_valid": 0,
        "metadata_imdb_present": 0,
        "canonical_imdb_in_sample": False,
        "alias_imdb_in_sample": False,
    }
    for sid in series_ids:
        # Never infer series IMDb from episode ProviderIds; use series detail.
        if len(sid) > 512:
            continue
        from urllib.parse import quote
        code, item = json_http(
            "GET", AIO + "/Items/" + quote(sid, safe=""),
            token=token, limit=128 * 1024,
        )
        if code != 200:
            continue
        counts["metadata_http_200"] += 1
        if (not isinstance(item, dict)
                or item.get("Type") != "Series" or item.get("Id") != sid):
            continue
        counts["metadata_series_valid"] += 1
        ids = item.get("ProviderIds") or {}
        imdb = ids.get("Imdb") if isinstance(ids, dict) else None
        if isinstance(imdb, str) and IMDB.fullmatch(imdb):
            counts["metadata_imdb_present"] += 1
            counts["canonical_imdb_in_sample"] |= imdb.lower() == "tt44051354"
            counts["alias_imdb_in_sample"] |= imdb.lower() == "tt44094505"

    result = {
        "audit": "jellyfin-auth-preview-v1",
        "media_history_writes": 0,
        "aio_settings_writes": 0,
        "api_user_session_verified": True,
        "aio_nextup_http": a_status,
        "gateway_nextup_http": b_status,
        "direct_items": len(direct),
        "gateway_items": len(preview),
        "preview_opaque_item_signature_equal": parity,
        "known_identity_both_present_in_sample":
            counts["canonical_imdb_in_sample"] and counts["alias_imdb_in_sample"],
        "series_lookup": counts,
        "normalization_applied": False,
        "user_id_printed": False,
        "session_token_saved": False,
        "next_gate": ("REVIEW_LIVE_METADATA_AND_USER_SCOPE"
                      if parity else "STOP_REVIEW_PREVIEW_PARITY"),
    }
    return result, user_id


def save_user_env(path, user_id):
    out = Path(path)
    if not out.is_absolute() or out.name != "jellyfin-user.env":
        raise Stop("Private output must be absolute and named jellyfin-user.env")
    parent = out.parent
    if (not parent.is_dir() or parent.is_symlink()
            or parent.stat().st_mode & 0o077):
        raise Stop("Output directory must exist with private mode 0700")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(out, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write("JELLYFIN_GATEWAY_AIO_USER_ID=" + user_id + "\n")
        os.chmod(out, 0o600)
    except BaseException:
        out.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--auth", choices=["token", "login"], default="token")
    p.add_argument("--max-series-details", type=int, default=8)
    p.add_argument("--save-user-env",
                   help="Optional private root-owned 0600 new file")
    args = p.parse_args()
    if not 0 <= args.max_series_details <= MAX_DETAIL:
        raise Stop("Invalid metadata sample size")
    token = obtain_session(args.auth)
    result, user_id = read_preview(token, args.max_series_details)
    result["new_login_session_created"] = args.auth == "login"
    if args.save_user_env:
        if not result["preview_opaque_item_signature_equal"]:
            raise Stop("Preview differed: will not export user mapping")
        save_user_env(args.save_user_env, user_id)
        result["user_mapping_file"] = "CREATED_0600_NO_TOKEN"
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Stop, KeyboardInterrupt) as ex:
        reason = str(ex) if isinstance(ex, Stop) else "Interrupted"
        print("STOP: " + reason, file=sys.stderr)
        sys.exit(1)
