#!/usr/bin/env python3
"""RC3-N3: privacy-safe source-only directed match_key redirect integrity.

Requires the previously audited mapped Jellyfin user, uniquely scoped
AIO sink, current verified Bridge graph, and two identical read-only
raw-inventory + anchor/drop snapshots. NO database/API mutation,
no synthetic Jellyfin IDs, no catalog, Trakt, NextUp or playback calls.
This is a *syntactic source graph*, NOT confirmed show equivalence.
Never print or persist raw IDs, episode coords, keys, timestamps or token.
"""
import importlib.util
from collections import Counter
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent


def imported(filename,name):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("LOCAL_MODULE_UNAVAILABLE")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


n2=imported("audit-v140-rc3-source-anchor-drop-homedocker.py","rc3n3_n2")
n1=imported("audit-v140-rc3-source-namespaces-homedocker.py","rc3n3_n1")
shadow=n2.shadow
PHASE="NOT_STARTED"
ALLOWED_PHASES=frozenset({
    "NOT_STARTED","USER_GATE","READ_DOUBLE_SOURCE_AND_ANCHORS",
    "VERIFY_N2","BUILD_REDIRECT_GRAPH","FINAL_GATE",
})
REASONS=frozenset({
    "USER_SCOPE_MISMATCH","SOURCE_SNAPSHOT_DRIFT","ANCHOR_SNAPSHOT_DRIFT",
    "SINK_SCOPE_DRIFT","ANCHOR_BUDGET","DROP_BUDGET",
    "ANCHOR_COUNT_UNVERIFIED","ANCHOR_ROW_INVALID",
    "DROP_ROW_INVALID","SOURCE_CANDIDATE_MISMATCH",
    "SOURCE_RANK_INVALID","ANCHOR_BUCKET_INCONSISTENT",
    "SOURCE_GRAPH_INVALID","SOURCE_GRAPH_OVER_BUDGET",
})
MAX_GRAPH_EDGES=10000


def graph_metrics(index,extra):
    """Only the N2-proven fixed-scope candidate/anchor pairs are considered.

    A parseable match_key is still only a syntactic hint. A redirect may
    point to a key missing from raw source inventory; never create it.
    """
    # N2 validates size/uniqueness/sort/shape and full candidate coverage,
    # drop timestamp safety before we attempt any in-memory correlation.
    n2.summarize(index,extra)
    candidate_keys=[x for x,_ in index["recent"]]
    candidate_set=set(candidate_keys)
    history_set={x for x,_ in index["history"]}
    anchors={row[0]:row for row in extra["anchors"]}
    if len(anchors)!=len(candidate_keys):
        raise shadow.StopAudit("SOURCE_GRAPH_INVALID")

    edges={}
    metrics={
        "PARSEABLE_SELF_MATCH":0,
        "SOURCE_REDIRECT_EDGES":0,
        "REDIRECT_EDGE_TARGET_IN_RECENT":0,
        "REDIRECT_EDGE_TARGET_HISTORY_ONLY":0,
        "REDIRECT_EDGE_TARGET_NOT_IN_HISTORY":0,
        "REDIRECT_SAME_NAMESPACE":0,
        "REDIRECT_CROSS_NAMESPACE":0,
        "TOP_200_REDIRECT_EDGES":0,
        "BEYOND_200_REDIRECT_EDGES":0,
        "REDIRECT_EDGE_TARGET_TOP_200":0,
        "REDIRECT_EDGE_TARGET_BEYOND_200":0,
    }
    positions={key:i for i,key in enumerate(candidate_keys)}
    for source in candidate_keys:
        target=n2.match_series_key(anchors[source][1])
        if not target:
            continue
        if target==source:
            metrics["PARSEABLE_SELF_MATCH"]+=1
            continue
        if source in edges:
            raise shadow.StopAudit("SOURCE_GRAPH_INVALID")
        edges[source]=target
        metrics["SOURCE_REDIRECT_EDGES"]+=1
        if positions[source]<200:
            metrics["TOP_200_REDIRECT_EDGES"]+=1
        else:
            metrics["BEYOND_200_REDIRECT_EDGES"]+=1
        if target in candidate_set:
            metrics["REDIRECT_EDGE_TARGET_IN_RECENT"]+=1
            if positions[target]<200:
                metrics["REDIRECT_EDGE_TARGET_TOP_200"]+=1
            else:
                metrics["REDIRECT_EDGE_TARGET_BEYOND_200"]+=1
        elif target in history_set:
            metrics["REDIRECT_EDGE_TARGET_HISTORY_ONLY"]+=1
        else:
            metrics["REDIRECT_EDGE_TARGET_NOT_IN_HISTORY"]+=1
        if n1.kind(source)==n1.kind(target):
            metrics["REDIRECT_SAME_NAMESPACE"]+=1
        else:
            metrics["REDIRECT_CROSS_NAMESPACE"]+=1

    if len(edges)>MAX_GRAPH_EDGES:
        raise shadow.StopAudit("SOURCE_GRAPH_OVER_BUDGET")
    inbound=Counter(edges.values())
    metrics["REDIRECT_DISTINCT_DESTINATIONS"]=len(inbound)
    metrics["DESTINATIONS_WITH_MULTIPLE_SOURCES"]=sum(
        number>=2 for number in inbound.values()
    )
    metrics["MAX_SOURCES_TO_ONE_DESTINATION"]=max(inbound.values(),default=0)
    metrics["EDGES_TARGETING_MULTISOURCE_DESTINATIONS"]=sum(
        number for number in inbound.values() if number>=2
    )

    # Functional graph, bounded by MAX_GRAPH_EDGES. All names remain
    # private and never appear in printed output.
    cycles=set()
    nodes_in_cycles=set()
    max_path=0
    source_paths_two_or_more=0
    sources_eventually_cyclic=0
    for source in edges:
        visited={}
        path=[]
        cursor=source
        found_cycle=False
        while cursor in edges and cursor not in visited:
            visited[cursor]=len(path)
            path.append(cursor)
            cursor=edges[cursor]
        if cursor in visited:
            component=frozenset(path[visited[cursor]:])
            if len(component)>=2:
                cycles.add(component)
                nodes_in_cycles.update(component)
                found_cycle=True
        if found_cycle:
            sources_eventually_cyclic+=1
        max_path=max(max_path,len(path))
        if len(path)>=2:
            source_paths_two_or_more+=1
    metrics["MULTIHOP_STARTS"]=source_paths_two_or_more
    metrics["MAX_REDIRECT_HOPS"]=max_path
    metrics["CYCLE_COMPONENTS"]=len(cycles)
    metrics["CYCLE_NODES"]=len(nodes_in_cycles)
    metrics["SOURCES_REACHING_A_CYCLE"]=sources_eventually_cyclic
    if (
        metrics["TOP_200_REDIRECT_EDGES"]+
        metrics["BEYOND_200_REDIRECT_EDGES"]!=len(edges)
        or metrics["REDIRECT_EDGE_TARGET_IN_RECENT"]+
        metrics["REDIRECT_EDGE_TARGET_HISTORY_ONLY"]+
        metrics["REDIRECT_EDGE_TARGET_NOT_IN_HISTORY"]!=len(edges)
        or metrics["REDIRECT_SAME_NAMESPACE"]+
        metrics["REDIRECT_CROSS_NAMESPACE"]!=len(edges)
        or metrics["REDIRECT_EDGE_TARGET_TOP_200"]+
        metrics["REDIRECT_EDGE_TARGET_BEYOND_200"] !=
        metrics["REDIRECT_EDGE_TARGET_IN_RECENT"]
    ):
        raise shadow.StopAudit("SOURCE_GRAPH_INVALID")
    return metrics


def main():
    global PHASE
    PHASE="USER_GATE"
    token,mapped=shadow.credential()
    shadow.bridge_gate()
    read=shadow.make_reader(token,time.monotonic()+12)
    if read("/Users/Me").get("Id")!=mapped:
        raise shadow.StopAudit("USER_SCOPE_MISMATCH")
    PHASE="READ_DOUBLE_SOURCE_AND_ANCHORS"
    index,extra=n2.source_gate_and_anchors()
    PHASE="VERIFY_N2"
    n2.summarize(index,extra)
    PHASE="BUILD_REDIRECT_GRAPH"
    counts=graph_metrics(index,extra)
    PHASE="FINAL_GATE"
    shadow.bridge_gate()
    print("RC3N3_AUDIT=READ_ONLY_SOURCE_REDIRECT_INTEGRITY")
    print("RC3N3_USER=MAPPED")
    print("RC3N3_SOURCE_AND_ANCHORS=DOUBLE_READ_IDENTICAL")
    for name,count in counts.items():
        print("RC3N3_"+name+"="+str(count))
    print("RC3N3_SYNTAX_LINKS_NOT_PROVEN_CANONICAL=YES")
    print("RC3N3_GLOBAL_NEXTUP_QUALIFIED=NO")
    print("RC3N3_PLAYBACK_PROVEN=NO")
    print("RC3N3_JELLYFIN_IDS_CREATED=0")
    print("RC3N3_CATALOG_CALLS=0")
    print("RC3N3_GATEWAY_FILTER=OFF")
    print("RC3N3_SQLITE=MODE_RO_QUERY_ONLY")
    print("RC3N3_HTTP=ONE_AUTHENTICATED_LOCAL_USER_GET")
    print("RC3N3_DATABASE_WRITES=0")
    print("RC3N3_PRODUCTION_CHANGES=0")
    print("RC3N3_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        reason=str(error) if isinstance(error,shadow.StopAudit) else ""
        if reason not in REASONS:
            reason="SOURCE_OR_RUNTIME_UNQUALIFIED"
        print("RC3N3_RESULT=STOP_UNQUALIFIED")
        print("RC3N3_FAILURE_PHASE="+(
            PHASE if PHASE in ALLOWED_PHASES else "UNKNOWN"))
        print("RC3N3_FAILURE_REASON="+reason)
        try:
            shadow.bridge_gate()
            print("RC3N3_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3N3_FILTER_ON_STOP=UNVERIFIED")
        print("RC3N3_PRODUCTION_CHANGES=0")
        print("RC3N3_SECRETS_PRINTED=NO")
        raise SystemExit(2)
