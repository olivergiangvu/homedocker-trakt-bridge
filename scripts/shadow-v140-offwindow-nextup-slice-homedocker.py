#!/usr/bin/env python3
"""Release Recovery / Vertical Slice 1: REAL off-window NextUp item, shadow-only.

Not imported into production. Selects ONE rank>200 canonical IMDb candidate
from a doubly verified EXACT AIO watch-sink, using existing watch-state title
snapshot. Search/Hints obtains SeriesId FROM THE SERVER (not synthesized).
GET /Items/{SeriesId} must confirm exact IMDb. GET /Shows/NextUp?SeriesId=...
must return an actual Episode ID also found in ONE bounded per-series
catalog page and resolved again by /Items/{EpisodeId}.

Requires the earlier, root-only RC3-N2 proven runtime dependencies under
/run/homedocker-r4/; the release branch deliberately does NOT incorporate
the 90-commit research PR. A failing dependency, unsupported server search,
missing episode or slow response is a clean STOP, not a fallback to ID minting.

AIO requests ONLY to http://127.0.0.1:3240/jellyfin, authenticated GET,
proxy/redirects disabled. Never print IDs, titles, search query, token, URLs,
raw JSON, playback offsets or timestamps. No watched writes. Never serves
clients. Gateway RC2 / Filter OFF are checked at entry and exit.
"""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, ProxyHandler, build_opener

ROOT=Path("/run/homedocker-r4")
DEPENDENCY="audit-v140-rc3-source-anchor-drop-homedocker.py"
AIO="http://127.0.0.1:3240/jellyfin"
MAX_HTTP=8
MAX_BYTES=1024*1024
MAX_SECONDS=135
CALL_SECONDS=18
MAX_LOOKUPS=16
MAX_HINTS=3
MAX_EPISODES=100
IMDB=re.compile(r"tt[0-9]{7,12}\Z")
JELLYFIN_ID=re.compile(r"[0-9a-fA-F]{32}\Z")
PHASE="INIT"
ALLOWED=frozenset({"INIT","MAP_USER","SOURCE","SOURCE_TITLE","SEARCH",
                   "VERIFY_SERIES","SERIES_NEXTUP","SERIES_CATALOG",
                   "VERIFY_EPISODE","VERIFY_SNAPSHOT","FINAL_GATE"})
REASONS=frozenset({
    "NO_VALID_OFFWINDOW_TITLE","SNAPSHOT_CHANGED","SNAPSHOT_ROWS_UNAVAILABLE",
    "SOURCE_NOT_VERIFIED","AUTH_USER_MISMATCH","API_TIMEOUT_OR_TRANSPORT",
    "API_STATUS_NOT_200","API_INVALID_JSON","API_BODY_TOO_LARGE",
    "API_CALL_BUDGET","API_TIME_BUDGET",
    "SEARCH_NO_SERVER_ISSUED_SERIES_ID",
    "SERIES_IDENTITY_UNVERIFIED","NEXTUP_NO_QUALIFIED_EPISODE",
    "CATALOG_NOT_COMPLETE","NEXTUP_EPISODE_NOT_IN_CATALOG",
    "EPISODE_IDENTITY_UNVERIFIED",
})
CALLS=0
END=None


class StopSlice(Exception):
    pass


def stop(code):
    raise StopSlice(code)


def load_proven_source():
    # Only preinstalled root-private script and its root-private dependencies.
    info=ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 \
            or stat.S_IMODE(info.st_mode)!=0o700:
        stop("SOURCE_NOT_VERIFIED")
    path=ROOT/DEPENDENCY
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 \
            or stat.S_IMODE(info.st_mode)!=0o600:
        stop("SOURCE_NOT_VERIFIED")
    spec=importlib.util.spec_from_file_location("recovery_n2",path)
    if spec is None or spec.loader is None:
        stop("SOURCE_NOT_VERIFIED")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRedirect(__import__("urllib.request",fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def reader_factory(token):
    opener=build_opener(ProxyHandler({}),NoRedirect())
    def read(path):
        global CALLS
        if CALLS>=MAX_HTTP:
            stop("API_CALL_BUDGET")
        remaining=END-time.monotonic()
        if remaining<=0:
            stop("API_TIME_BUDGET")
        CALLS+=1
        req=Request(AIO+path,headers={
            "X-Emby-Token":token,"Accept":"application/json",
            "Accept-Encoding":"identity","User-Agent":"homedocker-release-shadow/1",
        },method="GET")
        try:
            with opener.open(req,timeout=min(CALL_SECONDS,remaining)) as response:
                if response.status!=200:stop("API_STATUS_NOT_200")
                raw=response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:stop("API_BODY_TOO_LARGE")
                obj=json.loads(raw)
                if not isinstance(obj,dict):stop("API_INVALID_JSON")
                return obj
        except HTTPError:
            stop("API_STATUS_NOT_200")
        except (URLError,TimeoutError,OSError):
            stop("API_TIMEOUT_OR_TRANSPORT")
        except (ValueError,UnicodeError):
            stop("API_INVALID_JSON")
    return read


def take_candidates(index,anchor_data):
    keys=[x for x,_ in index["recent"]]
    anchors={a[0]:a for a in anchor_data["anchors"]}
    selected=[]
    for rank,key in enumerate(keys,1):
        if rank<=200:continue
        if not key.startswith("s|") or not IMDB.fullmatch(key[2:]):
            continue
        a=anchors.get(key)
        if a is None:stop("SOURCE_NOT_VERIFIED")
        # Avoid redirect/drop semantics until an actual justified client test.
        if a[1] not in (None,""):
            continue
        selected.append((rank,key,a))
        if len(selected)==MAX_LOOKUPS:break
    return selected


def selected_snapshot(n2,index,anchor_data):
    # A capped subset of the previously validated candidate rows, no fanout.
    candidates=take_candidates(index,anchor_data)
    if not candidates:stop("NO_VALID_OFFWINDOW_TITLE")
    itemkeys=[x[2][6] for x in candidates]
    scope=index["scope"]
    markers=",".join("?" for _ in itemkeys)
    with closing(n2.identity.ro(n2.identity.AIO_DB)) as db:
        db.execute("BEGIN")
        rows=db.execute(
            "SELECT item_key,series_key,sort_at,media_type,snapshot "
            "FROM watch_state WHERE uuid=? AND persona=? AND item_key IN ("
            +markers+")",(*scope,*itemkeys)
        ).fetchall()
    if len(rows)!=len(candidates):
        stop("SNAPSHOT_ROWS_UNAVAILABLE")
    mapped={row[0]:row for row in rows}
    if len(mapped)!=len(rows):
        stop("SNAPSHOT_ROWS_UNAVAILABLE")
    for rank,key,a in candidates:
        row=mapped.get(a[6])
        if row is None or row[1]!=key or row[2]!=a[3] or row[3]!=a[2]:
            stop("SNAPSHOT_CHANGED")
        try:
            payload=json.loads(row[4]) if row[4] and len(row[4])<=16384 else None
        except (ValueError,TypeError):
            payload=None
        if not isinstance(payload,dict):continue
        title=payload.get("seriesName")
        if not isinstance(title,str) or not 4<=len(title.strip())<=96:
            continue
        if any(ord(c)<32 for c in title):
            continue
        return (rank,key,a,title.strip(),tuple(row))
    stop("NO_VALID_OFFWINDOW_TITLE")


def verify_search_and_nextup(read, imdb, title):
    # ONE source title -> ONE Search/Hints GET -> up to THREE real server IDs.
    hints=read("/Search/Hints?"+urlencode({
        "SearchTerm":title,"Limit":"5","IncludeItemTypes":"Series"
    }))
    possible=hints.get("SearchHints")
    if not isinstance(possible,list) or len(possible)>5:
        stop("SEARCH_NO_SERVER_ISSUED_SERIES_ID")
    issued=[]
    for hint in possible:
        if not isinstance(hint,dict) or hint.get("Type")!="Series":
            continue
        ident=hint.get("ItemId") or hint.get("Id")
        if isinstance(ident,str) and JELLYFIN_ID.fullmatch(ident) and ident not in issued:
            issued.append(ident)
        if len(issued)==MAX_HINTS:break
    if not issued:stop("SEARCH_NO_SERVER_ISSUED_SERIES_ID")
    sid=None
    for ident in issued:
        obj=read("/Items/"+quote(ident,safe=""))
        ids=obj.get("ProviderIds") if isinstance(obj.get("ProviderIds"),dict) else {}
        if obj.get("Type")=="Series" and obj.get("Id")==ident \
                and isinstance(ids.get("Imdb"),str) \
                and ids["Imdb"].lower()==imdb:
            sid=ident
            break
    if sid is None:stop("SERIES_IDENTITY_UNVERIFIED")
    page=read("/Shows/NextUp?"+urlencode({"SeriesId":sid,"Limit":"1"}))
    cards=page.get("Items")
    if not isinstance(cards,list) or len(cards)!=1:
        stop("NEXTUP_NO_QUALIFIED_EPISODE")
    item=cards[0]
    if not isinstance(item,dict) or item.get("Type")!="Episode" \
            or item.get("SeriesId")!=sid \
            or not isinstance(item.get("Id"),str) \
            or not JELLYFIN_ID.fullmatch(item["Id"]) \
            or item.get("UserData",{}).get("Played") is True:
        stop("NEXTUP_NO_QUALIFIED_EPISODE")
    eid=item["Id"]
    catalog=read("/Shows/"+quote(sid,safe="")+"/Episodes?"+
                 urlencode({"StartIndex":"0","Limit":str(MAX_EPISODES)}))
    rows=catalog.get("Items")
    count=catalog.get("TotalRecordCount")
    if not isinstance(rows,list) or type(count)!=int \
            or count>MAX_EPISODES or count!=len(rows) \
            or catalog.get("StartIndex")!=0:
        stop("CATALOG_NOT_COMPLETE")
    if not any(isinstance(row,dict) and row.get("Id")==eid
               and row.get("SeriesId")==sid for row in rows):
        stop("NEXTUP_EPISODE_NOT_IN_CATALOG")
    resolved=read("/Items/"+quote(eid,safe=""))
    if resolved.get("Type")!="Episode" or resolved.get("Id")!=eid \
            or resolved.get("SeriesId")!=sid:
        stop("EPISODE_IDENTITY_UNVERIFIED")
    return {"series_verified":True,"episode_verified":True,
            "catalog_episode_count":count}


def main():
    global PHASE,END
    END=time.monotonic()+MAX_SECONDS
    PHASE="MAP_USER"
    n2=load_proven_source()
    token,mapped=n2.shadow.credential()
    n2.shadow.bridge_gate()
    read=reader_factory(token)
    if read("/Users/Me").get("Id")!=mapped:
        stop("AUTH_USER_MISMATCH")
    PHASE="SOURCE"
    index,anchor_data=n2.source_gate_and_anchors()
    n2.summarize(index,anchor_data)
    PHASE="SOURCE_TITLE"
    rank,key,anchor,title,before=selected_snapshot(n2,index,anchor_data)
    PHASE="SEARCH"
    # Server must return the actual ID; never infer a packed ID from key.
    result=verify_search_and_nextup(read,key[2:],title)
    PHASE="VERIFY_SNAPSHOT"
    rank2,key2,a2,title2,after=selected_snapshot(n2,index,anchor_data)
    if (rank,key,anchor,title,before)!=(rank2,key2,a2,title2,after):
        stop("SNAPSHOT_CHANGED")
    PHASE="FINAL_GATE"
    n2.shadow.bridge_gate()
    print("R5SLICE_RESULT=REAL_OFFWINDOW_NEXTUP_EPISODE_VERIFIED")
    print("R5SLICE_SOURCE_RANK_OUTSIDE_200=YES")
    print("R5SLICE_SERVER_ISSUED_SERIES_ID=VERIFIED")
    print("R5SLICE_SERIES_NEXTUP_EPISODE=VERIFIED")
    print("R5SLICE_EPISODE_ID_PRESENT_IN_CATALOG=YES")
    print("R5SLICE_CATALOG_EPISODE_COUNT="+str(result["catalog_episode_count"]))
    print("R5SLICE_HTTP_GET_CALLS="+str(CALLS))
    print("R5SLICE_GLOBAL_NEXTUP_PAGING=UNPROVEN")
    print("R5SLICE_STREAM_PLAYBACK=UNPROVEN")
    print("R5SLICE_CLIENT_RESPONSE_CHANGED=NO")
    print("R5SLICE_GATEWAY_FILTER=OFF")
    print("R5SLICE_SQLITE=MODE_RO_QUERY_ONLY")
    print("R5SLICE_PRODUCTION_CHANGES=0")
    print("R5SLICE_SECRETS_PRINTED=NO")


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        reason=str(exc) if isinstance(exc,StopSlice) else ""
        print("R5SLICE_RESULT=STOP")
        print("R5SLICE_FAILURE_PHASE="+(
            PHASE if PHASE in ALLOWED else "UNQUALIFIED_PHASE"))
        print("R5SLICE_FAILURE_REASON="+(
            reason if reason in REASONS else "SOURCE_OR_RUNTIME_UNQUALIFIED"))
        try:
            n2.shadow.bridge_gate()
            print("R5SLICE_GATEWAY_FILTER_ON_STOP=OFF")
        except Exception:
            print("R5SLICE_GATEWAY_FILTER_ON_STOP=UNVERIFIED")
        print("R5SLICE_PRODUCTION_CHANGES=0")
        print("R5SLICE_SECRETS_PRINTED=NO")
        raise SystemExit(2)
