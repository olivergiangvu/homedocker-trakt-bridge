#!/usr/bin/env python3
"""Release Recovery: one backend-validated off-window NextUp episode.

The pinned AIO packed-a1 SERIES codec is deterministic (not a guessed ID):
16 bytes: 0xa1, (series kind=2 <<4)|imdb type=1, media code,
six-byte IMDb numeric, ffff season, ffff episode, three zero bytes.
A packed address is UNTRUSTED until SAME mapped user GET /Items/{id}
returns Series and matching ProviderIds.Imdb; then AIO /Shows/NextUp
must provide an actual Episode also resolved in a real series catalog.

Purely read-only operator shadow. Reuses the existing root-private RC3-N2
source/snapshot proof. No production imports, history writes, route changes,
new b2 IDs, title Search/Hints, foreign host, playback or printed identities.
"""
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
import importlib.util
import json
import re
import stat
import time

ROOT=Path("/run/homedocker-r4")
DEPENDENCY="audit-v140-rc3-source-anchor-drop-homedocker.py"
AIO="http://127.0.0.1:3240/jellyfin"
IMDB=re.compile(r"tt[0-9]{7,12}\Z")
JF_ID=re.compile(r"[0-9a-f]{32}\Z")
MEDIA_CODES={"series":2,"anime":3,"tv":4}
MAX48=(1<<48)-1
MAX_SAMPLES=3
MAX_GETS=10
MAX_SECONDS=125
CALL_SECONDS=18
MAX_BYTES=1024*1024
MAX_EPISODES=200
CALLS=0
END=0
SOURCE=None
PHASE="INIT"
PHASES=frozenset({"INIT","MAP_USER","SOURCE_SNAPSHOT","PACKED_SERIES_GET",
                  "PER_SERIES_NEXTUP","EPISODE_CATALOG","EPISODE_DETAIL",
                  "SOURCE_RECHECK","FINAL_GATE"})
REASONS=frozenset({
    "SOURCE_NOT_VERIFIED","AUTH_USER_MISMATCH",
    "NO_SAFE_SOURCE_CANDIDATE","NO_OFFWINDOW_ITEM_VERIFIED",
    "NO_OFFWINDOW_NEXTUP_VERIFIED","NO_VALID_EPISODE_CATALOG",
    "EPISODE_IDENTITY_UNVERIFIED","SOURCE_SNAPSHOT_CHANGED",
    "GET_BUDGET_EXCEEDED","TIME_BUDGET_EXCEEDED",
    "HTTP_STATUS_UNEXPECTED","HTTP_TRANSPORT_UNAVAILABLE",
    "HTTP_INVALID_JSON","HTTP_BODY_TOO_LARGE",
})
class StopSlice(Exception):pass
def stop(reason):raise StopSlice(reason)

def source_module():
    folder=ROOT.lstat()
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid!=0 \
            or stat.S_IMODE(folder.st_mode)!=0o700:
        stop("SOURCE_NOT_VERIFIED")
    path=ROOT/DEPENDENCY
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 \
            or stat.S_IMODE(info.st_mode)!=0o600:
        stop("SOURCE_NOT_VERIFIED")
    spec=importlib.util.spec_from_file_location("release_source",path)
    if not spec or not spec.loader:stop("SOURCE_NOT_VERIFIED")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def packed_series_id(raw,media):
    """Exact pinned AIO byte layout for k=series, IMDb-only; verify via GET."""
    if not isinstance(raw,str) or not IMDB.fullmatch(raw) \
            or media not in MEDIA_CODES:return None
    number=int(raw[2:])
    if number>MAX48 or "tt"+str(number).zfill(7)!=raw:return None
    buffer=bytearray(16)
    buffer[0]=0xa1
    buffer[1]=0x21
    buffer[2]=MEDIA_CODES[media]
    buffer[3:9]=number.to_bytes(6,"big")
    buffer[9:13]=b"\xff\xff\xff\xff"
    return buffer.hex()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

def reader(token):
    opener=build_opener(ProxyHandler({}),NoRedirect())
    def get(path,missing_ok=False):
        global CALLS
        if CALLS>=MAX_GETS:stop("GET_BUDGET_EXCEEDED")
        remain=END-time.monotonic()
        if remain<=0:stop("TIME_BUDGET_EXCEEDED")
        CALLS+=1
        req=Request(AIO+path,method="GET",headers={
            "X-Emby-Token":token,"Accept":"application/json",
            "Accept-Encoding":"identity","User-Agent":"homedocker-release-slice/2",
        })
        try:
            with opener.open(req,timeout=min(CALL_SECONDS,remain)) as response:
                if response.status!=200:stop("HTTP_STATUS_UNEXPECTED")
                raw=response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:stop("HTTP_BODY_TOO_LARGE")
                data=json.loads(raw)
                if not isinstance(data,dict):stop("HTTP_INVALID_JSON")
                return data
        except HTTPError as error:
            if missing_ok and error.code==404:return None
            stop("HTTP_STATUS_UNEXPECTED")
        except (URLError,TimeoutError,OSError):
            stop("HTTP_TRANSPORT_UNAVAILABLE")
        except (ValueError,UnicodeError):
            stop("HTTP_INVALID_JSON")
    return get

def select_candidates(index,anchors):
    ranking=[key for key,_ in index["recent"]]
    pairs={row[0]:row for row in anchors["anchors"]}
    if len(pairs)!=len(ranking):stop("SOURCE_NOT_VERIFIED")
    chosen=[]
    for rank,key in enumerate(ranking,1):
        if rank<=200 or not isinstance(key,str) or not key.startswith("s|"):
            continue
        row=pairs.get(key)
        if row is None:stop("SOURCE_NOT_VERIFIED")
        if row[1] not in (None,""):continue
        series=packed_series_id(key[2:],row[2])
        if series:chosen.append((key[2:],series))
        if len(chosen)>=MAX_SAMPLES:break
    if not chosen:stop("NO_SAFE_SOURCE_CANDIDATE")
    return chosen

def try_episode(get,imdb,series_id):
    """Backend identity must match before per-series NextUp/catalog."""
    global PHASE
    PHASE="PACKED_SERIES_GET"
    obj=get("/Items/"+series_id,missing_ok=True)
    if obj is None:return None
    providers=obj.get("ProviderIds")
    if not isinstance(providers,dict):return None
    if obj.get("Type")!="Series" or obj.get("Id")!=series_id \
            or str(providers.get("Imdb","")).lower()!=imdb:
        return None
    PHASE="PER_SERIES_NEXTUP"
    nextup=get("/Shows/NextUp?"+urlencode({"SeriesId":series_id,"Limit":"1"}))
    cards=nextup.get("Items")
    if not isinstance(cards,list) or len(cards)>1:
        stop("NO_OFFWINDOW_NEXTUP_VERIFIED")
    if not cards:return None
    card=cards[0]
    if not isinstance(card,dict):stop("NO_OFFWINDOW_NEXTUP_VERIFIED")
    data=card.get("UserData")
    ep_id=card.get("Id")
    if card.get("Type")!="Episode" or card.get("SeriesId")!=series_id \
            or not isinstance(ep_id,str) or not JF_ID.fullmatch(ep_id) \
            or not isinstance(data,dict) or data.get("Played") is not False:
        stop("NO_OFFWINDOW_NEXTUP_VERIFIED")
    params={"StartIndex":"0","Limit":str(MAX_EPISODES)}
    season=card.get("ParentIndexNumber")
    if type(season)==int and 0<=season<65535:params["Season"]=str(season)
    PHASE="EPISODE_CATALOG"
    catalog=get("/Shows/"+series_id+"/Episodes?"+urlencode(params))
    rows=catalog.get("Items")
    total=catalog.get("TotalRecordCount")
    if not isinstance(rows,list) or type(total)!=int or total<0 \
            or total>MAX_EPISODES or total!=len(rows) \
            or catalog.get("StartIndex")!=0:
        stop("NO_VALID_EPISODE_CATALOG")
    if not any(isinstance(x,dict) and x.get("Id")==ep_id \
            and x.get("SeriesId")==series_id for x in rows):
        stop("NO_VALID_EPISODE_CATALOG")
    PHASE="EPISODE_DETAIL"
    detail=get("/Items/"+ep_id)
    if detail.get("Type")!="Episode" or detail.get("Id")!=ep_id \
            or detail.get("SeriesId")!=series_id:
        stop("EPISODE_IDENTITY_UNVERIFIED")
    return total

def main():
    global SOURCE,END,PHASE
    END=time.monotonic()+MAX_SECONDS
    PHASE="MAP_USER"
    SOURCE=source_module()
    token,mapped=SOURCE.shadow.credential()
    SOURCE.shadow.bridge_gate()
    get=reader(token)
    if get("/Users/Me").get("Id")!=mapped:stop("AUTH_USER_MISMATCH")
    PHASE="SOURCE_SNAPSHOT"
    index,anchors=SOURCE.source_gate_and_anchors()
    SOURCE.summarize(index,anchors)
    candidates=select_candidates(index,anchors)
    original=(index,anchors)
    attempts=0
    for imdb,series_id in candidates:
        attempts+=1
        count=try_episode(get,imdb,series_id)
        if count is None:continue
        PHASE="SOURCE_RECHECK"
        if SOURCE.source_gate_and_anchors()!=original:
            stop("SOURCE_SNAPSHOT_CHANGED")
        PHASE="FINAL_GATE"
        SOURCE.shadow.bridge_gate()
        print("R5SLICE_RESULT=OFFWINDOW_BACKEND_VALIDATED_EPISODE")
        print("R5SLICE_RANK_OUTSIDE_200=YES")
        print("R5SLICE_ID_ORIGIN=PINNED_STATELESS_A1_CODEC")
        print("R5SLICE_SERIES_ID_BACKEND_VALIDATED=YES")
        print("R5SLICE_PROVIDER_IMDB_EXACT=YES")
        print("R5SLICE_NEXTUP_EPISODE_IN_CATALOG=YES")
        print("R5SLICE_CATALOG_EPISODES="+str(count))
        print("R5SLICE_CANDIDATES_TESTED="+str(attempts))
        print("R5SLICE_HTTP_GET_COUNT="+str(CALLS))
        print("R5SLICE_GLOBAL_PAGINATION=NOT_PROVEN")
        print("R5SLICE_PLAYBACK=NOT_PROVEN")
        print("R5SLICE_CLIENT_RESPONSE_CHANGED=NO")
        print("R5SLICE_FILTER_FINAL=OFF")
        print("R5SLICE_SQLITE=MODE_RO_QUERY_ONLY")
        print("R5SLICE_PRODUCTION_CHANGES=0")
        print("R5SLICE_SECRETS_PRINTED=NO")
        return
    PHASE="SOURCE_RECHECK"
    if SOURCE.source_gate_and_anchors()!=original:
        stop("SOURCE_SNAPSHOT_CHANGED")
    stop("NO_OFFWINDOW_ITEM_VERIFIED")

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        reason=str(exc) if isinstance(exc,StopSlice) else ""
        print("R5SLICE_RESULT=STOP")
        print("R5SLICE_FAILURE_PHASE="+(PHASE if PHASE in PHASES else "UNKNOWN"))
        print("R5SLICE_FAILURE_REASON="+(
            reason if reason in REASONS else "SOURCE_OR_RUNTIME_UNQUALIFIED"))
        try:
            if SOURCE is None:raise StopSlice("SOURCE_NOT_VERIFIED")
            SOURCE.shadow.bridge_gate()
            print("R5SLICE_FILTER_ON_STOP=OFF")
        except Exception:
            print("R5SLICE_FILTER_ON_STOP=UNVERIFIED")
        print("R5SLICE_HTTP_GET_COUNT="+str(CALLS))
        print("R5SLICE_PRODUCTION_CHANGES=0")
        print("R5SLICE_SECRETS_PRINTED=NO")
        raise SystemExit(2)
