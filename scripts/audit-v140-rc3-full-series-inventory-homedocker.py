#!/usr/bin/env python3
"""RC3-M: discover complete raw series-key inventory in the one mapped sink.

Exhausts two SOURCE watch_state GROUP BY queries up to hard 10000 key
budget, independent of AIO /Shows/NextUp's 50 visible cards and up to 200
considered candidates. Honors exact uuid+persona on BOTH statements,
profile-bound identity graph, and unchanged double-read source snapshot.
This is a local, no-write audit, NOT a global Jellyfin NextUp/playlist.
No title, IMDb, Trakt ID, SeriesId, ranks, timestamps, episode coords,
bearer tokens, SQL rows or HTTP URLs are printed.
"""
from contextlib import closing
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import time

HERE=Path(__file__).resolve().parent


def imported(filename,name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPENDENCY_UNAVAILABLE")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity=imported("audit-v140-rc3-alias-timeline-coverage-homedocker.py",
                  "rc3m_identity")
shadow=imported("audit-v140-rc3-nextup-shadow-homedocker.py","rc3m_shadow")
MAX_KEYS=10_000
GRAPH_GROUP_MAX=200
RANK_SOURCE_SCAN_MAX=200  # Upstream route starts <= 200 candidates at Limit50.
FAIL_PHASES=frozenset({
    "INIT","GATE_AND_MAPPED_USER","FIRST_SNAPSHOT",
    "SECOND_SNAPSHOT","AGGREGATE","FINAL_GATE",
})
FAIL_CODES=frozenset({
    "PROFILE_NOT_UNIQUE","WATCH_SINK_NOT_UNIQUE",
    "WATCH_SINK_SCOPE_INVALID","GRAPH_CACHE_UNAVAILABLE",
    "GRAPH_UNTRUSTED","GRAPH_ALIAS_GROUP_LIMIT",
    "SOURCE_SERIES_BUDGET","SOURCE_CANDIDATE_BUDGET",
    "SOURCE_HISTORY_KEY_INVALID","SOURCE_HISTORY_COUNT_INVALID",
    "SOURCE_RECENT_KEY_INVALID","SOURCE_RECENT_SORT_INVALID",
    "SOURCE_RECENT_NOT_IN_HISTORY","SOURCE_RECENT_ORDER_INVALID",
    "SOURCE_SNAPSHOT_DRIFT","MAPPED_JELLYFIN_USER_MISMATCH",
    "AUDIT_SINK_UNVERIFIED",
})
PHASE="INIT"


def stop(code):
    raise shadow.StopAudit(code)


def snapshot():
    """Read both files through root-only, no-symlink SQLite mode=ro/query_only.

    One read transaction in each DB; profile, sink, graph, raw history and
    candidate keys are captured with DB-local snapshot consistency. The
    entire operation is repeated to detect changes between snapshots.
    """
    with closing(identity.ro(identity.AIO_DB)) as aio, \
         closing(identity.ro(identity.BRIDGE_DB)) as bridge:
        aio.execute("BEGIN")
        bridge.execute("BEGIN")
        ids=bridge.execute(
            "SELECT id FROM profiles WHERE access_token_enc IS NOT NULL"
        ).fetchall()
        if len(ids)!=1 or not isinstance(ids[0][0],str):
            stop("PROFILE_NOT_UNIQUE")
        scopes=aio.execute(
            "SELECT uuid, persona FROM watch_sinks "
            "WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL",
            (identity.SINK_NAME,identity.SINK_INSTANCE)
        ).fetchall()
        if len(scopes)!=1 or not all(
            isinstance(x,str) and bool(x) for x in scopes[0]
        ):
            stop("WATCH_SINK_NOT_UNIQUE")
        key=(
            "identity-graph:v1:"+ids[0][0],
        )
        graph_row=bridge.execute(
            "SELECT payload_json FROM media_cache "
            "WHERE cache_key=? AND expires_at > "
            "CAST(strftime('%s','now') AS INTEGER)",key
        ).fetchone()
        if not graph_row:
            stop("GRAPH_CACHE_UNAVAILABLE")
        try:
            graph=json.loads(graph_row[0])
            shows,groups=identity.verified_alias_groups(graph)
        except (ValueError,TypeError,identity.AuditStop):
            stop("GRAPH_UNTRUSTED")
        if len(groups)>GRAPH_GROUP_MAX:
            stop("GRAPH_ALIAS_GROUP_LIMIT")
        scope=scopes[0]
        histories=aio.execute(
            """SELECT series_key, COUNT(*)
               FROM watch_state
               WHERE uuid=? AND persona=? AND series_key IS NOT NULL
                 AND episode IS NOT NULL
               GROUP BY series_key ORDER BY series_key LIMIT ?""",
            (*scope,MAX_KEYS+1),
        ).fetchall()
        if len(histories)>MAX_KEYS:
            stop("SOURCE_SERIES_BUDGET")
        candidates=aio.execute(
            """SELECT series_key, MAX(sort_at) AS latest
               FROM watch_state
               WHERE uuid=? AND persona=? AND series_key IS NOT NULL
                 AND episode IS NOT NULL
                 AND (played=1 OR position_ms>0)
               GROUP BY series_key
               ORDER BY latest DESC, series_key LIMIT ?""",
            (*scope,MAX_KEYS+1),
        ).fetchall()
        if len(candidates)>MAX_KEYS:
            stop("SOURCE_CANDIDATE_BUDGET")
        # Never print source keys/profile ids or series names.
        return {
            "profile":ids[0][0],"scope":tuple(scope),
            "graph_json":graph_row[0],
            "graph_show_count":shows,
            "groups":groups,
            "history":histories,
            "recent":candidates,
        }


def aggregate(snapshot_data):
    hist={}
    for key,count in snapshot_data["history"]:
        if not isinstance(key,str) or not key or len(key)>512:
            stop("SOURCE_HISTORY_KEY_INVALID")
        if type(count)!=int or count<=0 or key in hist:
            stop("SOURCE_HISTORY_COUNT_INVALID")
        hist[key]=count
    ranked={}
    previous=float("inf")
    prevkey=""
    for index,(key,at) in enumerate(snapshot_data["recent"],1):
        if not isinstance(key,str) or not key or len(key)>512:
            stop("SOURCE_RECENT_KEY_INVALID")
        if type(at) not in (int,float) or not math.isfinite(at) or at<0:
            stop("SOURCE_RECENT_SORT_INVALID")
        if key not in hist or key in ranked:
            stop("SOURCE_RECENT_NOT_IN_HISTORY")
        if at>previous or (at==previous and key<=prevkey):
            stop("SOURCE_RECENT_ORDER_INVALID")
        ranked[key]=index
        previous=at
        prevkey=key
    groups=snapshot_data["groups"]
    import re
    imdb=re.compile(r"s\|tt[0-9]{7,12}\Z")
    all_keys=set(hist)
    valid={x for x in all_keys if imdb.fullmatch(x)}
    candidate_keys=set(ranked)
    mapped=set()
    collapse=0
    groups_present=0
    groups_both_candidates=0
    for spellings in groups:
        keys=["s|"+s for s in spellings]
        if any(x in mapped for x in keys):
            stop("GRAPH_UNTRUSTED")
        mapped.update(keys)
        existing=sum(x in all_keys for x in keys)
        collapse+=max(existing-1,0)
        if existing:
            groups_present+=1
        if sum(x in ranked for x in keys)>=2:
            groups_both_candidates+=1
    count=len(ranked)
    return {
        "full_history_series_keys":len(hist),
        "recent_series_candidate_keys":count,
        "history_only_series_keys":len(hist)-count,
        "source_candidate_rank_1_to_50":min(count,50),
        "source_candidate_rank_51_to_200":max(0,min(count,200)-50),
        "source_candidate_rank_beyond_200":max(0,count-200),
        "history_keys_with_imdb_syntax":len(valid),
        "history_keys_other_source_syntax":len(all_keys-valid),
        "recent_candidate_keys_with_imdb_syntax":len(valid&candidate_keys),
        "verified_graph_shows":snapshot_data["graph_show_count"],
        "verified_alias_groups":len(groups),
        "verified_alias_groups_present_in_history":groups_present,
        "verified_groups_with_both_raw_candidate_spellings":groups_both_candidates,
        "verified_alias_spelling_reduction_hint":collapse,
        "inventory_hint_count":len(hist)-collapse,
    }


def main():
    global PHASE
    PHASE="GATE_AND_MAPPED_USER"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    reader=shadow.make_reader(token,time.monotonic()+12)
    response=reader("/Users/Me")
    if response.get("Id")!=mapped:
        stop("MAPPED_JELLYFIN_USER_MISMATCH")
    PHASE="FIRST_SNAPSHOT"
    first=snapshot()
    PHASE="SECOND_SNAPSHOT"
    second=snapshot()
    if first!=second:
        stop("SOURCE_SNAPSHOT_DRIFT")
    PHASE="AGGREGATE"
    metrics=aggregate(first)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3M_AUDIT=FULL_SCOPED_WATCH_STATE_SERIES_DISCOVERY")
    print("RC3M_USER=MAPPED")
    print("RC3M_SOURCE_SNAPSHOT_DOUBLE_READ=IDENTICAL")
    for k,v in metrics.items():
        print("RC3M_"+k.upper()+"="+str(v))
    print("RC3M_SOURCE_RECENT_QUERY=EXHAUSTIVE_UNDER_10000_KEY_BUDGET")
    print("RC3M_SOURCE_RANK_NOT_NEXTUP_CARD=YES")
    print("RC3M_MATCH_KEY_DROP_RESOLUTION_PROVEN=NO")
    print("RC3M_ALL_PLAYABLE_CATALOG_RESOLVED=NO")
    print("RC3M_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3M_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3M_GATEWAY_FILTER=OFF")
    print("RC3M_SQLITE=MODE_RO_QUERY_ONLY")
    print("RC3M_HTTP=ONE_AUTHENTICATED_LOCAL_USER_GET")
    print("RC3M_SOURCE_HISTORY_WRITES=0")
    print("RC3M_CLIENT_ROUTING_CHANGED=NO")
    print("RC3M_PRODUCTION_CHANGES=0")
    print("RC3M_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as err:
        msg=str(err) if isinstance(err,shadow.StopAudit) else ""
        msg=msg if msg in FAIL_CODES else "UNQUALIFIED_LOCAL_OR_SOURCE"
        print("RC3M_RESULT=STOP_UNQUALIFIED")
        print("RC3M_FAILURE_PHASE="+(
            PHASE if PHASE in FAIL_PHASES else "UNKNOWN"
        ))
        print("RC3M_FAILURE_REASON="+msg)
        try:
            shadow.bridge_gate()
            print("RC3M_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3M_FILTER_ON_STOP=UNVERIFIED")
        print("RC3M_PRODUCTION_CHANGES=0")
        print("RC3M_SECRETS_PRINTED=NO")
        raise SystemExit(2)
