#!/usr/bin/env python3
"""B1V controlled LIVE read-only comparison of AIO and existing Gateway canary.

Default: only GET health on fixed loopback AIO/canary endpoints.
--compare --confirm LIVE_AUTH_LOGIN_GET_ONLY: hidden AIO/Jellyfin password.
  Performs ONE local POST /Users/AuthenticateByName, then GET-only comparison.
--existing-token --confirm LIVE_READONLY_GET_ONLY: opt-in old token mode.
--include-resume: additionally compare GET Resume source/canary (NO writes).
Password login may create an AIO server-side session; never claims pure GET-only.
No playback/history writes, Trakt, nginx, Docker, SQLite, credentials on disk.
All source/canary rows and IDs remain in process memory; print aggregates only.
AIO GET handlers may update their own caches. This is NOT client cutover.
"""
import argparse
import getpass
import json
import re
import sys
from urllib import error, request

sys.dont_write_bytecode = True
AIO = "http://127.0.0.1:3240/jellyfin"
GATEWAY = "http://127.0.0.1:17141"
NEXTUP = "/Shows/NextUp?Limit=20"
RESUME = "/UserItems/Resume?StartIndex=0&Limit=20"
PATHS = frozenset(("/health", "/Users/Me", NEXTUP, RESUME))
AUTH = re.compile(r"[^\s\x00-\x1f\x7f]{16,1024}\Z")
USER = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
MAX_BYTES = 2 * 1024 * 1024
AUTH_MAX_BYTES = 16384
AUTH_HEADER = ('MediaBrowser Client="HomeDockerB1VReadOnly",'
               'Device="SSH",DeviceId="b1v-audit",Version="1.0.0"')
OPENER = request.build_opener(request.ProxyHandler({}),
    type("NoRedirect", (request.HTTPRedirectHandler,), {
        "redirect_request": lambda self, req, fp, code, msg, headers, url: None
    })())

class Stop(Exception):
    pass

def login_with_username_password(username, password, opener=OPENER):
    """A SINGLE local Jellyfin authentication POST; never log the response.

    AIOStreams 'plain server address' uses configuration UUID/alias as
    username, or '<uuid-or-alias>/<household-name>' for a household user.
    The password is that configuration password (optional '/PIN' suffix).
    A successful call may CREATE a session on AIOStreams.
    """
    if (not isinstance(username,str) or not username or len(username)>256
        or any(ord(ch)<32 or ord(ch)==127 for ch in username)
        or not isinstance(password,str) or not password or len(password)>1024
        or any(ord(ch)<32 or ord(ch)==127 for ch in password)):
        raise Stop("INVALID_CREDENTIAL_INPUT")
    body=json.dumps({"Username":username,"Pw":password},
                    ensure_ascii=False).encode("utf-8")
    if len(body)>8192:
        raise Stop("CREDENTIAL_PAYLOAD_TOO_LARGE")
    req=request.Request(AIO+"/Users/AuthenticateByName",data=body,
                        method="POST",headers={
                           "Accept":"application/json",
                           "Accept-Encoding":"identity",
                           "Content-Type":"application/json",
                           "X-Emby-Authorization":AUTH_HEADER,
                        })
    try:
        with opener.open(req,timeout=10) as resp:
            if resp.status != 200: raise Stop("AUTH_HTTP_UNEXPECTED_STATUS")
            raw=resp.read(AUTH_MAX_BYTES+1)
            if len(raw)>AUTH_MAX_BYTES:
                raise Stop("AUTH_RESPONSE_TOO_LARGE")
    except error.HTTPError as exc:
        if exc.code in (400,401,403,404,405,429):
            raise Stop("AUTH_HTTP_"+str(exc.code)) from None
        raise Stop("AUTH_HTTP_OTHER") from None
    except (error.URLError,OSError,TimeoutError):
        raise Stop("AUTH_LOOPBACK_UNAVAILABLE") from None
    data=decoded(raw,"AUTH")
    token=data.get("AccessToken")
    user=data.get("User") or {}
    user_id=user.get("Id") if isinstance(user,dict) else None
    if not isinstance(token,str) or not AUTH.fullmatch(token):
        raise Stop("AUTH_ACCESS_TOKEN_NOT_RETURNED")
    if not isinstance(user_id,str) or not USER.fullmatch(user_id):
        raise Stop("AUTH_USER_ID_NOT_RETURNED")
    # Return only in memory; the caller never prints either.
    return token,user_id

def fixed_get(origin, path, token=None):
    if origin not in ("aio", "gateway") or path not in PATHS:
        raise Stop("UNAPPROVED_NETWORK_TARGET")
    if path == "/Users/Me" and origin != "aio":
        raise Stop("UNAPPROVED_NETWORK_TARGET")
    if path == "/health" and token is not None:
        raise Stop("TOKEN_NOT_NEEDED_FOR_HEALTH")
    root = AIO if origin == "aio" else GATEWAY
    if path == "/health" and origin == "aio":
        path = "/System/Info/Public"  # official AIO Jellyfin unauthenticated probe
    if token is not None and not AUTH.fullmatch(token):
        raise Stop("INVALID_HIDDEN_SESSION_TOKEN")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if token is not None:
        headers["X-Emby-Token"] = token
        headers["X-Emby-Authorization"] = AUTH_HEADER
    req = request.Request(root + path, headers=headers, method="GET")
    try:
        with OPENER.open(req, timeout=20 if path == NEXTUP else 10) as rsp:
            status = rsp.status
            raw = rsp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: raise Stop("RESPONSE_TOO_LARGE")
            if status != 200: raise Stop("UPSTREAM_UNEXPECTED_STATUS")
    except error.HTTPError as exc:
        if path == RESUME and exc.code == 404:
            return 404, b""
        raise Stop("HTTP_STATUS_" + str(exc.code)
                   if exc.code in (400,401,403,404,405,429,500,502,503)
                   else "HTTP_STATUS_OTHER") from None
    except (error.URLError, OSError, TimeoutError):
        raise Stop("LOOPBACK_HTTP_UNAVAILABLE") from None
    return status, raw

def decoded(data, purpose):
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError, TypeError):
        raise Stop("INVALID_"+purpose+"_JSON") from None
    if not isinstance(obj, dict):
        raise Stop("INVALID_"+purpose+"_OBJECT")
    return obj

def rows(obj, label):
    items = obj.get("Items")
    if not isinstance(items, list) or len(items) > 50:
        raise Stop("INVALID_"+label+"_ITEMS")
    if any(not isinstance(x, dict) or not isinstance(x.get("Id"),str)
           or not x["Id"] for x in items):
        raise Stop("INVALID_"+label+"_ITEM_ID")
    ids = [x["Id"] for x in items]
    if len(ids) != len(set(ids)):
        raise Stop("DUPLICATE_"+label+"_ITEM_ID")
    return items

def subset(source, projected):
    before = rows(source, "SOURCE")
    after = rows(projected, "GATEWAY")
    reference = {x["Id"]: x for x in before}
    if any(x["Id"] not in reference or x != reference[x["Id"]] for x in after):
        raise Stop("GATEWAY_MODIFIED_SOURCE_ITEM_OR_POSITION")
    if len(after) > len(before):
        raise Stop("GATEWAY_ADDED_ITEM")
    # Preserve both the opaque top-level envelope and each user's row,
    # except the count fields explicitly adjusted when the view is filtered.
    # For unchanged views the COMPLETE JSON response must be unchanged.
    if len(after) == len(before):
        if projected != source:
            raise Stop("GATEWAY_UNEXPECTED_UNFILTERED_MUTATION")
    else:
        if projected.get("TotalRecordCount") != len(after):
            raise Stop("GATEWAY_TOTAL_COUNT_MISMATCH")
        allowed = {"Items","TotalRecordCount","Count"}
        if any(projected.get(k) != v for k,v in source.items()
               if k not in allowed):
            raise Stop("GATEWAY_CHANGED_OPAQUE_ENVELOPE")
        if set(projected) != set(source):
            raise Stop("GATEWAY_CHANGED_ENVELOPE_KEYS")
        if "Count" in source and projected.get("Count") != len(after):
            raise Stop("GATEWAY_COUNT_MISMATCH")
    return {"source_items":len(before), "gateway_items":len(after),
            "removed_in_projection":len(before)-len(after),
            "source_item_data_preserved":True}

def safe_compare(source_get, gateway_get, include_resume=False, expected_user_id=None):
    # Both arguments are injected GET-only functions for testability.
    status, raw_user = source_get("/Users/Me")
    if status != 200: raise Stop("SOURCE_USER_IDENTITY_NOT_AUTHORIZED")
    me = decoded(raw_user, "USER")
    if not USER.fullmatch(str(me.get("Id") or "")):
        raise Stop("SOURCE_USER_ID_INVALID")
    if expected_user_id is not None and me["Id"] != expected_user_id:
        raise Stop("AUTH_LOGIN_USER_MISMATCH")
    status, pre = source_get(NEXTUP)
    if status != 200: raise Stop("SOURCE_NEXTUP_UNAVAILABLE")
    g_status, projected = gateway_get(NEXTUP)
    if g_status != 200: raise Stop("GATEWAY_NEXTUP_UNAVAILABLE")
    status, post = source_get(NEXTUP)
    if status != 200: raise Stop("SOURCE_NEXTUP_RECHECK_UNAVAILABLE")
    pre_json, post_json = decoded(pre,"NEXTUP"), decoded(post,"NEXTUP")
    observed = {
        "audit": "R23D2B1V_CONTROLLED_LIVE_READONLY_GATEWAY_COMPARISON",
        "aio_session_user_verified": True,
        "source_nextup_stable_during_probe": pre_json == post_json,
        "nextup_result": "INCONCLUSIVE_SOURCE_CHANGED",
        "resume_result": "NOT_REQUESTED",
        "aio_get_handlers_may_refresh_cache": True,
        "authentication_post_requests": 0,
        "comparison_get_only": True,
        "history_or_playback_writes_requested": 0,
        "trakt_api_requests": 0,
        "docker_nginx_or_sqlite_changes": 0,
        "raw_user_ids_item_ids_positions_tokens_or_body_printed": False,
        "production_gateway_routing_confirmed": False,
        "actual_client_playback_or_scrobble_confirmed": False,
        "stable_v140_release_authorized": False,
    }
    if pre_json == post_json:
        observed.update(subset(pre_json,decoded(projected,"GATEWAY_NEXTUP")))
        observed["nextup_result"] = "SOURCE_ITEM_SUBSET_CONTRACT_PASS"
    if include_resume:
        status, pre = source_get(RESUME)
        g_status, via = gateway_get(RESUME)
        status2, post = source_get(RESUME)
        if status == 200 and status2 == 200 and g_status == 404:
            observed["resume_result"] = "GATEWAY_RESUME_DISABLED_404"
        elif (status == status2 == g_status == 200):
            obj_pre = decoded(pre,"RESUME")
            obj_post = decoded(post,"RESUME")
            rows(obj_pre, "RESUME_SOURCE")
            if obj_pre == obj_post:
                # Resume is RAW passthrough: byte-for-byte source body.
                if via != pre:
                    raise Stop("RESUME_GATEWAY_BODY_NOT_PASSTHROUGH")
                observed["resume_result"] = "RAW_BYTES_MATCH_STABLE_SOURCE"
                observed["resume_items"] = len(obj_pre["Items"])
            else:
                observed["resume_result"] = "INCONCLUSIVE_SOURCE_CHANGED"
        else:
            observed["resume_result"] = "INCONCLUSIVE_UPSTREAM_OR_GATEWAY_STATUS"
    return observed

def preflight(get=fixed_get):
    health={}
    for origin in ("aio","gateway"):
        status, data=get(origin,"/health")
        if status != 200:raise Stop("HEALTH_"+origin.upper()+"_NOT_200")
        obj=decoded(data,"HEALTH")
        # This confirms a loopback HTTP endpoint, NOT Docker ownership.
        if origin=="gateway" and obj.get("status")!="ok":
            raise Stop("GATEWAY_HEALTH_UNEXPECTED")
        health[origin+"_health_200"]=True
    return {"audit":"R23D2B1V_HEALTH_PREFLIGHT_ONLY",
        **health,"credentials_requested":False,
        "source_or_gateway_history_read":False,
        "production_identity_or_route_verified":False,
        "history_or_playback_writes_requested":0,
        "docker_nginx_or_sqlite_changes":0,
        "stable_v140_release_authorized":False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compare",action="store_true",
                   help="Log in using hidden AIO Jellyfin configuration password, then GET-only comparison")
    p.add_argument("--existing-token",action="store_true",
                   help="Compatibility mode: provide an existing session token instead of username/password")
    p.add_argument("--confirm",default="")
    p.add_argument("--include-resume",action="store_true",
                   help="Optional GET-only Resume comparison; disabled routes may return 404")
    a=p.parse_args()
    try:
        if a.include_resume and not a.compare:
            raise Stop("RESUME_REQUIRES_COMPARE")
        if a.existing_token and not a.compare:
            raise Stop("EXISTING_TOKEN_REQUIRES_COMPARE")
        if a.compare:
            required=("LIVE_READONLY_GET_ONLY" if a.existing_token
                      else "LIVE_AUTH_LOGIN_GET_ONLY")
            if a.confirm != required:
                raise Stop("EXPLICIT_LIVE_"+("TOKEN" if a.existing_token
                           else "USERNAME_PASSWORD_LOGIN")+"_CONFIRMATION_REQUIRED")
            preflight()
            if not sys.stdin.isatty():
                raise Stop("INTERACTIVE_SSH_TTY_REQUIRED")
            if a.existing_token:
                token=getpass.getpass(
                    "Existing AIO Jellyfin token (hidden, never saved): ").strip()
                if not AUTH.fullmatch(token):
                    raise Stop("INVALID_HIDDEN_SESSION_TOKEN")
                user_id=None
            else:
                print("AIO Jellyfin: username = configuration UUID/alias "
                      "(household user: alias/name).")
                username=input("AIO Jellyfin username/alias: ").strip()
                password=getpass.getpass("AIO configuration password (hidden): ")
                try:
                    token,user_id=login_with_username_password(username,password)
                finally:
                    del password,username
            try:
                result=safe_compare(
                    lambda path:fixed_get("aio",path,token),
                    lambda path:fixed_get("gateway",path,token),
                    include_resume=a.include_resume,
                    expected_user_id=user_id)
                result["authentication_mode"]=(
                    "EXISTING_TOKEN" if a.existing_token
                    else "USERNAME_PASSWORD_LOGIN")
                result["authentication_post_requests"]=(
                    0 if a.existing_token else 1)
                result["authentication_created_server_session"]=(
                    False if a.existing_token else True)
                result["credentials_saved_to_disk"]=False
            finally:
                del token
        else:
            result=preflight()
        print(json.dumps(result,indent=2,sort_keys=True))
        if result.get("nextup_result")=="INCONCLUSIVE_SOURCE_CHANGED":
            raise SystemExit(2)
    except (EOFError,KeyboardInterrupt):
        print("\nR23D2B1V_STOP_INTERACTIVE_CANCELLED",file=sys.stderr)
        raise SystemExit(5) from None
    except Stop as ex:
        print("R23D2B1V_STOP_"+str(ex),file=sys.stderr)
        raise SystemExit(5) from None

if __name__=="__main__":
    main()
