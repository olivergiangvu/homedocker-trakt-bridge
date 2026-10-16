#!/usr/bin/env python3
"""R23B-2 EXPERIMENTAL: GET-only AIO Jellyfin localhost relay via Unix socket.

NO Docker changes, installs, nginx changes, Trakt calls or history writes.
Two *separate* processes required:
- host: Unix socket in an explicitly provisioned private host directory.
- sidecar: TCP 127.0.0.1:9999 inside the *existing canary's* network namespace.
Must NOT be used as a standalone production service without authorization,
Docker/UID/socket permission audit, lifecycle control and host acceptance.
"""
import argparse
import http.client
import os
import re
import socket
import socketserver
import stat
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

UPSTREAM_HOST="127.0.0.1"
UPSTREAM_PORT=3240
SOCKET_PATH="/run/homedocker-r23b2-aio/readonly.sock"
SIDECAR_BIND="127.0.0.1"
SIDECAR_PORT=9999
MAX_BODY=2*1024*1024
MAX_QUERY=1024
MAX_AUTH=2048
ALLOW_HEADERS=("x-emby-token","x-mediabrowser-token",
               "x-emby-authorization","authorization")
OTHER_HEADERS=("accept",)
ITEM=re.compile(r"/jellyfin/Items/[A-Za-z0-9_-]{1,128}\Z")
LEGACY_RESUME=re.compile(r"/jellyfin/Users/[A-Za-z0-9_-]{8,128}/Items/Resume\Z")
SAFE_QUERIES=frozenset(("Limit","StartIndex","SeriesId","ParentId","UserId",
                        "Fields","EnableUserData","MediaTypes"))

def route_allowed(path, allow_resume=False):
    if not isinstance(path,str) or len(path)>1536 or "#" in path:
        return False
    try:
        target=urlsplit(path)
        if target.scheme or target.netloc or target.fragment or "%" in target.path:
            return False
        qs=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,
                     max_num_fields=16)
        if len(target.query)>MAX_QUERY or any(
             key not in SAFE_QUERIES or len(value)>160 for key,value in qs):
            return False
    except ValueError:
        return False
    p=target.path
    if p in ("/jellyfin/Shows/NextUp","/jellyfin/Users/Me") or ITEM.fullmatch(p):
        return True
    if allow_resume and (p=="/jellyfin/UserItems/Resume" or LEGACY_RESUME.fullmatch(p)):
        return True
    return False

def filtered_headers(raw):
    # Per-hop authenticated session; never log the token and never forward
    # the rest of the request headers/cookies/host/proxy headers.
    h={}
    for key in ALLOW_HEADERS + OTHER_HEADERS:
        value=raw.get(key)
        if value is not None:
            if not isinstance(value,str) or not value or len(value)>MAX_AUTH or (
                "\r" in value or "\n" in value):
                raise ValueError("UNSAFE_HEADER")
            h[key]=value
    if not (h.get("x-emby-token") or h.get("x-mediabrowser-token")
            or h.get("authorization") or
            ("token=" in h.get("x-emby-authorization","").lower())):
        raise ValueError("NO_JELLYFIN_SESSION_TOKEN")
    h["accept-encoding"]="identity"
    h["connection"]="close"
    return h

def get_via_connection(connection,path,headers):
    try:
        connection.request("GET",path,headers=headers)
        resp=connection.getresponse()
        # For redirects, don't follow and don't forward Location.
        raw=resp.read(MAX_BODY+1)
        if len(raw)>MAX_BODY:
            return 502,{},b'{"error":"relay_response_too_large"}'
        forwarded={}
        for key in ("content-type","cache-control","etag"):
            val=resp.getheader(key)
            if val and len(val)<1024:
                forwarded[key]=val
        return resp.status,forwarded,raw
    except (OSError,http.client.HTTPException,TimeoutError):
        return 502,{},b'{"error":"read_only_upstream_unavailable"}'
    finally:
        connection.close()

class UnixConnection(http.client.HTTPConnection):
    def __init__(self,path):
        super().__init__("unix-only",timeout=12)
        self.unix_path=path
    def connect(self):
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)
        self.sock=sock

def to_host_aio(path,headers):
    return get_via_connection(
        http.client.HTTPConnection(UPSTREAM_HOST,UPSTREAM_PORT,timeout=12),
        path,headers)

def to_host_unix(path,headers,unix_path=SOCKET_PATH):
    return get_via_connection(UnixConnection(unix_path),path,headers)

class ReadOnlyHandler(BaseHTTPRequestHandler):
    transport=staticmethod(to_host_aio)
    allow_resume=False
    server_version="HomeDockerR23B2ReadOnly"
    sys_version=""
    def log_message(self,*args):
        # Deliberately no request URL/user agent/credential or response logs.
        return
    def _reject(self,code,reason):
        data=('{ "error": "'+reason+'" }').encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if not route_allowed(self.path,self.allow_resume):
            return self._reject(404,"read_only_route_denied")
        try:
            headers=filtered_headers(self.headers)
        except ValueError:
            return self._reject(401,"jellyfin_session_required")
        status,out_headers,body=self.transport(self.path,headers)
        self.send_response(status)
        for key,value in out_headers.items():
            self.send_header(key,value)
        self.send_header("Cache-Control","private, no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        self.send_header # No forwarded POST, including AuthByName.
        return self._reject(405,"get_only")
    do_PUT=do_POST
    do_PATCH=do_POST
    do_DELETE=do_POST
    do_HEAD=do_POST

class UnixHTTPServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
    allow_reuse_address=False

def build_handler(role,allow_resume=False,unix_path=SOCKET_PATH):
    class Endpoint(ReadOnlyHandler):
        pass
    Endpoint.allow_resume=allow_resume
    Endpoint.transport=staticmethod(
        to_host_aio if role=="host" else
        (lambda path,headers:to_host_unix(path,headers,unix_path)))
    return Endpoint

def run_host(allow_resume=False):
    parent=Path(SOCKET_PATH).parent
    info=parent.stat()
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid==65534
            and info.st_mode & 0o077==0
            and os.geteuid()==65534):
        raise SystemExit("R23B2_STOP_PRIVATE_UID65534_SOCKET_DIRECTORY_REQUIRED")
    if Path(SOCKET_PATH).exists():
        raise SystemExit("R23B2_STOP_EXISTING_SOCKET_REFUSE_OVERWRITE")
    os.umask(0o177)
    with UnixHTTPServer(SOCKET_PATH,build_handler("host",allow_resume)) as server:
        os.chmod(SOCKET_PATH,0o600)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            Path(SOCKET_PATH).unlink(missing_ok=True)

def run_sidecar(allow_resume=False):
    if os.geteuid()!=65534:
        raise SystemExit("R23B2_STOP_UNPRIVILEGED_SIDECAR_UID_REQUIRED")
    p=Path(SOCKET_PATH)
    info=p.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=65534 or (
        info.st_mode & 0o077):
        raise SystemExit("R23B2_STOP_PRIVATE_UNIX_SOCKET_REQUIRED")
    with ThreadingHTTPServer((SIDECAR_BIND,SIDECAR_PORT),
                             build_handler("sidecar",allow_resume)) as server:
        server.serve_forever(poll_interval=0.2)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role",required=True,choices=("host","sidecar"))
    parser.add_argument("--allow-resume",action="store_true")
    options=parser.parse_args()
    (run_host if options.role=="host" else run_sidecar)(options.allow_resume)

if __name__=="__main__":
    main()
