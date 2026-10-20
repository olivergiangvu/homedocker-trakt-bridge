#!/usr/bin/env python3
"""RC3-J: low-cost AIO NextUp transport-only diagnosis on HomeDocker.

Only the exact mapped-user /Users/Me and NextUp first 50 are requested.
1 NextUp GET with a 6s deadline. A timeout alone authorizes exactly ONE
more attempt with 12s. No metadata fan-out or catalog pages. No POST, no
write, no credentials/IDs/titles/URLs in output. Root-only read /run token.
Gateway must be running with Identity Filter OFF before and after.
"""
import errno
from http.client import IncompleteRead
import importlib.util
import json
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

HERE = Path(__file__).resolve().parent
SRC = HERE / "audit-v140-rc3-nextup-shadow-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3j_core",SRC)
if spec is None or spec.loader is None:
    raise RuntimeError("CORE_UNAVAILABLE")
core=importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

MAX_BODY = 1024*1024
OPENER = build_opener(ProxyHandler({}), core.NoRedirect())
NEXTUP = "/Shows/NextUp?StartIndex=0&Limit=50"
USER = "/Users/Me"


def network_class(exc):
    """Output only a fixed class, never raw transport exception details."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return "AUTH_REJECTED"
        return "HTTP_OTHER"
    if isinstance(exc, URLError):
        return network_class(exc.reason) if isinstance(exc.reason, BaseException) \
            else "TRANSPORT_OTHER"
    if isinstance(exc, OSError):
        if exc.errno == errno.ECONNREFUSED:
            return "CONNECTION_REFUSED"
        if exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return "NETWORK_UNREACHABLE"
        if exc.errno in (errno.ETIMEDOUT,):
            return "TIMEOUT"
        return "TRANSPORT_OTHER"
    if isinstance(exc, IncompleteRead):
        return "BODY_TRUNCATED"
    if isinstance(exc, (ValueError, UnicodeError)):
        return "JSON_INVALID"
    return "TRANSPORT_OTHER"


def read_get(token, path, timeout, opener=OPENER):
    if path not in (USER,NEXTUP):
        return ("PATH_DENIED",None,0)
    req=Request(core.AIO+path,method="GET",headers={
        "X-Emby-Token": token,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "homedocker-rc3j-get-diagnostic/1",
    })
    started=time.monotonic()
    try:
        with opener.open(req,timeout=timeout) as resp:
            if resp.status != 200:
                return ("HTTP_OTHER",None,int((time.monotonic()-started)*1000))
            raw=resp.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:
                return ("BODY_TOO_LARGE",None,int((time.monotonic()-started)*1000))
            # JSON parse failures used to appear as AIO_GET_UNAVAILABLE
            # in the existing shared reader, but are not network faults.
            try:
                parsed=json.loads(raw.decode("utf-8"))
            except (UnicodeError,ValueError):
                return ("JSON_INVALID",None,int((time.monotonic()-started)*1000))
            if not isinstance(parsed,dict):
                return ("JSON_CONTRACT_INVALID",None,int((time.monotonic()-started)*1000))
            return ("OK",parsed,int((time.monotonic()-started)*1000))
    except Exception as exc:
        return (network_class(exc),None,int((time.monotonic()-started)*1000))


def main():
    token,mapped=core.credential()
    core.bridge_gate()
    requests=0
    # The user identity is a cheap, explicit gate; never test another user's
    # NextUp with a token whose /Users/Me mapping cannot be verified.
    status,me,ms=read_get(token,USER,6)
    requests+=1
    if status!="OK" or me.get("Id")!=mapped:
        print("RC3J_STAGE=USER_AUTH")
        print("RC3J_USER_GET_CLASS="+status)
        print("RC3J_RESULT=STOP_USER_OR_TRANSPORT")
        return 2
    print("RC3J_USER=MAPPED")
    print("RC3J_USER_GET_MS="+str(ms))

    first,body,elapsed=read_get(token,NEXTUP,6)
    requests+=1
    print("RC3J_NEXTUP_GET_6S_CLASS="+first)
    print("RC3J_NEXTUP_GET_6S_MS="+str(elapsed))
    second="NOT_NEEDED"
    if first=="TIMEOUT":
        second,body,elapsed=read_get(token,NEXTUP,12)
        requests+=1
        print("RC3J_NEXTUP_GET_12S_MS="+str(elapsed))
    print("RC3J_NEXTUP_GET_12S_CLASS="+second)
    result=second if first=="TIMEOUT" else first
    if result=="OK":
        rows=body.get("Items")
        if not isinstance(rows,list) or len(rows)>50:
            print("RC3J_NEXTUP_SCHEMA=INVALID")
            print("RC3J_RESULT=STOP_SCHEMA")
            return 2
        print("RC3J_NEXTUP_ITEMS="+str(len(rows)))
        print("RC3J_NEXTUP_SCHEMA=BOUNDED")
    else:
        print("RC3J_RESULT=STOP_NEXTUP_"+result)
        print("RC3J_HTTP_GET_COUNT="+str(requests))
        return 2
    print("RC3J_HTTP_GET_COUNT="+str(requests))
    print("RC3J_RESULT=TRANSPORT_OK")
    return 0


if __name__=="__main__":
    code=2
    try:
        code=main()
    except Exception:
        print("RC3J_RESULT=STOP_LOCAL_UNQUALIFIED")
    finally:
        try:
            core.bridge_gate()
            print("RC3J_FILTER_FINAL=OFF")
        except Exception:
            print("RC3J_FILTER_FINAL=UNVERIFIED")
        print("RC3J_METHOD=LOCAL_AUTHENTICATED_GET_ONLY")
        print("RC3J_METADATA_LOOKUPS=0")
        print("RC3J_CLIENT_ROUTING_CHANGED=NO")
        print("RC3J_PRODUCTION_CHANGES=0")
        print("RC3J_SECRETS_PRINTED=NO")
    raise SystemExit(code)
