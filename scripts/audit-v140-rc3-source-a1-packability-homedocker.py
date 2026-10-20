#!/usr/bin/env python3
"""RC3-N5: syntactic packability of existing source *series descriptors*.

Do NOT issue, calculate or print any a1/b2 Jellyfin identifier. This audit
tests solely whether raw source base strings and watch_state media types could
satisfy pinned AIO tryPack(k=series)'s canonical numeric parser constraints.
Actual nextUp routes use seriesIdOf(base_id,video_id,media_type), may differ
from raw series_key. Consequently ALL results are theoretical hints, not
evidence of an issued ID, per-user authorization or a playable item.

Exact mapped Jellyfin user, unique watch sink + fresh graph, RC3-N2 double
read-only SQLite snapshots, one GET /Users/Me, Filter OFF. No metadata.
"""
import importlib.util
from pathlib import Path
import re
import time

HERE=Path(__file__).resolve().parent


def import_file(filename, name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPENDENCY_UNAVAILABLE")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


n2=import_file("audit-v140-rc3-source-anchor-drop-homedocker.py","rc3n5_n2")
shadow=n2.shadow
MAX48=2**48-1
TYPES=frozenset({"movie","series","anime","tv","other","channel"})
PREFIXES=("tmdb","tvdb","kitsu","mal","anilist","anidb","simkl")
IMDB=re.compile(r"tt(?P<n>[0-9]+)\Z")
NUMERIC=re.compile(r"(?P<family>tmdb|tvdb|kitsu|mal|anilist|anidb|simkl):(?P<n>[0-9]+)\Z")
PHASE="NOT_STARTED"
PHASES=frozenset({"NOT_STARTED","USER_GATE","SOURCE_DOUBLE_READ","CLASSIFY","FINAL_GATE"})
REASONS=frozenset({"USER_SCOPE_MISMATCH","SOURCE_SNAPSHOT_DRIFT",
                    "ANCHOR_SNAPSHOT_DRIFT","SINK_SCOPE_DRIFT",
                    "ANCHOR_BUDGET","DROP_BUDGET","ANCHOR_COUNT_UNVERIFIED",
                    "ANCHOR_ROW_INVALID","SOURCE_CANDIDATE_MISMATCH",
                    "SOURCE_RANK_INVALID","DROP_ROW_INVALID",
                    "ANCHOR_BUCKET_INCONSISTENT",
                    "PACKABILITY_COUNTS_INVALID"})


def packability_hint(raw_key, media):
    """Classify candidate source syntax; NEVER construct its Jellyfin ID.

    The pinned upstream tryPack is only one portion of the Jellyfin ID
    contract. A source key is not necessarily the routed series descriptor.
    """
    if not isinstance(raw_key,str) or not raw_key.startswith("s|"):
        return "NOT_CANONICAL_PACKABLE"
    base=raw_key[2:]
    if not isinstance(media,str) or media not in TYPES:
        return "MEDIA_TYPE_UNSUPPORTED"
    match=IMDB.fullmatch(base)
    if match:
        num=int(match.group("n"))
        if num<=MAX48 and "tt"+str(num).zfill(7)==base:
            return "CANONICAL_PACKABLE_SYNTAX"
        return "NOT_CANONICAL_PACKABLE"
    match=NUMERIC.fullmatch(base)
    if match:
        num=int(match.group("n"))
        if num<=MAX48 and match.group("family")+":"+str(num)==base:
            return "CANONICAL_PACKABLE_SYNTAX"
    return "NOT_CANONICAL_PACKABLE"


def count_hints(index, anchor_data):
    # AIO anchor matches are untrusted until strict N2 scope/shape and
    # source-key coverage have passed; no partial counts should escape.
    n2.summarize(index,anchor_data)
    recent=[key for key,_ in index["recent"]]
    anchors={row[0]:row for row in anchor_data["anchors"]}
    if len(anchors)!=len(recent):
        raise shadow.StopAudit("PACKABILITY_COUNTS_INVALID")
    out={}
    for band,subset in (
        ("TOP_200",recent[:200]),
        ("BEYOND_200",recent[200:]),
    ):
        stats={
            "RAW_SOURCE_KEYS":len(subset),
            "CANONICAL_PACKABLE_SYNTAX_HINT":0,
            "MEDIA_TYPE_UNSUPPORTED":0,
            "NONCANONICAL_OR_OPAQUE_SOURCE":0,
            "REDIRECT_TARGET_CANONICAL_PACKABLE_SYNTAX_HINT":0,
            "REDIRECT_TARGET_UNPACKABLE_OR_UNVERIFIED":0,
        }
        for key in subset:
            anchor=anchors[key]
            result=packability_hint(key,anchor[2])
            if result=="CANONICAL_PACKABLE_SYNTAX":
                stats["CANONICAL_PACKABLE_SYNTAX_HINT"]+=1
            elif result=="MEDIA_TYPE_UNSUPPORTED":
                stats["MEDIA_TYPE_UNSUPPORTED"]+=1
            else:
                stats["NONCANONICAL_OR_OPAQUE_SOURCE"]+=1
            target=n2.match_series_key(anchor[1])
            if target and target!=key:
                if packability_hint(target,anchor[2])=="CANONICAL_PACKABLE_SYNTAX":
                    stats["REDIRECT_TARGET_CANONICAL_PACKABLE_SYNTAX_HINT"]+=1
                else:
                    stats["REDIRECT_TARGET_UNPACKABLE_OR_UNVERIFIED"]+=1
        if sum(stats[k] for k in (
            "CANONICAL_PACKABLE_SYNTAX_HINT","MEDIA_TYPE_UNSUPPORTED",
            "NONCANONICAL_OR_OPAQUE_SOURCE"))!=len(subset):
            raise shadow.StopAudit("PACKABILITY_COUNTS_INVALID")
        out[band]=stats
    return out


def main():
    global PHASE
    PHASE="USER_GATE"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    reader=shadow.make_reader(token,time.monotonic()+12)
    if reader("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("USER_SCOPE_MISMATCH")
    PHASE="SOURCE_DOUBLE_READ"
    index,anchors=n2.source_gate_and_anchors()
    PHASE="CLASSIFY"
    result=count_hints(index,anchors)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N5_AUDIT=SCOPED_RAW_SOURCE_A1_PACKABILITY_HINT_ONLY")
    print("RC3N5_USER=MAPPED")
    print("RC3N5_SOURCE_ANCHOR_DOUBLE_READ=IDENTICAL")
    for band,stat in result.items():
        for field,count in stat.items():
            print("RC3N5_"+band+"_"+field+"="+str(count))
    print("RC3N5_MATCHES_A1_SYNTAX_NOT_ACTUAL_SERIESID=YES")
    print("RC3N5_HASHED_B2_MAP_ABSENCE_NOT_RESOLUTION_FAILURE=YES")
    print("RC3N5_SOURCE_ROW_DESCRIPTOR_VERIFIED=NO")
    print("RC3N5_JELLYFIN_IDS_COMPUTED=0")
    print("RC3N5_JELLYFIN_IDS_CREATED=0")
    print("RC3N5_CATALOG_CALLS=0")
    print("RC3N5_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N5_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3N5_GATEWAY_FILTER=OFF")
    print("RC3N5_SQLITE=MODE_RO_QUERY_ONLY")
    print("RC3N5_PRODUCTION_CHANGES=0")
    print("RC3N5_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        reason=str(error) if isinstance(error,shadow.StopAudit) else ""
        if reason not in REASONS:
            reason="SOURCE_OR_RUNTIME_UNQUALIFIED"
        print("RC3N5_RESULT=STOP_UNQUALIFIED")
        print("RC3N5_FAILURE_PHASE="+(
            PHASE if PHASE in PHASES else "UNKNOWN"))
        print("RC3N5_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N5_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N5_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N5_PRODUCTION_CHANGES=0")
        print("RC3N5_SECRETS_PRINTED=NO")
        raise SystemExit(2)
