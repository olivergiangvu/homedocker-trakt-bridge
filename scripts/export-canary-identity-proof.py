#!/usr/bin/env python3
"""Export ONE nonsecret RC6 Trakt show mapping for a standalone gateway canary.

READ-ONLY on production SQLite and Docker; writes one new private proof file
to a separately chosen path. Never copies Bridge SQLite, tokens, watched
history, OAuth credentials, profile metadata or AIO DB.

Do not invoke unless a dedicated canary directory has been prepared and
operator has chosen an absolute --output. A failed export leaves no proof.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

RC6 = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
       "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")
PROFILE = re.compile(r"^[0-9a-f]{24}$")
IMDB = re.compile(r"^tt[0-9]{7,12}$")
TTL_MIN = 60


class Stop(Exception):
    pass


def inspect_bridge():
    p = subprocess.run(
        ["docker", "container", "inspect", "trakt-bridge"],
        capture_output=True, text=True, check=False
    )
    if p.returncode:
        raise Stop("Could not inspect running Bridge")
    try:
        result = json.loads(p.stdout)
        if len(result) != 1 or result[0]["Name"] != "/trakt-bridge":
            raise Stop("Bridge container name mismatch")
        container = result[0]
        if (container["Config"]["Image"] != RC6
                or not container["State"]["Running"]):
            raise Stop("Bridge must still be running expected pinned RC6")
        mounts = [m for m in container["Mounts"]
                  if m.get("Destination") == "/app/data"]
        if len(mounts) != 1:
            raise Stop("Bridge data mount is ambiguous")
        path = Path(mounts[0]["Source"]) / "bridge.db"
        if not path.is_file() or path.is_symlink():
            raise Stop("Expected Bridge database is absent/symlink")
        return path
    except (ValueError, TypeError, KeyError) as ex:
        raise Stop("Malformed Docker inspect response") from ex


def proof_from_db(db, now=None):
    if now is None:
        now = int(time.time())
    db.execute("PRAGMA query_only=ON")
    rows = db.execute(
        "SELECT cache_key,payload_json,expires_at FROM media_cache "
        "WHERE cache_key LIKE 'identity-alias:v1:%' LIMIT 1001"
    ).fetchall()
    if len(rows) != 1:
        raise Stop("Expected exactly one unambiguous RC6 legacy cache")
    key, raw, expires_at = rows[0]
    profile = key.removeprefix("identity-alias:v1:")
    if not key.startswith("identity-alias:v1:") or not PROFILE.fullmatch(profile):
        raise Stop("Invalid RC6 profile scope")
    connected = db.execute(
        "SELECT 1 FROM profiles WHERE id=? AND access_token_enc IS NOT NULL LIMIT 1",
        (profile,)
    ).fetchone()
    if not connected:
        raise Stop("Legacy owner is not a connected Trakt profile")
    if int(expires_at) <= now + TTL_MIN:
        raise Stop("Legacy proof is expired or about to expire")
    try:
        legacy = json.loads(raw)
    except ValueError as ex:
        raise Stop("Invalid legacy cache JSON") from ex
    if not isinstance(legacy, dict) or not isinstance(legacy.get("shows"), dict):
        raise Stop("Invalid legacy state")
    # One media_cache row can contain MANY learned shows. We only export
    # the exact verified fixture; unrelated valid rows must not block it.
    # However, if another Trakt show claims either fixture IMDb spelling,
    # the equivalence is ambiguous and the entire export must stop.
    shows = legacy["shows"]
    if not 1 <= len(shows) <= 10000:
        raise Stop("Legacy shows empty or exceeds audit bound")
    show = shows.get("263102")
    if not isinstance(show, dict):
        raise Stop("Known verified show is missing from legacy cache")
    claimed_ids = {"tt44051354", "tt44094505"}
    for show_id, other in shows.items():
        if show_id == "263102":
            continue
        if not (isinstance(show_id, str) and show_id.isascii()
                and show_id.isdecimal()
                and 0 < int(show_id) <= 9007199254740991):
            raise Stop("Invalid unrelated legacy Trakt show key")
        if not isinstance(other, dict):
            raise Stop("Invalid unrelated legacy show row")
        for provider in ("preferredMetaId", "traktImdb"):
            spelling = other.get(provider)
            if (isinstance(spelling, str)
                    and spelling.strip().lower() in claimed_ids):
                raise Stop("Cross-show IMDb collision in RC6 legacy cache")
    if not isinstance(show, dict) or (show.get("traktImdb") != "tt44051354"
           or show.get("preferredMetaId") != "tt44094505"
           or not IMDB.fullmatch(show["traktImdb"])
           or not IMDB.fullmatch(show["preferredMetaId"])
           or not isinstance(show.get("updatedAt"), int)
           or not 0 < show["updatedAt"] <= now):
        raise Stop("Legacy pair is not the verified current fixture")
    # Export ONLY exact asserted series keys. No token, user, episode, resume,
    # watched history or raw media_cache payloads are included.
    return {
        "contract": "homedocker-canary-identity-proof-v1",
        "profileId": profile,
        "graph": {
            "revision": 1, "conflicts": [],
            "shows": {
                "263102": {
                    "canonicalImdb": "tt44051354",
                    "aliases": ["tt44094505"],
                    "tmdb": None, "tvdb": None,
                }
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="New absolute sanitized proof filepath; not an existing file")
    parser.add_argument("--node-uid", type=int, default=1000,
                        help="uid used by isolated, non-root Node container")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute() or output.name != "identity-proof.json":
        raise Stop("Output must be absolute and named identity-proof.json")
    if not 1 <= args.node_uid <= 65535:
        raise Stop("Invalid restricted Node user uid")
    parent = output.parent
    if (not parent.is_dir() or parent.is_symlink()
            or parent.stat().st_mode & 0o077):
        raise Stop("Canary proof directory must already exist with mode 0700")
    if output.exists() or output.is_symlink():
        raise Stop("Refusing to overwrite an existing proof")
    dbfile = inspect_bridge()
    db = sqlite3.connect(dbfile.resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=5)
    try:
        proof = proof_from_db(db)
    finally:
        db.close()

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as out:
            json.dump(proof, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
        os.chown(output, args.node_uid, args.node_uid)
        os.chmod(output, 0o600)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print("CANARY_PROOF=CREATED_NO_SECRETS")
    print("CANARY_BRIDGE_DB=READ_ONLY")
    print("CANARY_TRKT_EVENTS=0")
    print("CANARY_AIO_MUTATIONS=0")


if __name__ == "__main__":
    try:
        main()
    except (Stop, OSError, sqlite3.Error, ValueError, TypeError) as ex:
        raise SystemExit("STOP: " + str(ex)) from None
