#!/usr/bin/env python3
"""RC7 AIOStreams Jellyfin shelf audit; no Trakt, SQLite, or mutating requests.

Offline mode reads already captured Jellyfin API JSON responses.
Live mode is EXPLICIT opt-in: two GETs to a loopback AIOStreams Jellyfin API.
AIOStreams' GET handlers may schedule their normal watch-state refresh.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

ALLOWED_PROVIDERS = {
    "imdb": re.compile(r"^tt\d{5,12}$", re.I),
    "tmdb": re.compile(r"^\d{1,12}$"),
    "tvdb": re.compile(r"^\d{1,12}$"),
    "trakt": re.compile(r"^\d{1,12}$"),
}
MAX_RESPONSE_BYTES = 2_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def stop(message):
    raise SystemExit("STOP: " + message)


def sha(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def episode_numbers(item):
    if str(item.get("Type", "")).lower() != "episode":
        return None
    values = (item.get("ParentIndexNumber"), item.get("IndexNumber"))
    try:
        nums = tuple(int(x) for x in values)
    except (TypeError, ValueError):
        return None
    if any(x < 0 or x > 10000 for x in nums):
        return None
    return nums


def providers(item):
    raw = item.get("ProviderIds")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        name = str(key).lower()
        pattern = ALLOWED_PROVIDERS.get(name)
        if pattern and isinstance(value, (str, int)):
            value = str(value).strip()
            if pattern.fullmatch(value):
                result[name] = value.lower() if name == "imdb" else value
    return result


def identity_keys(item):
    if not isinstance(item, dict):
        return set()
    kind = str(item.get("Type", "")).lower()
    if kind not in ("movie", "episode", "series"):
        return set()
    keys = set()
    item_id = item.get("Id")
    if isinstance(item_id, str) and item_id:
        keys.add(("exact", item_id))
    episode = episode_numbers(item)
    series_id = item.get("SeriesId")
    if episode and isinstance(series_id, str) and series_id:
        keys.add(("series-episode", series_id, *episode))
    for provider, value in providers(item).items():
        if kind == "episode":
            if episode:
                keys.add(("provider", kind, provider, value, *episode))
        else:
            keys.add(("provider", kind, provider, value))
    return keys


def match_type(a, b):
    """No title/text matching. Return strongest shared identity only."""
    common = identity_keys(a) & identity_keys(b)
    for label in ("exact", "series-episode", "provider"):
        if any(key[0] == label for key in common):
            return label
    return None


def parse_items(data, source):
    if isinstance(data, dict):
        items = data.get("Items")
    elif isinstance(data, list):
        items = data
    else:
        stop(source + " must be a Jellyfin list JSON object or array")
    if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
        stop(source + " must contain an Items array of objects")
    if len(items) > 100:
        stop(source + " contains over 100 rows; use bounded API output")
    return items


def _identity_label(item):
    kind = str(item.get("Type", "unknown")).lower()
    item_id = item.get("Id")
    item_hash = sha(item_id) if isinstance(item_id, str) and item_id else "-"
    episode = episode_numbers(item)
    ep = ("S%02dE%02d" % episode) if episode else "-"
    public = providers(item)
    refs = ",".join("%s:%s" % x for x in sorted(public.items())) or "-"
    return "KIND=%s ITEM_HASH=%s EP=%s PROVIDERS=%s" % (kind, item_hash, ep, refs)


def matching_pairs(left, right, same=False):
    """Bounded pairwise comparison; preserves distinct rows and no mutations."""
    result = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            if same and j <= i:
                continue
            match = match_type(a, b)
            if match:
                result.append((i, j, match))
    return result


def report(resume, nextup, max_details=10):
    resume = parse_items(resume, "RESUME")
    nextup = parse_items(nextup, "NEXTUP")
    rr = matching_pairs(resume, resume, same=True)
    nn = matching_pairs(nextup, nextup, same=True)
    cross = matching_pairs(resume, nextup)
    print("HOMEDOCKER_RC7_JELLYFIN_SHELF_AUDIT")
    print("TRAKT_CALLS=0")
    print("DATABASE_WRITES=0")
    print("RESUME_ROWS=%d" % len(resume))
    print("NEXTUP_ROWS=%d" % len(nextup))
    print("RESUME_SAME_SHELF_MATCHES=%d" % len(rr))
    print("NEXTUP_SAME_SHELF_MATCHES=%d" % len(nn))
    print("CROSS_SHELF_MATCHES=%d" % len(cross))
    print("CROSS_SHELF_EXACT_ID=%d" % sum(k == "exact" for _, _, k in cross))
    for shelf, pairs, items in (("RESUME", rr, resume), ("NEXTUP", nn, nextup)):
        for i, j, kind in pairs[:max_details]:
            print("SAME_SHELF %s MATCH=%s A=%s B=%s" % (
                shelf, kind, _identity_label(items[i]), _identity_label(items[j])
            ))
    for i, j, kind in cross[:max_details]:
        print("CROSS_SHELF MATCH=%s RESUME=%s NEXTUP=%s" % (
            kind, _identity_label(resume[i]), _identity_label(nextup[j])
        ))
    if rr or nn:
        print("CLASSIFICATION=SAME_SHELF_IDENTITY_COLLISION_CANDIDATE")
    elif cross:
        print("CLASSIFICATION=CROSS_SHELF_OVERLAP_ONLY")
    else:
        print("CLASSIFICATION=NO_MATCH_IN_CAPTURED_SHELVES")
    print("NOTE=Cross-shelf overlap can be legitimate; it is not proof of a duplicate UI card.")
    print("NOTE=No provider match does not rule out other IDs, native Trakt, or client cache.")
    return {"resume": len(resume), "nextup": len(nextup),
            "same_shelf": len(rr) + len(nn), "cross": len(cross)}


def checked_local_url(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/jellyfin")):
        stop("live API base must be an HTTP loopback /jellyfin URL, without query or credentials")
    try:
        _ = parsed.port
    except ValueError:
        stop("invalid local API port")
    return base_url.rstrip("/")


def read_token(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        stop("token file must be a regular non-symlink file")
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        stop("token file must have chmod 600 (or stricter)")
    value = p.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9._~+\-]{16,512}", value):
        stop("invalid Jellyfin API token file")
    return value


def get_json(opener, url, token):
    request = Request(
        url, method="GET",
        headers={"X-Emby-Token": token, "Accept": "application/json",
                 "User-Agent": "homedocker-rc7-shelf-audit/1"}
    )
    try:
        with opener.open(request, timeout=15) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                stop("API response exceeds size limit")
            return json.loads(data)
    except HTTPError as exc:
        stop("Jellyfin GET HTTP %d (body and URL suppressed)" % exc.code)
    except (URLError, TimeoutError):
        stop("Jellyfin GET failed; check loopback port, auth, and API mount")
    except (UnicodeError, ValueError):
        stop("Jellyfin response was not valid JSON")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-json", type=Path)
    parser.add_argument("--nextup-json", type=Path)
    parser.add_argument("--live", action="store_true",
                        help="explicitly permit two loopback GETs; AIO may refresh state on GET")
    parser.add_argument("--base-url", help="e.g. http://127.0.0.1:3000/jellyfin")
    parser.add_argument("--token-file", type=Path, help="chmod 600 Jellyfin token file")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        stop("limit must be 1..50")
    offline = bool(args.resume_json or args.nextup_json)
    if offline:
        if args.live or args.base_url or args.token_file or not (args.resume_json and args.nextup_json):
            stop("offline mode requires both JSON files and no live flags")
        resume = json.loads(args.resume_json.read_text(encoding="utf-8"))
        nextup = json.loads(args.nextup_json.read_text(encoding="utf-8"))
        print("MODE=OFFLINE_JSON_NO_HTTP")
    else:
        if not (args.live and args.base_url and args.token_file):
            stop("use two JSON files, or explicit --live --base-url --token-file")
        base = checked_local_url(args.base_url)
        token = read_token(args.token_file)
        opener = build_opener(ProxyHandler({}), NoRedirect())
        print("MODE=EXPLICIT_LOOPBACK_GET_ONLY")
        print("AIO_GET_MAY_TRIGGER_NORMAL_WATCH_STATE_REFRESH=true")
        resume = get_json(opener, base + "/UserItems/Resume?Limit=%d" % args.limit, token)
        nextup = get_json(
            opener, base + "/Shows/NextUp?Limit=%d&EnableResumable=true" % args.limit,
            token
        )
    report(resume, nextup)


if __name__ == "__main__":
    main()
