#!/usr/bin/env python3
"""RC3-H: GET-only AIO NextUp 50-card canonical/alias visibility proof.

Verify current mapped Jellyfin user, Gateway Filter OFF, two identical 50
window reads, scoped Bridge identity graph and per-series provider IMDb.
No AIO/Bridge writes; does not change client response, pagination, nginx,
Docker, Trakt history or Gateway Filter. Output only aggregate counts.
No user/show/episode/IMDb/Jellyfin IDs, titles, progress, tokens or URLs.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

HERE = Path(__file__).parent


def load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, HERE / path)
    if spec is None or spec.loader is None:
        raise RuntimeError("MODULE_NOT_FOUND")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adaptive = load_module(
    "audit-v140-rc3-adaptive-window-homedocker.py", "rc3h_adaptive"
)
core = adaptive.core
identity = load_module(
    "audit-v140-rc3-alias-timeline-coverage-homedocker.py", "rc3h_identity"
)

MAX_GROUPS = 200
MAX_METADATA = 50
MAX_CONCURRENCY = 4
DEADLINE_SECONDS = 35


def verified_groups_from_bridge():
    db_file = adaptive.DB_PATH
    if not db_file.is_file() or db_file.is_symlink():
        raise core.StopAudit("BRIDGE_DATABASE_UNAVAILABLE")
    try:
        with closing(sqlite3.connect(
            db_file.resolve().as_uri() + "?mode=ro", uri=True, timeout=3
        )) as db:
            db.execute("PRAGMA query_only=ON")
            profiles = db.execute(
                "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
            ).fetchall()
            if len(profiles) != 1:
                raise core.StopAudit("CONNECTED_PROFILE_NOT_UNIQUE")
            entry = db.execute(
                "SELECT payload_json FROM media_cache "
                "WHERE cache_key=? AND expires_at > "
                "CAST(strftime('%s','now') AS INTEGER)",
                ("identity-graph:v1:" + profiles[0][0],)
            ).fetchone()
    except sqlite3.Error:
        raise core.StopAudit("BRIDGE_DATABASE_UNAVAILABLE") from None

    if not entry:
        raise core.StopAudit("VERIFIED_GRAPH_UNAVAILABLE")
    try:
        graph = json.loads(entry[0])
    except (ValueError, TypeError):
        raise core.StopAudit("GRAPH_UNTRUSTED") from None
    try:
        _, groups = identity.verified_alias_groups(graph)
    except identity.AuditStop:
        raise core.StopAudit("GRAPH_UNTRUSTED") from None
    if not groups or len(groups) > MAX_GROUPS:
        raise core.StopAudit("VERIFIED_ALIAS_GROUPS_UNAVAILABLE")
    return groups


def summarize_visibility(rows, series_imdb, groups):
    """Pure diagnostic; return counts, never individual identity data."""
    owner = {}
    for i, spellings in enumerate(groups):
        for role, imdb in enumerate(spellings):
            if imdb in owner:
                raise core.StopAudit("MULTI_OWNER_IDENTITY")
            owner[imdb] = (i, role == 0)
    observed = [set() for _ in groups]
    visible_canonical = visible_alias = outside_graph = invalid_metadata = 0
    for row in rows:
        imdb = series_imdb.get(row["SeriesId"])
        if not isinstance(imdb, str) or not adaptive.IMDB.fullmatch(imdb):
            invalid_metadata += 1
            continue
        match = owner.get(imdb)
        if match is None:
            outside_graph += 1
            continue
        group, canonical = match
        observed[group].add("canonical" if canonical else "alias")
        if canonical:
            visible_canonical += 1
        else:
            visible_alias += 1
    return {
        "verified_alias_groups": len(groups),
        "window_items": len(rows),
        "series_metadata_checked": len(series_imdb),
        "valid_imdb_metadata_cards": len(rows) - invalid_metadata,
        "outside_verified_alias_group_cards": outside_graph,
        "canonical_cards": visible_canonical,
        "alias_cards": visible_alias,
        "groups_canonical_only_in_window": sum(
            states == {"canonical"} for states in observed
        ),
        "groups_alias_only_in_window": sum(
            states == {"alias"} for states in observed
        ),
        "groups_both_spellings_in_window": sum(
            states == {"canonical", "alias"} for states in observed
        ),
        "groups_absent_from_window": sum(not states for states in observed),
    }


def main():
    token, mapped = core.credential()
    core.bridge_gate()
    groups = verified_groups_from_bridge()
    reader = core.make_reader(token, time.monotonic() + DEADLINE_SECONDS)
    user = reader("/Users/Me")
    if user.get("Id") != mapped:
        raise core.StopAudit("USER_IDENTITY_MISMATCH")
    url_path = "/Shows/NextUp?StartIndex=0&Limit=50"
    first = reader(url_path)
    second = reader(url_path)
    if first != second:
        raise core.StopAudit("NEXTUP_WINDOW_CHANGED")
    rows = adaptive.items(first, MAX_METADATA)
    if not rows:
        raise core.StopAudit("NEXTUP_EMPTY")
    series_ids = sorted({row["SeriesId"] for row in rows})
    if len(series_ids) > MAX_METADATA:
        raise core.StopAudit("METADATA_LIMIT_EXCEEDED")

    def detail(series_id):
        data = reader("/Items/" + quote(series_id, safe=""))
        if data.get("Type") != "Series" or data.get("Id") != series_id:
            raise core.StopAudit("SERIES_IDENTITY_UNVERIFIED")
        provider = data.get("ProviderIds")
        imdb = provider.get("Imdb") if isinstance(provider, dict) else None
        imdb = imdb.lower() if isinstance(imdb, str) else None
        return (series_id, imdb)

    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
            metadata = dict(executor.map(detail, series_ids))
    except (core.StopAudit, OSError, ValueError, TypeError):
        raise core.StopAudit("SERIES_METADATA_UNAVAILABLE") from None

    summary = summarize_visibility(rows, metadata, groups)
    core.bridge_gate()
    print("RC3H_AUDIT=NEXTUP_CANONICAL_ALIAS_VISIBILITY")
    print("RC3H_USER=MAPPED")
    print("RC3H_WINDOW_DOUBLE_READ=STABLE")
    for key, value in summary.items():
        print("RC3H_" + key.upper() + "=" + str(value))
    print("RC3H_FILTER_FINAL=OFF")
    print("RC3H_READ_ONLY=YES")
    print("RC3H_HTTP=AUTHENTICATED_GET_ONLY")
    print("RC3H_SOURCE_PROGRESS_WRITES=0")
    print("RC3H_CLIENT_ROUTING_CHANGED=NO")
    print("RC3H_GLOBAL_PAGINATION_QUALIFIED=NO")
    print("RC3H_SECRETS_PRINTED=NO")


if __name__ == "__main__":
    try:
        main()
    except (core.StopAudit, sqlite3.Error, OSError, ValueError, TypeError):
        print("RC3H_RESULT=STOP_UNQUALIFIED_SOURCE")
        print("RC3H_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
