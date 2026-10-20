#!/usr/bin/env python3
"""RC3-N6: source anchor -> effective Series descriptor provenance.

This is a *source descriptor modeling* audit, not an ID issuer. Reproduces
the pinned AIO seriesIdOf(base_id, video_id, media_type) logic conservatively.
NO 32-hex packed/hashed Jellyfin ID calculation, no catalog GET fanout,
no endpoint calls beyond same-user local /Users/Me, no writes. All source
values remain private; output comprises aggregate counters and fixed codes.

Source/anchors from strict RC3-N2 two stable exact-sink read-only snapshots.
An additional bounded two-pass SQLite transaction reads ONLY the already
selected anchor item_keys (batched, one SELECT per <=350 keys). Each fetched
row must agree with the N2 snapshot's exact item_key/series_key/season/
episode/sort_at/media_type or the audit FAILS CLOSED. It cannot prove
which ID was actually issued to a client or that streams are playable.
"""
from contextlib import closing
from pathlib import Path
import importlib.util
import re
import time

HERE=Path(__file__).resolve().parent


def load(filename,name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("SOURCE_MODULE_NOT_AVAILABLE")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


n5=load("audit-v140-rc3-source-a1-packability-homedocker.py","rc3n6_n5")
n2=n5.n2
shadow=n2.shadow
identity=n2.identity
MAX_ANCHORS=10_000
BATCH=350
PHASE="START"
PHASES=frozenset({"START","MAP_USER","SOURCE_DOUBLE_READ","DESCRIPTOR_FIRST",
                  "DESCRIPTOR_SECOND","COMPARE_SOURCE","FINAL_GATE"})
REASONS=frozenset({
    "USER_SCOPE_MISMATCH","SOURCE_SNAPSHOT_DRIFT","ANCHOR_SNAPSHOT_DRIFT",
    "SINK_SCOPE_DRIFT","ANCHOR_BUDGET","DROP_BUDGET",
    "ANCHOR_COUNT_UNVERIFIED","ANCHOR_ROW_INVALID","DROP_ROW_INVALID",
    "SOURCE_CANDIDATE_MISMATCH","SOURCE_RANK_INVALID",
    "ANCHOR_BUCKET_INCONSISTENT","ANCHOR_ITEM_KEY_NOT_UNIQUE",
    "DESCRIPTOR_SNAPSHOT_DRIFT","DESCRIPTOR_ROW_COUNT_MISMATCH",
    "DESCRIPTOR_ROW_INVALID","DESCRIPTOR_ANCHOR_MISMATCH",
    "DESCRIPTOR_VALUE_TOO_LARGE","DESCRIPTOR_MODEL_INVALID",
    "DESCRIPTOR_BUDGET_EXCEEDED",
})
# Exactly the pinned upstream episode-bearing families from IdParser; the
# parser also supports non-episode anime providers, which cannot trigger the
# seriesIdOf() correction because parsed.episode is absent.
EPISODES=(
    re.compile(r"^(?P<series>(?:tt|imdb)[:-]?[0-9]+):(?P<s>[0-9]+):(?P<e>[0-9]+)$"),
    re.compile(r"^(?P<series>(?:tvdb|tmdb)[:-]?[0-9]+):(?P<s>[0-9]+):(?P<e>[0-9]+)$"),
    re.compile(r"^(?P<series>(?:mal|kitsu|anilist|anidb|anidb_id|anidbid)[:-]?[0-9]+):(?P<e>[0-9]+)$"),
)
BASE_ID=(
    re.compile(r"^(?:tt|imdb)[:-]?[0-9]+$"),
    re.compile(r"^(?:tvdb|tmdb|mal|kitsu|anilist|anidb|anidb_id|anidbid)[:-]?[0-9]+$"),
    re.compile(r"^(?:animeplanet|ap|acd|anisearch|simkl)[:-]?[0-9]+$"),
    re.compile(r"^(?:notifymoe|nm)[:-]?[a-zA-Z0-9]+$"),
)


def parsed_base(value):
    return isinstance(value,str) and any(p.fullmatch(value) for p in BASE_ID)


def effective_series_base(base,video,media):
    """Mirror seriesIdOf token-boundary check, WITHOUT creating a Jellyfin ID.

    Never infer a series from unknown parser/video input. If base is itself
    parseable, upstream always keeps it regardless of video.
    """
    if not isinstance(base,str) or not base or len(base)>512 \
            or not isinstance(media,str) or len(media)>64 \
            or video is not None and (
                not isinstance(video,str) or len(video)>512
            ):
        raise shadow.StopAudit("DESCRIPTOR_ROW_INVALID")
    if not video or parsed_base(base):
        return base,"RAW_BASE"
    for p in EPISODES:
        match=p.fullmatch(video)
        if not match:
            continue
        suffix=(":"+match.group("s") if match.groupdict().get("s") else "")+":"+match.group("e")
        if not video.endswith(suffix):
            return base,"RAW_BASE"
        series=video[:-len(suffix)]
        # Exact upstream containsToken() semantics (alphanumeric boundaries).
        at=base.find(series)
        while at!=-1:
            before=base[at-1] if at else ""
            after=base[at+len(series)] if at+len(series)<len(base) else ""
            if (not before or not before.isascii() or not before.isalnum()) \
                    and (not after or not after.isascii() or not after.isalnum()):
                return series,"VIDEO_TOKEN_DERIVED"
            at=base.find(series,at+1)
        return base,"RAW_BASE"
    return base,"RAW_BASE"


def descriptor_snapshot(scope,item_keys):
    if len(item_keys)>MAX_ANCHORS or len(item_keys)!=len(set(item_keys)):
        raise shadow.StopAudit("DESCRIPTOR_BUDGET_EXCEEDED")
    if not all(isinstance(x,str) and 0<len(x)<=512 for x in item_keys):
        raise shadow.StopAudit("DESCRIPTOR_ROW_INVALID")
    rows=[]
    with closing(identity.ro(identity.AIO_DB)) as db:
        db.execute("BEGIN")
        for i in range(0,len(item_keys),BATCH):
            batch=item_keys[i:i+BATCH]
            markers=",".join("?" for _ in batch)
            rows.extend(db.execute(
                "SELECT item_key,base_id,video_id,media_type,series_key,"
                "sort_at,season,episode FROM watch_state "
                "WHERE uuid=? AND persona=? AND item_key IN ("+markers+") "
                "ORDER BY item_key",
                (*scope,*batch),
            ).fetchall())
    if len(rows)!=len(item_keys):
        raise shadow.StopAudit("DESCRIPTOR_ROW_COUNT_MISMATCH")
    return rows


def classify(index,anchor_data,source_rows):
    n2.summarize(index,anchor_data)
    keys=[key for key,_ in index["recent"]]
    anchors={row[0]:row for row in anchor_data["anchors"]}
    if len(anchors)!=len(keys):
        raise shadow.StopAudit("ANCHOR_COUNT_UNVERIFIED")
    chosen_items=[anchors[x][6] for x in keys]
    if len(chosen_items)!=len(set(chosen_items)):
        raise shadow.StopAudit("ANCHOR_ITEM_KEY_NOT_UNIQUE")
    fetched={}
    for row in source_rows:
        if len(row)!=8 or not isinstance(row[0],str) or row[0] in fetched:
            raise shadow.StopAudit("DESCRIPTOR_ROW_INVALID")
        fetched[row[0]]=row
    if len(fetched)!=len(chosen_items) or set(fetched)!=set(chosen_items):
        raise shadow.StopAudit("DESCRIPTOR_ROW_COUNT_MISMATCH")
    out={}
    for name,subset in (("TOP_200",keys[:200]),("BEYOND_200",keys[200:])):
        stats={
            "RAW_CANDIDATE_KEYS":len(subset),
            "SOURCE_BASE_EQUALS_SERIES_KEY":0,
            "SOURCE_BASE_DIFFERS_FROM_SERIES_KEY":0,
            "ROUTED_BASE_EQUALS_SERIES_KEY":0,
            "ROUTED_BASE_DIFFERS_FROM_SERIES_KEY":0,
            "VIDEO_TOKEN_DERIVED_BASE":0,
            "ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT":0,
            "ROUTED_BASE_UNPACKABLE_OR_UNVERIFIED":0,
            "SYNTAX_REDIRECT_EQUALS_ROUTED_BASE":0,
            "SYNTAX_REDIRECT_DIFFERS_FROM_ROUTED_BASE":0,
        }
        for source in subset:
            anchor=anchors[source]
            item,base,video,media,series_key,at,season,episode=fetched[anchor[6]]
            if any((
                item!=anchor[6],media!=anchor[2],series_key!=source,
                at!=anchor[3],season!=anchor[4],episode!=anchor[5]
            )):
                raise shadow.StopAudit("DESCRIPTOR_ANCHOR_MISMATCH")
            if not isinstance(base,str) or len(base)>512 \
                    or video is not None and (
                        not isinstance(video,str) or len(video)>512
                    ):
                raise shadow.StopAudit("DESCRIPTOR_VALUE_TOO_LARGE")
            routed,how=effective_series_base(base,video,media)
            raw=source[2:]
            if base==raw:
                stats["SOURCE_BASE_EQUALS_SERIES_KEY"]+=1
            else:
                stats["SOURCE_BASE_DIFFERS_FROM_SERIES_KEY"]+=1
            if routed==raw:
                stats["ROUTED_BASE_EQUALS_SERIES_KEY"]+=1
            else:
                stats["ROUTED_BASE_DIFFERS_FROM_SERIES_KEY"]+=1
            if how=="VIDEO_TOKEN_DERIVED":
                stats["VIDEO_TOKEN_DERIVED_BASE"]+=1
            if n5.packability_hint("s|"+routed,media)=="CANONICAL_PACKABLE_SYNTAX":
                stats["ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT"]+=1
            else:
                stats["ROUTED_BASE_UNPACKABLE_OR_UNVERIFIED"]+=1
            target=n2.match_series_key(anchor[1])
            if target and target!=source:
                if target[2:]==routed:
                    stats["SYNTAX_REDIRECT_EQUALS_ROUTED_BASE"]+=1
                else:
                    stats["SYNTAX_REDIRECT_DIFFERS_FROM_ROUTED_BASE"]+=1
        for a,b in (
            ("SOURCE_BASE_EQUALS_SERIES_KEY","SOURCE_BASE_DIFFERS_FROM_SERIES_KEY"),
            ("ROUTED_BASE_EQUALS_SERIES_KEY","ROUTED_BASE_DIFFERS_FROM_SERIES_KEY"),
            ("ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT","ROUTED_BASE_UNPACKABLE_OR_UNVERIFIED"),
        ):
            if stats[a]+stats[b]!=len(subset):
                raise shadow.StopAudit("DESCRIPTOR_MODEL_INVALID")
        out[name]=stats
    return out


def main():
    global PHASE
    PHASE="MAP_USER"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    reader=shadow.make_reader(token,time.monotonic()+12)
    if reader("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("USER_SCOPE_MISMATCH")
    PHASE="SOURCE_DOUBLE_READ"
    index,anchors=n2.source_gate_and_anchors()
    chosen=[row[6] for row in anchors["anchors"]]
    PHASE="DESCRIPTOR_FIRST"
    first=descriptor_snapshot(index["scope"],chosen)
    PHASE="DESCRIPTOR_SECOND"
    second=descriptor_snapshot(index["scope"],chosen)
    if first!=second:
        raise shadow.StopAudit("DESCRIPTOR_SNAPSHOT_DRIFT")
    PHASE="COMPARE_SOURCE"
    counts=classify(index,anchors,first)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N6_AUDIT=SCOPED_SOURCE_DESCRIPTOR_PROVENANCE")
    print("RC3N6_USER=MAPPED")
    print("RC3N6_SOURCE_ANCHORS_DOUBLE_READ=IDENTICAL")
    print("RC3N6_SELECTED_ANCHOR_DESCRIPTORS_DOUBLE_READ=IDENTICAL")
    for cohort,stats in counts.items():
        for k,val in stats.items():
            print("RC3N6_"+cohort+"_"+k+"="+str(val))
    print("RC3N6_DESCRIPTOR_MODELED_FROM_PINNED_UPSTREAM=YES")
    print("RC3N6_REAL_JELLYFIN_SERIESID_ISSUED_PROVEN=NO")
    print("RC3N6_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3N6_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N6_JELLYFIN_IDS_COMPUTED=0")
    print("RC3N6_JELLYFIN_IDS_CREATED=0")
    print("RC3N6_CATALOG_CALLS=0")
    print("RC3N6_GATEWAY_FILTER=OFF")
    print("RC3N6_SQLITE_MODE=READ_ONLY_QUERY_ONLY")
    print("RC3N6_PRODUCTION_CHANGES=0")
    print("RC3N6_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        reason=str(error) if isinstance(error,shadow.StopAudit) else ""
        reason=reason if reason in REASONS else "SOURCE_OR_RUNTIME_UNQUALIFIED"
        print("RC3N6_RESULT=STOP_UNQUALIFIED")
        print("RC3N6_FAILURE_PHASE="+(
            PHASE if PHASE in PHASES else "UNKNOWN"))
        print("RC3N6_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N6_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N6_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N6_PRODUCTION_CHANGES=0")
        print("RC3N6_SECRETS_PRINTED=NO")
        raise SystemExit(2)
