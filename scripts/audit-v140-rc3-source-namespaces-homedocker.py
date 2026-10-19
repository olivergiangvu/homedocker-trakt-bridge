#!/usr/bin/env python3
"""RC3-N1: classify all observed AIO source keys by ID namespace, read-only.

A namespace syntax match is NOT an Identity Graph match and is NOT proof
that /Shows/{SeriesId}/Episodes or /Items/{EpisodeId} can resolve.
Uses RC3-M2's already bounded, exact mapped-user/uuid/persona, graph-
verified, fully double-read source inventory. ONE authenticated /Users/Me
GET plus two read-only SQLite snapshots; no catalog, NextUp or Trakt GET.
No raw series keys, IMDb IDs, titles, episode coords, tokens or timestamps
in output; fixed category names and numeric counts only.
"""
import importlib.util
from pathlib import Path
import re
import time

HERE=Path(__file__).resolve().parent


def import_file(filename,name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("DEPENDENCY_MISSING")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


inventory=import_file("audit-v140-rc3-full-series-inventory-homedocker.py",
                      "rc3n1_inventory")
shadow=inventory.shadow
GROUPS=(
    "IMDB_CANONICAL","IMDB_ALTERNATE","TVDB","TMDB",
    "MAL","KITSU","ANILIST","ANIDB","ANIME_MISC",
    "OTHER_OR_OPAQUE",
)
PHASE="INIT"
ALLOWED_PHASES=frozenset({
    "INIT","GATE_USER","SNAPSHOT_FIRST","SNAPSHOT_SECOND",
    "VERIFY_INVENTORY","CLASSIFY_NAMESPACES","FINAL_GATE",
})
ALLOWED_ERRORS=frozenset({
    "SOURCE_SNAPSHOT_DRIFT","MAPPED_JELLYFIN_USER_MISMATCH",
    "SOURCE_SERIES_BUDGET","SOURCE_CANDIDATE_BUDGET",
    "SOURCE_HISTORY_KEY_INVALID","SOURCE_HISTORY_COUNT_INVALID",
    "SOURCE_RECENT_KEY_INVALID","SOURCE_RECENT_SORT_INVALID",
    "SOURCE_RECENT_ORDER_INVALID","SOURCE_RECENT_NOT_IN_HISTORY",
    "PROFILE_NOT_UNIQUE","WATCH_SINK_NOT_UNIQUE",
    "WATCH_SINK_SCOPE_INVALID","GRAPH_CACHE_UNAVAILABLE",
    "GRAPH_UNTRUSTED","GRAPH_ALIAS_GROUP_LIMIT",
    "INVENTORY_COUNT_MISMATCH","SOURCE_CATEGORY_UNVERIFIED",
})


def kind(series_key):
    """Syntax buckets verified against pinned upstream IdParser definitions.

    These are classification hints ONLY. No ID resolution/mapping is made.
    The AIO IdParser accepts ttN, imdb:N, tvdb:N, tmdb:N and anime
    provider forms; keep unfamiliar keys untouched under OTHER/OPAQUE.
    """
    if not isinstance(series_key,str) or len(series_key)>512 \
            or not series_key.startswith("s|"):
        return "OTHER_OR_OPAQUE"
    base=series_key[2:]
    if re.fullmatch(r"tt[0-9]{7,12}",base):
        return "IMDB_CANONICAL"
    if re.fullmatch(r"(?:tt[0-9]+|imdb[:-]?[0-9]+)",base):
        return "IMDB_ALTERNATE"
    if re.fullmatch(r"tvdb[:-]?[0-9]+",base):
        return "TVDB"
    if re.fullmatch(r"tmdb[:-]?[0-9]+",base):
        return "TMDB"
    for name,pattern in (
        ("MAL",r"mal[:-]?[0-9]+"),
        ("KITSU",r"kitsu[:-]?[0-9]+"),
        ("ANILIST",r"anilist[:-]?[0-9]+"),
        ("ANIDB",r"(?:anidb|anidb_id|anidbid)[:-]?[0-9]+"),
    ):
        if re.fullmatch(pattern,base):
            return name
    for pattern in (
        r"(?:animeplanet|ap)[:-]?[0-9]+",
        r"acd[:-]?[0-9]+",
        r"anisearch[:-]?[0-9]+",
        r"(?:notifymoe|nm)[:-]?[a-zA-Z0-9]+",
        r"simkl[:-]?[0-9]+",
    ):
        if re.fullmatch(pattern,base):
            return "ANIME_MISC"
    return "OTHER_OR_OPAQUE"


def summarize(snapshot):
    """Use complete, validated RC3-M candidate list, not a NextUp prefix."""
    checks=inventory.aggregate(snapshot)
    totals={c:0 for c in GROUPS}
    recent={c:0 for c in GROUPS}
    late={c:0 for c in GROUPS}
    history_keys=set()
    for key,_ in snapshot["history"]:
        typ=kind(key)
        totals[typ]+=1
        history_keys.add(key)
    for i,(key,_) in enumerate(snapshot["recent"],1):
        typ=kind(key)
        if key not in history_keys:
            raise shadow.StopAudit("INVENTORY_COUNT_MISMATCH")
        recent[typ]+=1
        if i>200:
            late[typ]+=1
    if sum(totals.values())!=checks["full_history_series_keys"] \
            or sum(recent.values())!=checks["recent_series_candidate_keys"] \
            or sum(late.values())!=checks["source_candidate_rank_beyond_200"] \
            or totals["IMDB_CANONICAL"]!=checks["history_keys_with_imdb_syntax"] \
            or recent["IMDB_CANONICAL"]!=checks[
                "recent_candidate_keys_with_imdb_syntax"]:
        raise shadow.StopAudit("INVENTORY_COUNT_MISMATCH")
    return totals,recent,late,checks


def main():
    global PHASE
    PHASE="GATE_USER"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    read=shadow.make_reader(token,time.monotonic()+12)
    if read("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("MAPPED_JELLYFIN_USER_MISMATCH")
    PHASE="SNAPSHOT_FIRST"
    first=inventory.snapshot()
    PHASE="SNAPSHOT_SECOND"
    second=inventory.snapshot()
    if first!=second:
        raise shadow.StopAudit("SOURCE_SNAPSHOT_DRIFT")
    PHASE="VERIFY_INVENTORY"
    inventory.aggregate(first)
    PHASE="CLASSIFY_NAMESPACES"
    totals,recent,late,checks=summarize(first)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N1_AUDIT=SCOPED_SOURCE_NAMESPACE_FEASIBILITY")
    print("RC3N1_USER=MAPPED")
    print("RC3N1_DOUBLE_SNAPSHOT=IDENTICAL")
    print("RC3N1_HISTORY_KEYS="+str(checks["full_history_series_keys"]))
    print("RC3N1_CANDIDATE_KEYS="+str(checks["recent_series_candidate_keys"]))
    print("RC3N1_CANDIDATES_BEYOND_200="+str(
        checks["source_candidate_rank_beyond_200"]))
    for cat in GROUPS:
        print("RC3N1_HISTORY_"+cat+"="+str(totals[cat]))
        print("RC3N1_CANDIDATE_"+cat+"="+str(recent[cat]))
        print("RC3N1_BEYOND_200_"+cat+"="+str(late[cat]))
    print("RC3N1_SYNTAX_CLASSIFICATION_NOT_IDENTITY_PROOF=YES")
    print("RC3N1_RESOLVABLE_ITEM_IDS_PROVEN=NO")
    print("RC3N1_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N1_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3N1_FILTER_FINAL=OFF")
    print("RC3N1_MODE=SQLITE_READ_ONLY_AND_ONE_LOCAL_USER_GET")
    print("RC3N1_PRODUCTION_CHANGES=0")
    print("RC3N1_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        reason=str(exc) if isinstance(exc,shadow.StopAudit) else ""
        if reason not in ALLOWED_ERRORS:
            reason="UNQUALIFIED_SOURCE_OR_RUNTIME"
        print("RC3N1_RESULT=STOP")
        print("RC3N1_FAILURE_PHASE="+(
            PHASE if PHASE in ALLOWED_PHASES else "UNKNOWN"))
        print("RC3N1_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N1_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N1_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N1_PRODUCTION_CHANGES=0")
        print("RC3N1_SECRETS_PRINTED=NO")
        raise SystemExit(2)
