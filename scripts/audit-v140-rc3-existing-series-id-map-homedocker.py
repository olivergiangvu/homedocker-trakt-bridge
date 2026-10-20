#!/usr/bin/env python3
"""RC3-N4: existing persisted AIO hashed-SeriesId coverage, read-only.

AIO upstream issues packed a1 IDs (no persisted row) OR hashed b2 IDs
stored in GLOBAL jellyfin_id_map (not scoped to the mapped Jellyfin user).
Never reconstruct/synthesize a1 or b2, never treat table membership as
user-issued/permitted/available playback, and never call /Items or /Shows.
Only aggregate counts. No raw IDs, titles, keys, profiles or timestamps.
"""
from collections import Counter
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import time

HERE=Path(__file__).resolve().parent


def dep(filename, name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPENDENCY_MISSING")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


n2=dep("audit-v140-rc3-source-anchor-drop-homedocker.py","rc3n4_n2")
n1=dep("audit-v140-rc3-source-namespaces-homedocker.py","rc3n4_n1")
shadow=n2.shadow
identity=n2.identity

MAX_MAP_ROWS=20_000
B2_ID=re.compile(r"b2[0-9a-f]{30}\Z")
PHASE="INIT"
PHASES=frozenset({
    "INIT","GATE_USER","SOURCE_AND_ANCHOR_SNAPSHOTS",
    "ID_MAP_FIRST","ID_MAP_SECOND","AGGREGATE","FINAL_GATE",
})
REASONS=frozenset({
    "USER_SCOPE_MISMATCH","SOURCE_SNAPSHOT_DRIFT",
    "ANCHOR_SNAPSHOT_DRIFT","SINK_SCOPE_DRIFT",
    "ANCHOR_BUDGET","DROP_BUDGET","ANCHOR_COUNT_UNVERIFIED",
    "ANCHOR_ROW_INVALID","DROP_ROW_INVALID",
    "SOURCE_CANDIDATE_MISMATCH","SOURCE_RANK_INVALID",
    "ANCHOR_BUCKET_INCONSISTENT",
    "ID_MAP_ROWS_OVER_BUDGET","ID_MAP_SCHEMA_UNAVAILABLE",
    "ID_MAP_SNAPSHOT_DRIFT","ID_MAP_DATA_INVALID",
    "ID_MAP_DUPLICATE_ID","MAPPED_SERIES_SOURCE_INCONSISTENT",
})


def map_snapshot():
    """One independent read transaction; never access map with write SQL.

    The AIO jellyfin_id_map is instance-global: no (uuid,persona) columns.
    This scan must NOT be interpreted as same-user authorization.
    """
    try:
        with closing(identity.ro(identity.AIO_DB)) as db:
            db.execute("BEGIN")
            count=db.execute("SELECT COUNT(*) FROM jellyfin_id_map").fetchone()
            if count is None or type(count[0]) is not int or count[0]<0:
                raise shadow.StopAudit("ID_MAP_SCHEMA_UNAVAILABLE")
            if count[0]>MAX_MAP_ROWS:
                raise shadow.StopAudit("ID_MAP_ROWS_OVER_BUDGET")
            rows=db.execute(
                "SELECT id,payload,seen_at FROM jellyfin_id_map "
                "ORDER BY id LIMIT ?",
                (MAX_MAP_ROWS+1,),
            ).fetchall()
            if len(rows)>MAX_MAP_ROWS or len(rows)!=count[0]:
                raise shadow.StopAudit("ID_MAP_ROWS_OVER_BUDGET")
            return rows
    except shadow.StopAudit:
        raise
    except Exception:
        raise shadow.StopAudit("ID_MAP_SCHEMA_UNAVAILABLE") from None


def validate_map(rows):
    """Return exact already-stored hashed *series* descriptor IDs only.

    Other issued Item kinds (episodes, movies, views) are not correlated.
    Packed a1 series IDs will be absent even if already used by clients.
    """
    if not isinstance(rows,list) or len(rows)>MAX_MAP_ROWS:
        raise shadow.StopAudit("ID_MAP_ROWS_OVER_BUDGET")
    seen=set()
    by_base={}
    other_kind=0
    for raw_id,payload,seen_at in rows:
        if not isinstance(raw_id,str) or len(raw_id)!=32 \
                or not isinstance(payload,str) or len(payload)>4096 \
                or type(seen_at)!=int or seen_at<0:
            raise shadow.StopAudit("ID_MAP_DATA_INVALID")
        if raw_id in seen:
            raise shadow.StopAudit("ID_MAP_DUPLICATE_ID")
        seen.add(raw_id)
        if not B2_ID.fullmatch(raw_id):
            # The current pinned source persists only hashed mappings.
            # Do not classify unexpected IDs as pre-issued Series IDs.
            other_kind+=1
            continue
        try:
            obj=json.loads(payload)
        except (ValueError,TypeError):
            raise shadow.StopAudit("ID_MAP_DATA_INVALID") from None
        if not isinstance(obj,dict) or not isinstance(obj.get("k"),str):
            raise shadow.StopAudit("ID_MAP_DATA_INVALID")
        if obj["k"]!="series":
            other_kind+=1
            continue
        base=obj.get("i")
        media=obj.get("t")
        if not isinstance(base,str) or not 1<=len(base)<=512 \
                or not isinstance(media,str) or media not in (
                    "series","tv","anime","other","movie","channel"
                ):
            raise shadow.StopAudit("ID_MAP_DATA_INVALID")
        by_base.setdefault(base,set()).add(raw_id)
    return by_base,other_kind


def summarize(index,anchors,rows):
    # Replay N2's exact candidate+anchor count/scope/drop verification.
    n2.summarize(index,anchors)
    by_base,not_series=validate_map(rows)
    raw_hist={x for x,_ in index["history"]}
    raw_recent=[x for x,_ in index["recent"]]
    recent=set(raw_recent)
    hist_hits=set()
    recent_hits=set()
    for base in by_base:
        source="s|"+base
        if source in raw_hist:
            hist_hits.add(source)
        if source in recent:
            recent_hits.add(source)
    redirect_targets=set()
    for row in anchors["anchors"]:
        target=n2.match_series_key(row[1])
        if target and target!=row[0]:
            redirect_targets.add(target)
    target_hits={x for x in redirect_targets if x[2:] in by_base}
    counts={
        "GLOBAL_ID_MAP_TOTAL_ROWS":len(rows),
        "GLOBAL_ID_MAP_NON_SERIES_OR_UNEXPECTED_KIND_ROWS":not_series,
        "GLOBAL_HASHED_SERIES_DESCRIPTOR_BASES":len(by_base),
        "GLOBAL_HASHED_SERIES_DESCRIPTOR_IDS":sum(
            len(x) for x in by_base.values()
        ),
        "GLOBAL_HASHED_SERIES_BASES_WITH_MULTIPLE_IDS":sum(
            len(x)>1 for x in by_base.values()
        ),
        "HISTORY_SOURCE_KEYS_WITH_EXISTING_HASHED_SERIES_MAP":len(hist_hits),
        "RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP":len(recent_hits),
        "TOP_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP":sum(
            x in recent_hits for x in raw_recent[:200]
        ),
        "BEYOND_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP":sum(
            x in recent_hits for x in raw_recent[200:]
        ),
        "REDIRECT_DISTINCT_TARGET_SOURCE_KEYS":len(redirect_targets),
        "REDIRECT_TARGETS_WITH_EXISTING_HASHED_SERIES_MAP":len(target_hits),
        "REDIRECT_TARGETS_OUTSIDE_HISTORY_WITH_MAP":sum(
            x not in raw_hist for x in target_hits
        ),
    }
    if (
        counts["GLOBAL_HASHED_SERIES_DESCRIPTOR_IDS"]+not_series!=len(rows)
        or counts["RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"]!=(
            counts["TOP_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"]+
            counts["BEYOND_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"]
        )
        or counts["HISTORY_SOURCE_KEYS_WITH_EXISTING_HASHED_SERIES_MAP"]<
            counts["RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"]
    ):
        raise shadow.StopAudit("MAPPED_SERIES_SOURCE_INCONSISTENT")
    # Exact syntax only. No assumption of same show between namespaces.
    for name in ("IMDB_CANONICAL","TVDB","TMDB","KITSU",
                 "OTHER_OR_OPAQUE"):
        counts["RECENT_"+name+"_WITH_GLOBAL_HASHED_MAP"]=sum(
            x in recent_hits and n1.kind(x)==name for x in raw_recent
        )
    return counts


def main():
    global PHASE
    PHASE="GATE_USER"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    get=shadow.make_reader(token,time.monotonic()+12)
    if get("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("USER_SCOPE_MISMATCH")
    PHASE="SOURCE_AND_ANCHOR_SNAPSHOTS"
    index,anchors=n2.source_gate_and_anchors()
    PHASE="ID_MAP_FIRST"
    first=map_snapshot()
    PHASE="ID_MAP_SECOND"
    second=map_snapshot()
    if first!=second:
        raise shadow.StopAudit("ID_MAP_SNAPSHOT_DRIFT")
    PHASE="AGGREGATE"
    metrics=summarize(index,anchors,first)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N4_AUDIT=PERSISTED_HASHED_SERIES_ID_MAP_COVERAGE")
    print("RC3N4_USER=MAPPED")
    print("RC3N4_SOURCE_ANCHOR_DOUBLE_READ=IDENTICAL")
    print("RC3N4_GLOBAL_ID_MAP_DOUBLE_READ=IDENTICAL")
    for key,val in metrics.items():
        print("RC3N4_"+key+"="+str(val))
    print("RC3N4_GLOBAL_ID_MAP_IS_NOT_USER_SCOPED=YES")
    print("RC3N4_PACKED_A1_IDS_NOT_IN_THIS_TABLE=YES")
    print("RC3N4_ID_MAP_MEMBERSHIP_NOT_USER_ISSUANCE=YES")
    print("RC3N4_SOURCE_TO_JELLYFIN_ID_RESOLVED=NO")
    print("RC3N4_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3N4_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N4_JELLYFIN_IDS_CREATED=0")
    print("RC3N4_CATALOG_ENDPOINT_CALLS=0")
    print("RC3N4_GATEWAY_FILTER=OFF")
    print("RC3N4_SQLITE_MODE=READ_ONLY")
    print("RC3N4_PRODUCTION_CHANGES=0")
    print("RC3N4_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        reason=str(error) if isinstance(error,shadow.StopAudit) else ""
        reason=reason if reason in REASONS else "UNQUALIFIED_SOURCE_OR_RUNTIME"
        print("RC3N4_RESULT=STOP_UNQUALIFIED")
        print("RC3N4_FAILURE_PHASE="+(
            PHASE if PHASE in PHASES else "UNKNOWN"))
        print("RC3N4_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N4_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N4_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N4_PRODUCTION_CHANGES=0")
        print("RC3N4_SECRETS_PRINTED=NO")
        raise SystemExit(2)
