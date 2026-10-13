#!/usr/bin/env python3
"""RC7 owner-side repair: suppress one stale AIO NextUp branch via AIO's public API.

No AIOStreams source/image changes; no Trakt requests or SQL writes.
DRY-RUN is the default. --apply needs an exact confirmation and a temporary
Jellyfin API key. The only write is an official Jellyfin Rating(dislike) action
on the obsolete *series* identity, which AIO treats as "dropped from NextUp".
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DB = "/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite"
SINK_NAME = "homedocker-trakt-bridge"
SINK_INSTANCE = "e3fe3b0"
TRAKT = "tt44051354"
AIO = "tt44094505"
TMDB = "276470"
MAX_BODY = 2_000_000
EXPECTED = {("trakt", ep) for ep in range(1, 11)} | {
    ("aio", ep) for ep in range(1, 9)
}


def stop(message):
    raise SystemExit("STOP: " + message)


def open_readonly(path):
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        stop("local AIO database is missing or not a regular file")
    db = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def inspect_local(db, *, expect_dropped=False):
    sinks = db.execute(
        """SELECT uuid, persona FROM watch_sinks
           WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL""",
        (SINK_NAME, SINK_INSTANCE),
    ).fetchall()
    if len(sinks) != 1:
        stop("expected exactly one active connected Bridge AIO watch sink")
    uuid, persona = sinks[0]["uuid"], sinks[0]["persona"]
    if not isinstance(uuid, str) or not isinstance(persona, str):
        stop("invalid watch scope")

    rows = db.execute(
        """SELECT item_key, base_id, series_key, video_id, season, episode,
                  played, position_ms, origin, match_key
           FROM watch_state
           WHERE uuid=? AND persona=? AND
                 series_key IN (?,?)
           ORDER BY item_key""",
        (uuid, persona, "s|" + TRAKT, "s|" + AIO)
    ).fetchall()
    if len(rows) != 18:
        stop("expected exactly 18 historical episode rows in the two series-key groups")
    seen = set()
    for row in rows:
        series = row["series_key"]
        ep = int(row["episode"] or -1)
        season = int(row["season"] or -1)
        if season != 1 or ep < 1 or row["match_key"] is not None:
            stop("unexpected numbering or canonical match key; refuse stale repair")
        is_trakt = series == "s|" + TRAKT
        source = "trakt" if is_trakt else "aio"
        entry = (source, ep)
        if entry in seen:
            stop("duplicate stored episode in local scope")
        seen.add(entry)
        expected_video = (TRAKT if is_trakt else "tmdb:" + TMDB) + f":1:{ep}"
        expected_base = TRAKT if is_trakt else AIO
        expected_played = (ep <= 9) if is_trakt else True
        expected_origin = "import" if is_trakt and ep <= 9 else "local"
        if (row["item_key"] != "e|" + expected_video or
                row["video_id"] != expected_video or
                row["base_id"] != expected_base or
                bool(row["played"]) != expected_played or
                row["origin"] != expected_origin):
            stop("stored history changed since B8; fail closed, do not repair")
        position = int(row["position_ms"] or 0)
        if is_trakt and ep == 10:
            if position <= 0:
                stop("Trakt S01E10 resume is no longer active")
        elif position != 0:
            stop("non-resume episode has unexpected position")
    if seen != EXPECTED:
        stop("history episode set does not match the read-only B8 fixture")
    existing = db.execute(
        """SELECT item_key, dropped, rating, likes FROM watch_state
           WHERE uuid=? AND persona=? AND item_key=?""",
        (uuid, persona, "s|" + AIO),
    ).fetchall()
    if expect_dropped:
        if (len(existing) != 1 or int(existing[0]["dropped"] or 0) != 1
                or existing[0]["rating"] is not None
                or existing[0]["likes"] is not None):
            stop("rollback requires exactly one drop-only series row with no rating or likes")
    elif existing:
        stop("stale series already has a persisted row; inspect before changing rating/drop")

    print("AIO_CONNECTED_SCOPE_VERIFIED=true")
    print("TRAKT_WATCHED=9")
    print("TRAKT_RESUME=S01E10")
    print("STALE_AIO_WATCHED=8")
    print("STALE_AIO_LATEST=S01E08")
    print("HISTORY_ROWS_UNCHANGED=18")
    return (uuid, persona)


def packed_series_id(imdb):
    if imdb != AIO:
        stop("series ID packing only permits the documented AIO alias")
    buf = bytearray(16)
    buf[0] = 0xA1
    buf[1] = (2 << 4) | 1
    buf[2] = 2
    buf[3:9] = int(imdb[2:]).to_bytes(6, "big")
    buf[9:11] = (65535).to_bytes(2, "big")
    buf[11:13] = (65535).to_bytes(2, "big")
    return buf.hex()


def packed_id(raw):
    """Read only AIO public 'a1' packed Jellyfin IDs, not opaque 'b2' IDs."""
    if not isinstance(raw, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", raw):
        return None
    data = bytes.fromhex(raw)
    if data[0] != 0xa1 or data[2] != 2 or (data[1] & 0xf) != 1:
        return None
    kind = {2: "series", 4: "episode"}.get(data[1] >> 4)
    if kind is None:
        return None
    imdb = "tt" + str(int.from_bytes(data[3:9], "big")).zfill(7)
    s = int.from_bytes(data[9:11], "big")
    e = int.from_bytes(data[11:13], "big")
    return kind, imdb, None if s == 65535 else s, None if e == 65535 else e


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_url(base):
    url = urlsplit(base)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost")
            or url.username or url.password or url.query or url.fragment
            or url.path.rstrip("/") != "/jellyfin"):
        stop("API must be an uncredentialed HTTP loopback /jellyfin URL")
    try:
        if not (1 <= url.port <= 65535):
            stop("local API port is required")
    except (TypeError, ValueError):
        stop("invalid API port")
    return base.rstrip("/")


def read_token(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        stop("temporary API key must be a real file (not symlink)")
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        stop("temporary API key file must have mode 600 or stricter")
    value = p.read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9._~+\-]{16,1024}", value):
        stop("temporary API key format not supported")
    return value


def request(opener, method, url, token, data=None):
    headers = {"X-Emby-Token": token,
               "Accept": "application/json",
               "User-Agent": "homedocker-rc7-bridge-owner-repair/1"}
    req = Request(url, method=method, headers=headers, data=data)
    try:
        with opener.open(req, timeout=15) as response:
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                stop("Jellyfin API response too large")
            if not raw:
                return {}
            return json.loads(raw)
    except HTTPError as err:
        stop("Jellyfin API returned HTTP %d on %s (response suppressed)" %
             (err.code, method))
    except (URLError, TimeoutError):
        stop("loopback Jellyfin API unavailable")
    except (ValueError, UnicodeError):
        stop("invalid Jellyfin response")


def list_items(payload, name):
    if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
        stop(name + " did not return a Jellyfin Items object")
    items = payload["Items"]
    if len(items) > 50 or any(not isinstance(x, dict) for x in items):
        stop(name + " response shape not supported")
    return items


def api_preflight(resume, nextup):
    canonical = []
    stale = []
    for item in nextup:
        ep = packed_id(item.get("Id"))
        series = packed_id(item.get("SeriesId"))
        if not ep or ep[0] != "episode" or not series or series[0] != "series":
            continue
        if series[1] == TRAKT and ep[2:] == (1, 10):
            canonical.append(item)
        if series[1] == AIO and ep[2:] == (1, 9):
            stale.append(item)
    if len(canonical) != 1 or len(stale) != 1:
        stop("NextUp no longer has exact canonical S01E10 + stale AIO S01E09")
    target_series_id = stale[0]["SeriesId"]
    # Avoid hiding a real resume on the alias branch.
    stale_data = stale[0].get("UserData") or {}
    if not isinstance(stale_data, dict) or float(
        stale_data.get("PlaybackPositionTicks") or 0
    ) > 0:
        stop("stale candidate is resumable or has malformed UserData")
    active = []
    for item in resume:
        ep = packed_id(item.get("Id"))
        series = packed_id(item.get("SeriesId"))
        if (ep and series and ep[0] == "episode" and ep[2:] == (1, 10)
                and series[0] == "series" and series[1] == TRAKT):
            ticks = (item.get("UserData") or {}).get("PlaybackPositionTicks")
            if isinstance(ticks, (int, float)) and ticks > 0:
                active.append(item)
    if len(active) != 1:
        stop("canonical S01E10 not found exactly once in Resume")
    print("AIO_HTTP_NEXTUP_DOUBLE_CONFIRMED=true")
    print("TRAKT_CANONICAL_S01E10=true")
    print("AIO_STALE_S01E09=true")
    print("REPAIR_TARGET=one_stale_series_only")
    return target_series_id


def verify_nextup_after(opener, base, token, original_id):
    rows = list_items(request(opener, "GET",
                              base + "/Shows/NextUp?Limit=50&EnableResumable=true",
                              token), "NextUp after apply")
    if any(x.get("SeriesId") == original_id for x in rows):
        print("POST_VERIFY=FAILED_STALE_SERIES_STILL_PRESENT")
        return False
    if not any(
        (lambda ep, series: ep and series
         and ep[0] == "episode" and ep[2:] == (1, 10)
         and series[0] == "series" and series[1] == TRAKT)(
            packed_id(x.get("Id")), packed_id(x.get("SeriesId"))
        ) for x in rows
    ):
        print("POST_VERIFY=FAILED_CANONICAL_S01E10_NOT_VISIBLE")
        return False
    print("POST_VERIFY=PASS_STALE_SERIES_ABSENT_CANONICAL_RETAINED")
    return True


def run(args):
    if args.undo and args.apply:
        stop("--undo and --apply are mutually exclusive")
    with closing(open_readonly(args.aio_db)) as db:
        scope = inspect_local(db, expect_dropped=args.undo)
    base = loopback_url(args.base_url)
    token = read_token(args.token_file)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    if args.undo:
        if args.confirm_stale_imdb != AIO:
            stop("--undo requires --confirm-stale-imdb " + AIO)
        series_id = packed_series_id(AIO)
        print("MODE=EXPLICIT_UNDO")
        request(opener, "DELETE", base + "/UserItems/" + series_id + "/Rating", token)
        with closing(open_readonly(args.aio_db)) as db:
            check = db.execute(
                "SELECT dropped FROM watch_state WHERE uuid=? AND persona=? "
                "AND item_key=? AND series_key=? AND base_id=?",
                (scope[0], scope[1], "s|" + AIO, "s|" + AIO, AIO),
            ).fetchall()
        if len(check) != 1 or int(check[0]["dropped"] or 0) != 0:
            stop("rollback API call accepted but stored drop was not cleared; inspect scope")
        print("ROLLBACK_VERIFIED=STALE_SERIES_DROP_CLEARED")
        return
    resume = list_items(request(opener, "GET",
                                base + "/UserItems/Resume?Limit=50", token),
                        "Resume")
    nextup = list_items(request(opener, "GET",
                                base + "/Shows/NextUp?Limit=50&EnableResumable=true",
                                token), "NextUp")
    series_id = api_preflight(resume, nextup)

    if not args.apply:
        print("MODE=DRY_RUN")
        print("PLANNED_API_ACTION=POST_SERIES_DISLIKE_TO_HIDE_STALE_NEXTUP")
        print("NO_CHANGES_MADE=true")
        return

    if args.confirm_stale_imdb != AIO:
        stop("--apply requires --confirm-stale-imdb " + AIO)
    print("MODE=EXPLICIT_APPLY")
    # Official AIO Jellyfin rate(series, likes=false) sets dropped status.
    # Bridge manifest does not advertise 'dropped' as a Trakt push event.
    request(opener, "POST", base + "/UserItems/" + series_id +
            "/Rating?Likes=false", token, data=b"")
    print("OFFICIAL_AIO_SERIES_DROP_POST_ACCEPTED=true")
    with closing(open_readonly(args.aio_db)) as db:
        inspect_local(db, expect_dropped=True)
    print("POST_DB_VERIFY=DROP_ROW_SET_EPISODE_HISTORY_UNCHANGED")
    if not verify_nextup_after(opener, base, token, series_id):
        stop("post-write NextUp verification failed; no automatic rollback")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aio-db", default=DB)
    parser.add_argument("--base-url", default="http://127.0.0.1:3240/jellyfin")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="allow exactly one official AIO Jellyfin series drop")
    parser.add_argument("--undo", action="store_true",
                        help="explicitly undo the one stale series drop via official API")
    parser.add_argument("--confirm-stale-imdb", default="")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
