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
# RC3-J HomeDocker: 6s NextUp timed out; single 12s retry completed in 11.377s.
# Extend ONLY this offline catalog audit's two NextUp GETs, not its other
# endpoint budgets and never the production Gateway.
NEXTUP_TIMEOUT_SECONDS = 18
# Standalone verification of a genuine preexisting episode ID may be slower
# than lightweight Series metadata. 18s applies ONLY to this one item GET.
EPISODE_DETAIL_TIMEOUT_SECONDS = 18
DEADLINE_SECONDS = 150

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
    "NEXTUP_18S_TIMEOUT", "NEXTUP_18S_TRANSPORT_UNAVAILABLE",
    "EPISODE_GET_18S_TIMEOUT", "EPISODE_GET_18S_TRANSPORT_UNAVAILABLE",
    "EPISODE_GET_BAD_JSON", "EPISODE_GET_BODY_TOO_LARGE",
})
PHASE = "BEFORE_START"
# This becomes true ONLY after two identical fully validated catalog reads
# and a match to the existing canonical NextUp Episode ID.
CATALOG_EVIDENCE_VALIDATED = False
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


def read_bounded_nextup(token, deadline):
    """Only the fixed 50-card local NextUp route gets the 18s allowance.

    Reuses RC3 core's explicit local base URL, no proxies/redirects, bounded
    body, same user token and GET-only headers. Never prints raw exceptions.
    This function does NOT alter core.MAX_CALL_SECONDS or production Gateway.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise core.StopAudit("AUDIT_DEADLINE_EXCEEDED")
    opener = core.build_opener(core.ProxyHandler({}), core.NoRedirect())
    path = "/Shows/NextUp?StartIndex=0&Limit=50"
    req = core.Request(
        core.AIO + path, method="GET",
        headers={
            "X-Emby-Token": token,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "homedocker-rc3i3-catalog-readonly/1",
        },
    )
    try:
        with opener.open(req, timeout=min(NEXTUP_TIMEOUT_SECONDS, remaining)) as response:
            if response.status != 200:
                raise core.StopAudit("AIO_GET_NON_200")
            raw = response.read(core.MAX_BODY + 1)
            if len(raw) > core.MAX_BODY:
                raise core.StopAudit("AIO_RESPONSE_TOO_LARGE")
            value = core.json.loads(raw)
            if not isinstance(value, dict):
                raise core.StopAudit("AIO_JSON_CONTRACT")
            return value
    except core.HTTPError:
        raise core.StopAudit("AIO_GET_NON_200") from None
    except core.URLError as err:
        if isinstance(err.reason, TimeoutError):
            raise core.StopAudit("NEXTUP_18S_TIMEOUT") from None
        raise core.StopAudit("NEXTUP_18S_TRANSPORT_UNAVAILABLE") from None
    except TimeoutError:
        raise core.StopAudit("NEXTUP_18S_TIMEOUT") from None
    except (OSError, UnicodeError, ValueError):
        raise core.StopAudit("NEXTUP_18S_TRANSPORT_UNAVAILABLE") from None


def read_existing_episode_detail(token, episode_id, deadline):
    """Exactly one already-discovered catalog/NextUp episode; GET only.

    No retries, no PlaybackInfo, no stream/progress endpoints. Does NOT
    modify the shared core 6s timeout used by all other item GETs.
    """
    if not isinstance(episode_id, str) or not 1 <= len(episode_id) <= 512:
        raise core.StopAudit("EXISTING_EPISODE_NOT_RESOLVABLE")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise core.StopAudit("AUDIT_DEADLINE_EXCEEDED")
    opener = core.build_opener(core.ProxyHandler({}), core.NoRedirect())
    req = core.Request(
        core.AIO + "/Items/" + quote(episode_id, safe=""), method="GET",
        headers={
            "X-Emby-Token": token,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "homedocker-rc3i4-catalog-readonly/1",
        },
    )
    try:
        with opener.open(
            req, timeout=min(EPISODE_DETAIL_TIMEOUT_SECONDS, remaining)
        ) as response:
            if response.status != 200:
                raise core.StopAudit("AIO_GET_NON_200")
            raw = response.read(core.MAX_BODY + 1)
            if len(raw) > core.MAX_BODY:
                raise core.StopAudit("EPISODE_GET_BODY_TOO_LARGE")
            try:
                item = core.json.loads(raw)
            except (ValueError, UnicodeError):
                raise core.StopAudit("EPISODE_GET_BAD_JSON") from None
            if not isinstance(item, dict):
                raise core.StopAudit("EPISODE_GET_BAD_JSON")
            return item
    except core.HTTPError:
        raise core.StopAudit("AIO_GET_NON_200") from None
    except core.URLError as err:
        if isinstance(err.reason, TimeoutError):
            raise core.StopAudit("EPISODE_GET_18S_TIMEOUT") from None
        raise core.StopAudit("EPISODE_GET_18S_TRANSPORT_UNAVAILABLE") from None
    except TimeoutError:
        raise core.StopAudit("EPISODE_GET_18S_TIMEOUT") from None
    except OSError:
        raise core.StopAudit("EPISODE_GET_18S_TRANSPORT_UNAVAILABLE") from None


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
    global PHASE, CATALOG_EVIDENCE_VALIDATED
    # A previous successful run must never cause another audit to report
    # stale catalog evidence after a later failure.
    CATALOG_EVIDENCE_VALIDATED = False
    PHASE = "READ_PRIVATE_SESSION"
    token,mapped=core.credential()
    PHASE = "CHECK_GATE_AND_GRAPH"
    core.bridge_gate()
    groups=visibility.verified_groups_from_bridge()
    deadline = time.monotonic()+DEADLINE_SECONDS
    read=core.make_reader(token,deadline)
    PHASE = "AUTHENTICATED_USER_PROBE"
    if read("/Users/Me").get("Id")!=mapped:
        raise core.StopAudit("MAPPED_USER_MISMATCH")
    PHASE = "READ_DOUBLE_NEXTUP"
    window1=read_bounded_nextup(token,deadline)
    window2=read_bounded_nextup(token,deadline)
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
    # Catalog binding is a separate and already completed evidence gate.
    # Individual Item resolution remains a second independent check.
    CATALOG_EVIDENCE_VALIDATED = True
    # Only one exact preexisting episode item is GET-resolved. PlaybackInfo
    # and media streams are intentionally never requested.
    PHASE = "GET_EXISTING_EPISODE"
    detail_item=read_existing_episode_detail(token,nextup_ep,deadline)
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
    print("RC3I_NEXTUP_AUDIT_CALL_TIMEOUT_S="+str(NEXTUP_TIMEOUT_SECONDS))
    print("RC3I_OTHER_GET_CALL_TIMEOUT_S="+str(core.MAX_CALL_SECONDS))
    print("RC3I_VERIFIED_CANONICAL_ONLY=YES")
    print("RC3I_SERIES_METADATA_LOOKUPS="+str(len(series_ids)))
    print("RC3I_SERIES_EPISODE_PAGES="+str(len(first_pages)))
    print("RC3I_SERIES_EPISODES_VERIFIED="+str(len(catalog)))
    print("RC3I_CATALOG_DOUBLE_READ=IDENTICAL")
    print("RC3I_NEXTUP_EPISODE_IN_CATALOG=YES")
    print("RC3I_EXISTING_EPISODE_ITEM_GET=VERIFIED")
    print("RC3I_CATALOG_BINDING_GATE=VERIFIED_SINGLE_SERIES")
    print("RC3I_EXISTING_EPISODE_GET_CALL_TIMEOUT_S="+str(EPISODE_DETAIL_TIMEOUT_SECONDS))
    print("RC3I_OTHER_GET_CALL_TIMEOUT_S="+str(core.MAX_CALL_SECONDS))
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
    except Exception as error:
        print("RC3I_RESULT=STOP_CATALOG_UNQUALIFIED")
        print("RC3I_FAILURE_PHASE=" + (PHASE if PHASE in PHASES else "UNKNOWN_PHASE"))
        print("RC3I_FAILURE_REASON=" + safe_stop_code(error))
        print("RC3I_CATALOG_BINDING_GATE=" + (
            "VERIFIED_SINGLE_SERIES" if CATALOG_EVIDENCE_VALIDATED
            else "NOT_YET_VERIFIED"
        ))
        print("RC3I_EXISTING_EPISODE_GET=" + (
            "UNQUALIFIED" if PHASE == "GET_EXISTING_EPISODE"
            else "NOT_TESTED"
        ))
        print("RC3I_GLOBAL_NEXTUP_QUALIFIED=NO")
        print("RC3I_MEDIA_PLAYBACK_PROVEN=NO")
        # Cannot infer status of external operator changes after a stop.
        # Re-evaluate the no-filter invariant with a separate READ-ONLY
        # status check; NEVER report OFF if that check is unavailable.
        try:
            core.bridge_gate()
            print("RC3I_FILTER_ON_STOP=OFF")
        except Exception:
            print("RC3I_FILTER_ON_STOP=UNVERIFIED")
        print("RC3I_SECRETS_PRINTED=NO")
        print("RC3I_PRODUCTION_CHANGES=0")
        raise SystemExit(2)
