#!/usr/bin/env python3
"""RC3-I: probe AIO's real paginated series episode catalog, GET-only.

Uses the SAME mapped Jellyfin session across /Users/Me, /Shows/NextUp,
Series /Items, /Shows/{SeriesId}/Episodes and one /Items/{EpisodeId}.
Links ONLY to an existing NextUp canonical card from Bridge's verified
cross-IMDb identity graph. Makes NO Jellyfin NextUp replacement, user
history write, Trakt write, Gateway toggle or production deployment.
Output contains counts and categorical flags only; no titles/IDs/URLs
or credentials. Uses a double-read bounded catalog; not a transactional
snapshot and NOT a streaming/playback availability check.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import time
from urllib.parse import quote

HERE = Path(__file__).parent
MAX_METADATA = 50
CATALOG_PAGE = 100
CATALOG_MAX_PAGES = 4
DEADLINE_SECONDS = 90

# Never emit the body of an unexpected exception, credential, URL or raw IDs.
# Only fixed source-code error constants and finite state names may be printed.
SAFE_STOP_CODES = frozenset({
    "PRIVATE_FILE_NOT_AVAILABLE", "PRIVATE_FILE_UNSAFE", "PRIVATE_DIR_UNSAFE",
    "PRIVATE_DIR_NOT_AVAILABLE", "TOKEN_FORMAT_INVALID",
    "MAPPED_USER_ID_UNAVAILABLE", "BRIDGE_GATE_UNAVAILABLE",
    "BRIDGE_FILTER_NOT_OFF_OR_SOURCE_UNEXPECTED",
    "USER_IDENTITY_MISMATCH", "MAPPED_USER_MISMATCH",
    "CONNECTED_PROFILE_NOT_UNIQUE", "VERIFIED_GRAPH_UNAVAILABLE",
    "BRIDGE_DATABASE_UNAVAILABLE", "GRAPH_UNTRUSTED",
    "VERIFIED_ALIAS_GROUPS_UNAVAILABLE",
    "AUDIT_DEADLINE_EXCEEDED", "AIO_GET_NON_200",
    "AIO_RESPONSE_TOO_LARGE", "AIO_JSON_CONTRACT",
    "AIO_GET_UNAVAILABLE", "EXACT_ONE_ALIAS_GROUP_REQUIRED",
    "CANONICAL_ONLY_NEXTUP_NOT_PROVEN", "NEXTUP_SOURCE_DRIFT",
    "NEXTUP_WINDOW_UNQUALIFIED", "INVALID_NEXTUP_PAGE",
    "UNEXPECTED_FIRST_PAGE_OFFSET", "INVALID_TOTAL_COUNT",
    "INVALID_COUNT", "INVALID_EPISODE_SHAPE", "EPISODE_IDS_OVERLAP",
    "SERIES_METADATA_IDENTITY_UNVERIFIED", "CANONICAL_SERIES_NOT_VERIFIED",
    "CATALOG_PAGING_CONTRACT_INVALID", "CATALOG_TOTAL_CHANGED",
    "CATALOG_EPISODE_IDENTITY_INVALID", "CATALOG_DUPLICATE_ID_OR_COORDINATE",
    "CATALOG_PARTIAL_TOTAL", "CATALOG_EXCEEDS_AUDIT_BUDGET",
    "CATALOG_SNAPSHOT_DRIFT", "NEXTUP_ITEM_NOT_IN_CATALOG",
    "EXISTING_EPISODE_NOT_RESOLVABLE",
})
PHASE = "BEFORE_START"
PHASES = frozenset({
    "BEFORE_START", "READ_PRIVATE_SESSION", "CHECK_GATE_AND_GRAPH",
    "AUTHENTICATED_USER_PROBE", "READ_DOUBLE_NEXTUP",
    "VERIFY_NEXTUP_SHAPE", "READ_SERIES_METADATA",
    "VERIFY_CANONICAL", "CATALOG_FIRST_PASS",
    "CATALOG_SECOND_PASS", "CATALOG_REPLAY_CHECK",
    "NEXTUP_EPISODE_IN_CATALOG", "GET_EXISTING_EPISODE",
    "FINAL_GATE",
})


def safe_stop_code(error):
    if isinstance(error, core.StopAudit):
        code = str(error)
        return code if code in SAFE_STOP_CODES else "UNCLASSIFIED_SAFE_STOP"
    return "LOCAL_RUNTIME_FAILURE"


def module(path, name):
    spec=importlib.util.spec_from_file_location(name, HERE/path)
    if spec is None or spec.loader is None:
        raise RuntimeError("MODULE_MISSING")
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


visibility=module("audit-v140-rc3-nextup-visibility-homedocker.py",
                  "rc3i_visibility")
core=visibility.core
adaptive=visibility.adaptive


def candidate_from_window(rows, metadata, groups):
    """Choose ONLY one genuine canonical NextUp row, no fabricated SeriesId."""
    if len(groups)!=1 or len(groups[0])<2:
        raise core.StopAudit("EXACT_ONE_ALIAS_GROUP_REQUIRED")
    canonical=groups[0][0]
    alias=set(groups[0][1:])
    winners=[row for row in rows
             if metadata.get(row["SeriesId"])==canonical]
    other=[row for row in rows
           if metadata.get(row["SeriesId"]) in alias]
    if len(winners)!=1 or other:
        raise core.StopAudit("CANONICAL_ONLY_NEXTUP_NOT_PROVEN")
    return winners[0]


def validate_catalog_page(page, offset, requested_limit, series_id,
                          ids, coordinates, expected_total):
    items=page.get("Items")
    total=page.get("TotalRecordCount")
    if not isinstance(items,list) or len(items)>requested_limit \
            or page.get("StartIndex")!=offset or type(total) is not int \
            or total<offset+len(items) \
            or ("Count" in page and page["Count"]!=len(items)):
        raise core.StopAudit("CATALOG_PAGING_CONTRACT_INVALID")
    if expected_total is not None and expected_total!=total:
        raise core.StopAudit("CATALOG_TOTAL_CHANGED")
    for row in items:
        if not isinstance(row,dict) or row.get("Type")!="Episode" \
                or not isinstance(row.get("Id"),str) or not row["Id"] \
                or row.get("SeriesId")!=series_id \
                or type(row.get("ParentIndexNumber")) is not int \
                or not 0<=row["ParentIndexNumber"]<=200 \
                or type(row.get("IndexNumber")) is not int \
                or not 1<=row["IndexNumber"]<=10000:
            raise core.StopAudit("CATALOG_EPISODE_IDENTITY_INVALID")
        coord=(row["ParentIndexNumber"],row["IndexNumber"])
        if row["Id"] in ids or coord in coordinates:
            raise core.StopAudit("CATALOG_DUPLICATE_ID_OR_COORDINATE")
        ids.add(row["Id"]);coordinates.add(coord)
    return items,total


def scan_pages(read,series_id):
    catalog=[];response_pages=[];ids=set();coords=set();total=None
    encoded=quote(series_id,safe="")
    for i in range(CATALOG_MAX_PAGES):
        offset=i*CATALOG_PAGE
        page=read("/Shows/"+encoded+"/Episodes?StartIndex="+str(offset)
                  +"&Limit="+str(CATALOG_PAGE))
        items,total=validate_catalog_page(
            page,offset,CATALOG_PAGE,series_id,ids,coords,total
        )
        response_pages.append(page)
        catalog.extend(items)
        if len(items)<CATALOG_PAGE or len(catalog)==total:
            if len(catalog)!=total:
                raise core.StopAudit("CATALOG_PARTIAL_TOTAL")
            return catalog,response_pages
    raise core.StopAudit("CATALOG_EXCEEDS_AUDIT_BUDGET")


def main():
    global PHASE
    PHASE = "READ_PRIVATE_SESSION"
    token,mapped=core.credential()
    PHASE = "CHECK_GATE_AND_GRAPH"
    core.bridge_gate()
    groups=visibility.verified_groups_from_bridge()
    read=core.make_reader(token,time.monotonic()+DEADLINE_SECONDS)
    PHASE = "AUTHENTICATED_USER_PROBE"
    if read("/Users/Me").get("Id")!=mapped:
        raise core.StopAudit("MAPPED_USER_MISMATCH")
    url="/Shows/NextUp?StartIndex=0&Limit=50"
    PHASE = "READ_DOUBLE_NEXTUP"
    window1=read(url);window2=read(url)
    if window1!=window2:
        raise core.StopAudit("NEXTUP_SOURCE_DRIFT")
    PHASE = "VERIFY_NEXTUP_SHAPE"
    rows=adaptive.items(window1,MAX_METADATA)
    series_ids=sorted({r["SeriesId"] for r in rows})
    if not series_ids or len(series_ids)>MAX_METADATA:
        raise core.StopAudit("NEXTUP_WINDOW_UNQUALIFIED")

    def detail(id):
        item=read("/Items/"+quote(id,safe=""))
        if item.get("Type")!="Series" or item.get("Id")!=id:
            raise core.StopAudit("SERIES_METADATA_IDENTITY_UNVERIFIED")
        provider=item.get("ProviderIds")
        imdb=provider.get("Imdb") if isinstance(provider,dict) else None
        return id,imdb.lower() if isinstance(imdb,str) else None,item

    PHASE = "READ_SERIES_METADATA"
    with ThreadPoolExecutor(max_workers=4) as pool:
        resolved=list(pool.map(detail,series_ids))
    meta={id:imdb for id,imdb,_ in resolved}
    PHASE = "VERIFY_CANONICAL"
    row=candidate_from_window(rows,meta,groups)
    series_id=row["SeriesId"]
    series=next((obj for id,_,obj in resolved if id==series_id),None)
    if series is None or meta.get(series_id)!=groups[0][0]:
        raise core.StopAudit("CANONICAL_SERIES_NOT_VERIFIED")
    PHASE = "CATALOG_FIRST_PASS"
    catalog,first_pages=scan_pages(read,series_id)
    PHASE = "CATALOG_SECOND_PASS"
    catalog_again,second_pages=scan_pages(read,series_id)
    PHASE = "CATALOG_REPLAY_CHECK"
    if first_pages!=second_pages:
        raise core.StopAudit("CATALOG_SNAPSHOT_DRIFT")
    PHASE = "NEXTUP_EPISODE_IN_CATALOG"
    nextup_ep=row["Id"]
    catalog_by_id={item["Id"]:item for item in catalog}
    if nextup_ep not in catalog_by_id:
        raise core.StopAudit("NEXTUP_ITEM_NOT_IN_CATALOG")
    # Only one exact preexisting episode item is GET-resolved. PlaybackInfo
    # and media streams are intentionally never requested.
    PHASE = "GET_EXISTING_EPISODE"
    detail_item=read("/Items/"+quote(nextup_ep,safe=""))
    candidate=catalog_by_id[nextup_ep]
    if not isinstance(detail_item,dict) or detail_item.get("Type")!="Episode" \
            or detail_item.get("Id")!=nextup_ep \
            or detail_item.get("SeriesId")!=series_id \
            or (detail_item.get("ParentIndexNumber"),
                detail_item.get("IndexNumber")) != (
                    candidate["ParentIndexNumber"],candidate["IndexNumber"]
                ):
        raise core.StopAudit("EXISTING_EPISODE_NOT_RESOLVABLE")

    PHASE = "FINAL_GATE"
    core.bridge_gate()
    print("RC3I_AUDIT=REAL_SERIES_CATALOG_CONTRACT")
    print("RC3I_USER=MAPPED")
    print("RC3I_NEXTUP_WINDOW=DOUBLE_READ_STABLE")
    print("RC3I_VERIFIED_CANONICAL_ONLY=YES")
    print("RC3I_SERIES_METADATA_LOOKUPS="+str(len(series_ids)))
    print("RC3I_SERIES_EPISODE_PAGES="+str(len(first_pages)))
    print("RC3I_SERIES_EPISODES_VERIFIED="+str(len(catalog)))
    print("RC3I_CATALOG_DOUBLE_READ=IDENTICAL")
    print("RC3I_NEXTUP_EPISODE_IN_CATALOG=YES")
    print("RC3I_EXISTING_EPISODE_ITEM_GET=VERIFIED")
    print("RC3I_FULL_NEXTUP_QUALIFIED=NO")
    print("RC3I_MEDIA_PLAYBACK_PROVEN=NO")
    print("RC3I_FILTER_FINAL=OFF")
    print("RC3I_AUTHENTICATED_GET_ONLY=YES")
    print("RC3I_CLIENT_RESPONSE_CHANGED=NO")
    print("RC3I_PRODUCTION_CHANGES=0")
    print("RC3I_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except (core.StopAudit,OSError,ValueError,TypeError,RuntimeError) as error:
        print("RC3I_RESULT=STOP_CATALOG_UNQUALIFIED")
        print("RC3I_FAILURE_PHASE=" + (PHASE if PHASE in PHASES else "UNKNOWN_PHASE"))
        print("RC3I_FAILURE_REASON=" + safe_stop_code(error))
        # Cannot infer status of external operator changes after a stop.
        print("RC3I_FILTER_ON_STOP=UNVERIFIED")
        print("RC3I_SECRETS_PRINTED=NO")
        print("RC3I_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
