#!/usr/bin/env python3
"""RC3-K: real same-user catalog coordinates vs scoped read-only AIO history.

HomeDocker only, a separate operator-run audit; NOT part of live Gateway.
Authenticates exact Jellyfin user; verified Trakt/IMDb graph and unique AIO
sink; GET-only NextUp/Series/catalog; both SQLite DBs mode=ro, query_only=ON.
Only aggregate counts and fixed reasons are printed. Never export identities,
tokens, episode numbers, watched coordinates, positions, timestamps or titles.
Does not build client NextUp, manufacture episode IDs or make playback claims.
"""
from contextlib import closing
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent


def import_file(filename, name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPENDENCY_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit=import_file("audit-v140-rc3-series-catalog-homedocker.py","rc3k_catalog")
identity=import_file("audit-v140-rc3-alias-timeline-coverage-homedocker.py","rc3k_identity")
core=audit.core
MAX_ROWS=5000
MAX_ALIAS_GROUPS=1
MAX_COORD=10000
SAFE_PHASES=frozenset({
    "NOT_STARTED","AUTH_AND_GATE","READ_WATCH_STATE_BEFORE",
    "NEXTUP_STABLE_WINDOW","SERIES_METADATA","CANONICAL_SELECTOR",
    "CATALOG_DOUBLE_READ","REPLAY_CHECK","WATCH_STATE_AFTER",
    "COMPARE_COORDINATES","FINAL_GATE",
})
SAFE_REASONS=frozenset({
    "USER_SCOPE_UNVERIFIED","IDENTITY_GRAPH_UNAVAILABLE","GROUP_COUNT_INVALID",
    "SINK_SCOPE_UNVERIFIED","WATCH_ROWS_OVER_BUDGET",
    "WATCH_ROW_INVALID","DUPLICATE_WATCH_COORDINATE",
    "AMBIGUOUS_PLAYBACK_STATE","WATCH_STATE_DRIFT",
    "NEXTUP_DRIFT","NEXTUP_WINDOW_UNQUALIFIED",
    "CANONICAL_NOT_PRESENT","CANONICAL_ITEM_NOT_IN_CATALOG",
    "CATALOG_DRIFT","CATALOG_COORDINATE_INVALID",
    "CATALOG_COORDINATE_DUPLICATE",
    "CATALOG_HISTORY_NOT_FULLY_BOUND","DATABASE_UNAVAILABLE",
    "AIO_GET_UNAVAILABLE","AIO_GET_NON_200","AUDIT_DEADLINE_EXCEEDED",
    "NEXTUP_18S_TIMEOUT","NEXTUP_18S_TRANSPORT_UNAVAILABLE",
    "CATALOG_PAGING_CONTRACT_INVALID","CATALOG_TOTAL_CHANGED",
    "CATALOG_EPISODE_IDENTITY_INVALID","CATALOG_DUPLICATE_ID_OR_COORDINATE",
    "CATALOG_PARTIAL_TOTAL","CATALOG_EXCEEDS_AUDIT_BUDGET",
    "SERIES_METADATA_IDENTITY_UNVERIFIED","CANONICAL_ONLY_NEXTUP_NOT_PROVEN",
})
PHASE="NOT_STARTED"


def fail(code):
    raise core.StopAudit(code)


def observe_history(spellings):
    """Verifies fresh graph AND sink along with rows, all SQLite read-only."""
    if len(spellings)<2 or len(spellings)>13 or any(
        not isinstance(x,str) or not identity.IMDB.fullmatch(x)
        for x in spellings
    ):
        fail("GROUP_COUNT_INVALID")
    try:
        with closing(identity.ro(identity.AIO_DB)) as aio, \
             closing(identity.ro(identity.BRIDGE_DB)) as bridge:
            profile=bridge.execute(
                "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
            ).fetchall()
            if len(profile)!=1:
                fail("USER_SCOPE_UNVERIFIED")
            cache=bridge.execute(
                "SELECT payload_json FROM media_cache "
                "WHERE cache_key=? AND expires_at > "
                "CAST(strftime('%s','now') AS INTEGER)",
                ("identity-graph:v1:"+profile[0][0],)
            ).fetchone()
            if not cache:
                fail("IDENTITY_GRAPH_UNAVAILABLE")
            try:
                _,groups=identity.verified_alias_groups(json.loads(cache[0]))
            except (ValueError,TypeError,identity.AuditStop):
                fail("IDENTITY_GRAPH_UNAVAILABLE")
            if groups!=[list(spellings)]:
                fail("GROUP_COUNT_INVALID")
            scopes=aio.execute(
                "SELECT uuid, persona FROM watch_sinks "
                "WHERE addon_name=? AND addon_instance_id=? "
                "AND retired_at IS NULL",
                (identity.SINK_NAME,identity.SINK_INSTANCE),
            ).fetchall()
            if len(scopes)!=1 or not all(
                isinstance(x,str) for x in scopes[0]
            ):
                fail("SINK_SCOPE_UNVERIFIED")
            keys=["s|"+s for s in spellings]
            placeholders=",".join("?" for _ in keys)
            rows=aio.execute(
                "SELECT series_key, season, episode, played, position_ms "
                "FROM watch_state WHERE uuid=? AND persona=? "
                "AND kind='episode' AND series_key IN ("
                +placeholders+") LIMIT ?",
                (*scopes[0],*keys,MAX_ROWS+1)
            ).fetchall()
            if len(rows)>MAX_ROWS:
                fail("WATCH_ROWS_OVER_BUDGET")
            return tuple(sorted(rows))
    except sqlite3.Error:
        fail("DATABASE_UNAVAILABLE")


def history_coordinates(rows,spellings):
    valid={"s|"+s for s in spellings}
    recorded={}
    by_coord={}
    canonical="s|"+spellings[0]
    for spelling,season,episode,played,position in rows:
        if spelling not in valid or type(season)!=int or not 0<=season<=200 \
                or type(episode)!=int or not 1<=episode<=MAX_COORD \
                or type(played)!=int or played not in (0,1) \
                or type(position) not in (int,float) \
                or not math.isfinite(position) or position<0 \
                or position>9007199254740991:
            fail("WATCH_ROW_INVALID")
        if played==1 and position>0:
            fail("AMBIGUOUS_PLAYBACK_STATE")
        key=(season,episode)
        owner=(spelling,key)
        if owner in recorded:
            fail("DUPLICATE_WATCH_COORDINATE")
        recorded[owner]=(played,position)
        states=by_coord.setdefault(key,[])
        states.append((spelling,played,position))
    for states in by_coord.values():
        if len(states)>len(spellings):
            fail("DUPLICATE_WATCH_COORDINATE")
        if len({x[1] for x in states})>1 or sum(
            x[1]==0 and x[2]>0 for x in states
        )>1:
            fail("AMBIGUOUS_PLAYBACK_STATE")
    canon={coord for spelling,coord in recorded if spelling==canonical}
    alias={coord for spelling,coord in recorded if spelling!=canonical}
    return {
        "raw_rows":len(rows),"logical_coords":set(by_coord),
        "canonical_rows":len(canon),"alias_rows":len(alias),
        "canonical_alias_overlap":len(canon & alias),
        "alias_only":len(alias-canon),
        "active_resumes":sum(
            any(played==0 and pos>0 for _,played,pos in states)
            for states in by_coord.values()
        ),
    }


def match_catalog(catalog,history):
    if not isinstance(catalog,list) or len(catalog)>400:
        fail("CATALOG_COORDINATE_INVALID")
    coords=set()
    for row in catalog:
        if not isinstance(row,dict) or type(row.get("ParentIndexNumber"))!=int \
                or type(row.get("IndexNumber"))!=int \
                or not 0<=row["ParentIndexNumber"]<=200 \
                or not 1<=row["IndexNumber"]<=MAX_COORD:
            fail("CATALOG_COORDINATE_INVALID")
        pair=(row["ParentIndexNumber"],row["IndexNumber"])
        if pair in coords:
            fail("CATALOG_COORDINATE_DUPLICATE")
        coords.add(pair)
    watched=history["logical_coords"]
    return {
        "catalog_rows":len(catalog),
        "history_rows":len(watched),
        "history_in_catalog":len(watched & coords),
        "history_missing_from_catalog":len(watched - coords),
        "catalog_without_history":len(coords - watched),
        "canonical_rows":history["canonical_rows"],
        "alias_rows":history["alias_rows"],
        "canonical_alias_overlap":history["canonical_alias_overlap"],
        "alias_only":history["alias_only"],
        "active_resumes":history["active_resumes"],
    }


def main():
    global PHASE
    PHASE="AUTH_AND_GATE"
    token,mapped=core.credential()
    core.bridge_gate()
    spellings_groups=audit.visibility.verified_groups_from_bridge()
    if len(spellings_groups)!=MAX_ALIAS_GROUPS:
        fail("GROUP_COUNT_INVALID")
    spellings=spellings_groups[0]
    PHASE="READ_WATCH_STATE_BEFORE"
    old_rows=observe_history(spellings)
    old_history=history_coordinates(old_rows,spellings)
    deadline=time.monotonic()+audit.DEADLINE_SECONDS
    reader=core.make_reader(token,deadline)
    if reader("/Users/Me").get("Id")!=mapped:
        fail("USER_SCOPE_UNVERIFIED")

    PHASE="NEXTUP_STABLE_WINDOW"
    first=audit.read_bounded_nextup(token,deadline)
    second=audit.read_bounded_nextup(token,deadline)
    if first!=second:
        fail("NEXTUP_DRIFT")
    cards=audit.adaptive.items(first,50)
    series_ids=sorted({c["SeriesId"] for c in cards})
    if not series_ids or len(series_ids)>50:
        fail("NEXTUP_WINDOW_UNQUALIFIED")

    def detail(ident):
        row=reader("/Items/"+quote(ident,safe=""))
        if row.get("Type")!="Series" or row.get("Id")!=ident:
            fail("SERIES_METADATA_IDENTITY_UNVERIFIED")
        provider=row.get("ProviderIds")
        imdb=provider.get("Imdb") if isinstance(provider,dict) else None
        return ident, imdb.lower() if isinstance(imdb,str) else None

    PHASE="SERIES_METADATA"
    with ThreadPoolExecutor(max_workers=4) as executor:
        found=list(executor.map(detail,series_ids))
    metadata=dict(found)
    PHASE="CANONICAL_SELECTOR"
    chosen=audit.candidate_from_window(cards,metadata,[spellings])
    sid=chosen["SeriesId"]
    if metadata.get(sid)!=spellings[0]:
        fail("CANONICAL_NOT_PRESENT")

    PHASE="CATALOG_DOUBLE_READ"
    catalog,pages1=audit.scan_pages(reader,sid)
    catalog2,pages2=audit.scan_pages(reader,sid)
    PHASE="REPLAY_CHECK"
    if pages1!=pages2:
        fail("CATALOG_DRIFT")
    if chosen["Id"] not in {x["Id"] for x in catalog}:
        fail("CANONICAL_ITEM_NOT_IN_CATALOG")
    PHASE="WATCH_STATE_AFTER"
    new_rows=observe_history(spellings)
    if old_rows!=new_rows:
        fail("WATCH_STATE_DRIFT")
    # Re-evaluate connected profile/graph immediately after the read.
    if audit.visibility.verified_groups_from_bridge()!=spellings_groups:
        fail("IDENTITY_GRAPH_UNAVAILABLE")
    PHASE="COMPARE_COORDINATES"
    metrics=match_catalog(catalog,old_history)
    PHASE="FINAL_GATE"
    core.bridge_gate()

    print("RC3K_AUDIT=SCOPED_HISTORY_CATALOG_COORDINATE_JOIN")
    print("RC3K_USER=MAPPED")
    print("RC3K_GRAPH_ALIAS_GROUPS_VERIFIED=1")
    print("RC3K_CATALOG_DOUBLE_READ=IDENTICAL")
    print("RC3K_WATCH_STATE_DOUBLE_READ=IDENTICAL")
    print("RC3K_CATALOG_PAGES="+str(len(pages1)))
    print("RC3K_NEXTUP_EXISTING_ITEM_IN_CATALOG=YES")
    for k in ("canonical_rows","alias_rows","canonical_alias_overlap",
              "alias_only","history_rows","catalog_rows",
              "history_in_catalog","history_missing_from_catalog",
              "catalog_without_history","active_resumes"):
        print("RC3K_"+k.upper()+"="+str(metrics[k]))
    fully_bound=(metrics["history_missing_from_catalog"]==0)
    print("RC3K_SCOPED_HISTORY_COORDINATES_ALL_BOUND="+(
        "YES" if fully_bound else "NO"
    ))
    print("RC3K_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3K_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3K_CLIENT_RESPONSE_CHANGED=NO")
    print("RC3K_FILTER_FINAL=OFF")
    print("RC3K_SQLITE_MODE=READ_ONLY")
    print("RC3K_AUTHENTICATED_HTTP=GET_ONLY")
    print("RC3K_DATABASE_WRITES=0")
    print("RC3K_PRODUCTION_CHANGES=0")
    print("RC3K_SECRETS_PRINTED=NO")
    return 0 if fully_bound else 3


if __name__=="__main__":
    status=2
    try:
        status=main()
    except Exception as err:
        reason=str(err) if isinstance(err,core.StopAudit) else ""
        reason=reason if reason in SAFE_REASONS else "UNQUALIFIED_SOURCE"
        print("RC3K_RESULT=STOP_UNQUALIFIED")
        print("RC3K_FAILURE_PHASE="+(
            PHASE if PHASE in SAFE_PHASES else "UNQUALIFIED_PHASE"
        ))
        print("RC3K_FAILURE_REASON="+reason)
        try:
            core.bridge_gate()
            print("RC3K_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3K_FILTER_ON_STOP=UNVERIFIED")
        print("RC3K_DATABASE_WRITES=0")
        print("RC3K_PRODUCTION_CHANGES=0")
        print("RC3K_SECRETS_PRINTED=NO")
    raise SystemExit(status)
