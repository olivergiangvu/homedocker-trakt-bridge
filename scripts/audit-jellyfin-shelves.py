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


# Historical *one-show* regression fixture from docs/DEVELOPMENT.md.
# IDs are not globally interchangeable with unrelated titles.
KNOWN_SHOW = {
    ("imdb", "tt44051354"): "TRAKT_IMDB",
    ("imdb", "tt44094505"): "AIO_IMDB",
    ("tmdb", "276470"): "TMDB",
    ("tvdb", "480791"): "TVDB",
}


def decode_packed_content_id(value):
    """Decode only AIOStreams' public a1 packed Jellyfin ID format.

    Based on packages/core/src/jellyfin/ids.ts. b2 hashed IDs cannot be
    decoded without AIO's private durable mapping table; fail open as unknown.
    """
    if not isinstance(value, str):
        return None
    compact = value.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", compact):
        return None
    buf = bytes.fromhex(compact)
    if buf[0] != 0xA1:
        return None
    kind = {1: "movie", 2: "series", 3: "season", 4: "episode", 5: "boxset"}.get(
        buf[1] >> 4
    )
    provider = {1: "imdb", 2: "tmdb", 3: "tvdb"}.get(buf[1] & 0xF)
    if not kind or not provider:
        return None
    numeric = int.from_bytes(buf[3:9], "big")
    value = (
        "tt" + str(numeric).zfill(7)
        if provider == "imdb"
        else str(numeric)
    )
    season = int.from_bytes(buf[9:11], "big")
    episode = int.from_bytes(buf[11:13], "big")
    return {
        "kind": kind, "provider": provider, "value": value,
        "season": None if season == 65535 else season,
        "episode": None if episode == 65535 else episode,
    }


def known_show_refs(item):
    """Only exact known-provider matches; never a display-name match."""
    found = set()
    for provider, value in providers(item).items():
        label = KNOWN_SHOW.get((provider, value))
        if label:
            found.add(label)
    for field in ("Id", "SeriesId"):
        d = decode_packed_content_id(item.get(field))
        if d:
            label = KNOWN_SHOW.get((d["provider"], d["value"]))
            if label:
                found.add(label)
    return found


def trace_known_show(resume_json, nextup_json, disabled_json=None):
    """Print targeted redacted evidence for known one-show IMDb split."""
    captures = [
        ("RESUME", parse_items(resume_json, "RESUME")),
        ("NEXTUP_TRUE", parse_items(nextup_json, "NEXTUP_TRUE")),
    ]
    if disabled_json is not None:
        captures.append((
            "NEXTUP_FALSE", parse_items(disabled_json, "NEXTUP_FALSE")
        ))
    print("KNOWN_SHOW_IDENTITY_TRACE=Ok_Lets_Get_Divorced")
    print("TRAKT_SHOW_ID=263102 TMDB=276470 TVDB=480791")
    seen = set()
    identified = 0
    per_episode = defaultdict(list)
    nextup_identified = []
    for shelf, items in captures:
        matched = 0
        packed = 0
        opaque = 0
        for item in items:
            fields = (item.get("Id"), item.get("SeriesId"))
            if any(decode_packed_content_id(value) for value in fields):
                packed += 1
            else:
                opaque += 1
            refs = known_show_refs(item)
            if not refs:
                continue
            matched += 1
            seen.update(refs)
            ep = episode_numbers(item)
            label = ("S%02dE%02d" % ep) if ep else "-"
            item_id = item.get("Id")
            if ep and isinstance(item_id, str):
                per_episode[ep].append((shelf, sha(item_id), frozenset(refs)))
                if shelf == "NEXTUP_TRUE":
                    nextup_identified.append((sha(item_id), ep, frozenset(refs)))
            print("SHOW_ROW SHELF=%s KIND=%s EP=%s ITEM_HASH=%s REF=%s" % (
                shelf, str(item.get("Type", "unknown")).lower(), label,
                sha(item_id) if isinstance(item_id, str) and item_id else "-",
                ",".join(sorted(refs)),
            ))
        print("SHOW_SHELF=%s KNOWN_MATCH_ROWS=%d PACKED_ROW_CANDIDATES=%d OPAQUE_ROW_CANDIDATES=%d" %
              (shelf, matched, packed, opaque))
        identified += matched
    collisions = []
    for episode, rows in sorted(per_episode.items()):
        spellings = set().union(*(r[2] for r in rows))
        item_hashes = {r[1] for r in rows}
        if ("TRAKT_IMDB" in spellings and "AIO_IMDB" in spellings
                and len(item_hashes) > 1):
            collisions.append(episode)
    print("SHOW_TRACE_MATCHED_ROWS=%d" % identified)
    print("SHOW_TRACE_REFS=%s" % (",".join(sorted(seen)) or "-"))
    print("SHOW_DUAL_IMDB_SAME_EP_CANDIDATES=%d" % len(collisions))
    trakt_next = {item_hash for item_hash, _, refs in nextup_identified
                  if "TRAKT_IMDB" in refs}
    aio_next = {item_hash for item_hash, _, refs in nextup_identified
                if "AIO_IMDB" in refs}
    both_nextup = bool(trakt_next and aio_next and
                       any(a != b for a in trakt_next for b in aio_next))
    print("SHOW_NEXTUP_DUAL_IMDB_DISTINCT_ITEMS=%s" % str(both_nextup).lower())
    if both_nextup:
        for item_hash, ep, refs in nextup_identified[:10]:
            print("SHOW_NEXTUP_CANDIDATE EP=S%02dE%02d ITEM_HASH=%s REF=%s" %
                  (ep[0], ep[1], item_hash, ",".join(sorted(refs))))
    for season, episode in collisions[:10]:
        print("SHOW_DUAL_IMDB_EP=S%02dE%02d" % (season, episode))
    if "TRAKT_IMDB" in seen and "AIO_IMDB" in seen:
        print("SHOW_TRACE_CLASS=DUAL_IMDB_IN_CAPTURE")
    elif seen:
        print("SHOW_TRACE_CLASS=PARTIAL_IDENTITY_IN_CAPTURE")
    else:
        print("SHOW_TRACE_CLASS=NO_IDENTIFIABLE_SHOW_IN_CAPTURE")
    print("NOTE=Opaque b2 IDs, native Trakt, other endpoints and rows outside the captured page are not resolved.")

def compare_nextup_modes(resume_json, enabled_json, disabled_json):
    """Compare the same Resume shelf against NextUp with/without resumable.

    This compares API results, not client card rendering or a transactionally
    consistent snapshot. Distinct sequential GETs may observe state changes.
    """
    resume = parse_items(resume_json, "RESUME")
    enabled = parse_items(enabled_json, "NEXTUP_TRUE")
    disabled = parse_items(disabled_json, "NEXTUP_FALSE")
    a = matching_pairs(resume, enabled)
    b = matching_pairs(resume, disabled)
    print("NEXTUP_MODE_COMPARISON")
    print("NEXTUP_TRUE_ROWS=%d" % len(enabled))
    print("NEXTUP_FALSE_ROWS=%d" % len(disabled))
    print("CROSS_NEXTUP_TRUE=%d" % len(a))
    print("CROSS_NEXTUP_FALSE=%d" % len(b))
    print("FALSE_NEXTUP_SAME_SHELF_MATCHES=%d" %
          len(matching_pairs(disabled, disabled, same=True)))
    if a and not b:
        print("FLAG_REMOVED_OBSERVED_CROSS_SHELF_OVERLAPS=true")
    elif a and b:
        print("FLAG_REMOVED_OBSERVED_CROSS_SHELF_OVERLAPS=false")
    else:
        print("FLAG_REMOVED_OBSERVED_CROSS_SHELF_OVERLAPS=not_applicable")
    print("NOTE=Sequential GETs are not atomic; this does not prove the client combined two shelves.")
    print("NOTE=EnableResumable=false is a per-request read filter, not a production configuration change.")


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
                        help="permit bounded loopback GETs; AIO may refresh state on GET")
    parser.add_argument("--base-url", help="e.g. http://127.0.0.1:3000/jellyfin")
    parser.add_argument("--token-file", type=Path, help="chmod 600 Jellyfin token file")
    parser.add_argument("--trace-known-show", action="store_true",
                        help="trace historical double-IMDb show by exact packed/provider IDs")
    parser.add_argument("--compare-nextup-modes", action="store_true",
                        help="live only: third GET NextUp EnableResumable=false and summarize difference")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        stop("limit must be 1..50")
    offline = bool(args.resume_json or args.nextup_json)
    if offline:
        if args.live or args.base_url or args.token_file or args.compare_nextup_modes or not (args.resume_json and args.nextup_json):
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
    if args.trace_known_show and not args.compare_nextup_modes:
        trace_known_show(resume, nextup)
    if args.compare_nextup_modes:
        nextup_without = get_json(
            opener, base + "/Shows/NextUp?Limit=%d&EnableResumable=false" % args.limit,
            token
        )
        compare_nextup_modes(resume, nextup, nextup_without)
        if args.trace_known_show:
            trace_known_show(resume, nextup, nextup_without)


if __name__ == "__main__":
    main()
