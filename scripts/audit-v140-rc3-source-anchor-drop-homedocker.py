#!/usr/bin/env python3
"""RC3-N2: classify scoped AIO recentSeries anchors and potential drop hints.

Never infer a playable Jellyfin SeriesId from source series_key. Upstream
encodeItemId may need a pre-existing hashed-ID mapping, so this is SQLite
OBSERVATION only, not catalog resolution or NextUp synthesis.

One mapped-user local authenticated GET /Users/Me; source/Bridge DBs opened
via RC3-M fixed-path sqlite mode=ro PRAGMA query_only. Independent pairs of
complete index and anchor/drop reads must replay byte-for-byte identically.
No raw source keys, match keys, metadata, series/episode numbers, titles,
profile IDs, URLs or timestamps printed. Zero Trakt/catalog/PlaybackInfo GETs.
"""
import importlib.util
from contextlib import closing
import math
from pathlib import Path
import re
import time

HERE=Path(__file__).resolve().parent


def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    if spec is None or spec.loader is None:
        raise RuntimeError("LOCAL_DEPENDENCY_MISSING")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


inventory=load("rc3n2_inventory","audit-v140-rc3-full-series-inventory-homedocker.py")
identity=inventory.identity
shadow=inventory.shadow
MAX_ANCHORS=10_000
MAX_DROP_ROWS=10_000
PHASE="INIT"
PHASES=frozenset({"INIT","MAP_USER","FIRST_SOURCE","FIRST_ANCHORS",
                  "SECOND_SOURCE","SECOND_ANCHORS","VERIFY_SCOPE",
                  "AGGREGATE","FINAL_GATE"})
REASONS=frozenset({
    "USER_SCOPE_MISMATCH","SOURCE_SNAPSHOT_DRIFT","ANCHOR_SNAPSHOT_DRIFT",
    "SINK_SCOPE_DRIFT","ANCHOR_BUDGET","DROP_BUDGET",
    "ANCHOR_COUNT_UNVERIFIED","ANCHOR_ROW_INVALID",
    "DROP_ROW_INVALID","SOURCE_CANDIDATE_MISMATCH",
    "SOURCE_RANK_INVALID","DATA_SOURCE_UNAVAILABLE",
    "ANCHOR_BUCKET_INCONSISTENT",
})
MATCH_EPISODE=re.compile(
    r"^e\|(?P<base>(?:tt[0-9]+|imdb[:-]?[0-9]+|tvdb[:-]?[0-9]+|"
    r"tmdb[:-]?[0-9]+)):(?P<season>[0-9]+):(?P<episode>[0-9]+)$"
    r"|^e\|(?P<anime>(?:mal|kitsu|anilist|anidb|anidb_id|anidbid)"
    r"[:-]?[0-9]+):(?P<absolute>[0-9]+)$"
)
# Other upstream recognized providers are conservatively classified as
# unverified, never force a redirect/identity match from partial parsing.


def match_series_key(value):
    """Return only a *syntactic* source series key; never Jellyfin IDs."""
    if not isinstance(value,str) or len(value)>512:
        return None
    parsed=MATCH_EPISODE.fullmatch(value)
    if not parsed:
        return None
    base=parsed.group("base") or parsed.group("anime")
    return "s|"+base if base else None


def anchor_snapshot(scope):
    """One scoped transaction; upstream-like per-key latest watched anchor.

    An extra item_key lexical tie-break is used only to make this audit
    deterministic; it does NOT prove the upstream source's chosen anchor
    when sort_at, season and episode all tie.
    """
    with closing(identity.ro(identity.AIO_DB)) as aio:
        aio.execute("BEGIN")
        anchor_rows=aio.execute(
            """WITH latest AS (
                 SELECT series_key,MAX(sort_at) AS at
                 FROM watch_state
                 WHERE uuid=? AND persona=? AND series_key IS NOT NULL
                   AND episode IS NOT NULL
                   AND (played=1 OR position_ms>0)
                 GROUP BY series_key
               ),
               ranked AS (
                 SELECT w.series_key,w.match_key,w.media_type,w.sort_at,
                        w.season,w.episode,w.item_key,
                        ROW_NUMBER() OVER (
                           PARTITION BY w.series_key
                           ORDER BY w.season DESC,w.episode DESC,w.item_key DESC
                        ) AS rn
                 FROM latest l JOIN watch_state w
                   ON w.uuid=? AND w.persona=?
                  AND w.series_key=l.series_key AND w.sort_at=l.at
                 WHERE w.episode IS NOT NULL
                   AND (w.played=1 OR w.position_ms>0)
               )
               SELECT series_key,match_key,media_type,sort_at,
                      season,episode,item_key
               FROM ranked WHERE rn=1
               ORDER BY sort_at DESC,series_key LIMIT ?""",
            (*scope,*scope,MAX_ANCHORS+1)
        ).fetchall()
        if len(anchor_rows)>MAX_ANCHORS:
            raise shadow.StopAudit("ANCHOR_BUDGET")
        drops=aio.execute(
            "SELECT item_key,match_key,dropped_at FROM watch_state "
            "WHERE uuid=? AND persona=? AND dropped=1 LIMIT ?",
            (*scope,MAX_DROP_ROWS+1)
        ).fetchall()
        if len(drops)>MAX_DROP_ROWS:
            raise shadow.StopAudit("DROP_BUDGET")
        return {"anchors":anchor_rows,"drops":drops}


def source_gate_and_anchors():
    global PHASE
    PHASE="FIRST_SOURCE"
    first_index=inventory.snapshot()
    PHASE="FIRST_ANCHORS"
    first_anchors=anchor_snapshot(first_index["scope"])
    PHASE="SECOND_SOURCE"
    second_index=inventory.snapshot()
    PHASE="SECOND_ANCHORS"
    second_anchors=anchor_snapshot(second_index["scope"])
    PHASE="VERIFY_SCOPE"
    if first_index!=second_index:
        raise shadow.StopAudit("SOURCE_SNAPSHOT_DRIFT")
    if first_index["scope"]!=second_index["scope"]:
        raise shadow.StopAudit("SINK_SCOPE_DRIFT")
    if first_anchors!=second_anchors:
        raise shadow.StopAudit("ANCHOR_SNAPSHOT_DRIFT")
    # Both source index snapshots are exhaustive, not first 50 NextUp.
    inventory.aggregate(first_index)
    return first_index,first_anchors


def valid_anchor(row):
    key,match,media,at,season,episode,item=row
    return isinstance(key,str) and bool(key) and len(key)<=512 \
        and (match is None or isinstance(match,str)) \
        and isinstance(media,str) and len(media)<=64 \
        and type(at) in (int,float) and math.isfinite(at) and at>=0 \
        and type(season) in (int,type(None)) \
        and type(episode) is int and episode>=1 \
        and isinstance(item,str) and len(item)<=512


def summarize(index, extra):
    source_keys=[key for key,_ in index["recent"]]
    anchors=extra["anchors"]
    if len(anchors)!=len(source_keys):
        raise shadow.StopAudit("ANCHOR_COUNT_UNVERIFIED")
    lookup={}
    for row in anchors:
        if len(row)!=7 or not valid_anchor(row):
            raise shadow.StopAudit("ANCHOR_ROW_INVALID")
        key=row[0]
        if key in lookup:
            raise shadow.StopAudit("ANCHOR_COUNT_UNVERIFIED")
        lookup[key]=row
    if set(lookup)!=set(source_keys):
        raise shadow.StopAudit("SOURCE_CANDIDATE_MISMATCH")
    # SortAt is already separately verified in RC3-M; every anchored maximum
    # must match the corresponding ranked source grouped MAX(sort_at).
    for key,sort in index["recent"]:
        if lookup[key][3]!=sort:
            raise shadow.StopAudit("SOURCE_RANK_INVALID")
    dropped_at={}
    for item,matched,at in extra["drops"]:
        if not isinstance(item,str) or not item or len(item)>512 \
            or (matched is not None and not isinstance(matched,str)):
            raise shadow.StopAudit("DROP_ROW_INVALID")
        # Upstream Number(dropped_at ?? 0) permits NULL or a numeric
        # SQLite string. Preserve that interpretation, but never accept
        # malformed/nonfinite numbers or send their values to stdout.
        if at is None:
            at=0
        elif isinstance(at,str) and 0<len(at)<=32:
            try:
                at=float(at)
            except ValueError:
                raise shadow.StopAudit("DROP_ROW_INVALID") from None
        if type(at) not in (int,float) or not math.isfinite(at) or at<0:
            raise shadow.StopAudit("DROP_ROW_INVALID")
        for key in (item,matched):
            if key:
                dropped_at[key]=max(dropped_at.get(key,0),at)
    result={}
    for band,start,end in (
        ("TOP_200",0,200),("BEYOND_200",200,len(source_keys))
    ):
        items=source_keys[start:end]
        result[band]={
            "SOURCE_CANDIDATE_KEYS":len(items),
            "ANCHOR_HAS_MATCH_KEY":0,
            "ANCHOR_PARSEABLE_EPISODE_MATCH":0,
            "ANCHOR_OTHER_OR_INVALID_MATCH":0,
            "ANCHOR_NO_MATCH_KEY":0,
            "SOURCE_KEY_DIFFERS_FROM_PARSED_MATCH":0,
            "EFFECTIVE_DROP_HINT":0,
            "MATCH_DESTINATION_HAS_SOURCE_CANDIDATE":0,
        }
        stats=result[band]
        for key in items:
            row=lookup[key]
            match=row[1]
            target=match_series_key(match)
            if not match:
                stats["ANCHOR_NO_MATCH_KEY"]+=1
            else:
                stats["ANCHOR_HAS_MATCH_KEY"]+=1
                if target:
                    stats["ANCHOR_PARSEABLE_EPISODE_MATCH"]+=1
                else:
                    stats["ANCHOR_OTHER_OR_INVALID_MATCH"]+=1
            if target and target!=key:
                stats["SOURCE_KEY_DIFFERS_FROM_PARSED_MATCH"]+=1
            if target and target in lookup:
                stats["MATCH_DESTINATION_HAS_SOURCE_CANDIDATE"]+=1
            if (dropped_at.get(key,0)>row[3] or
                    (target and dropped_at.get(target,0)>row[3])):
                stats["EFFECTIVE_DROP_HINT"]+=1
        if sum(stats[x] for x in (
            "ANCHOR_NO_MATCH_KEY","ANCHOR_PARSEABLE_EPISODE_MATCH",
            "ANCHOR_OTHER_OR_INVALID_MATCH"))!=len(items):
            raise shadow.StopAudit("ANCHOR_BUCKET_INCONSISTENT")
    return result,len(extra["drops"])


def main():
    global PHASE
    PHASE="MAP_USER"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    reader=shadow.make_reader(token,time.monotonic()+12)
    if reader("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("USER_SCOPE_MISMATCH")
    PHASE="FIRST_SOURCE"
    # This function performs two complete verified rounds in fixed order.
    index,extra=source_gate_and_anchors()
    PHASE="AGGREGATE"
    summary,drop_count=summarize(index,extra)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N2_AUDIT=RAW_SOURCE_ANCHOR_AND_DROP_FEASIBILITY")
    print("RC3N2_USER=MAPPED")
    print("RC3N2_SOURCE_DOUBLE_READ=IDENTICAL")
    print("RC3N2_ANCHOR_DOUBLE_READ=IDENTICAL")
    print("RC3N2_SCOPED_DROPPED_ROWS="+str(drop_count))
    for band,stats in summary.items():
        for metric,n in stats.items():
            print("RC3N2_"+band+"_"+metric+"="+str(n))
    print("RC3N2_MATCH_KEY_SYNTAX_NOT_CANONICAL_PROOF=YES")
    print("RC3N2_DROP_HINT_NOT_FULL_UPSTREAM_SIMULATION=YES")
    print("RC3N2_JELLYFIN_ID_GENERATION=NONE")
    print("RC3N2_CATALOG_ENDPOINT_CALLS=0")
    print("RC3N2_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N2_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3N2_FILTER_FINAL=OFF")
    print("RC3N2_MODE=READ_ONLY_SQLITE_AND_SINGLE_LOCAL_USER_GET")
    print("RC3N2_PRODUCTION_CHANGES=0")
    print("RC3N2_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        reason=str(error) if isinstance(error,shadow.StopAudit) else ""
        reason=reason if reason in REASONS else "UNQUALIFIED"
        print("RC3N2_RESULT=STOP")
        print("RC3N2_FAILURE_PHASE="+(
            PHASE if PHASE in PHASES else "UNKNOWN"))
        print("RC3N2_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N2_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N2_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N2_PRODUCTION_CHANGES=0")
        print("RC3N2_SECRETS_PRINTED=NO")
        raise SystemExit(2)
