"""R23B2 relay tests: fake local TCP AIO + Unix + sidecar, no Docker or real credentials."""
import importlib.util
import http.client
import json
from pathlib import Path
import socketserver
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest import mock

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/r23b2-experimental-aio-unix-get-relay.py"
sp=importlib.util.spec_from_file_location("r23b2_relay",SCRIPT)
mod=importlib.util.module_from_spec(sp)
sp.loader.exec_module(mod)
TOKEN="synthetic-AIO-token-32-characters-only"
CANON=b'{"Items":[{"Id":"canonical-episode","UserData":{"PlaybackPositionTicks":888}}],"TotalRecordCount":1}'
RESUME=b'  {"Items":[{"Id":"movie","UserData":{"PlaybackPositionTicks":4242}}],"TotalRecordCount":1}  '
class FakeAio(BaseHTTPRequestHandler):
    calls=[]
    def log_message(self,*args):return
    def do_GET(self):
        auth=self.headers.get("X-Emby-Token")
        FakeAio.calls.append((self.command,self.path,auth))
        if auth!=TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/jellyfin/Shows/NextUp"):
            payload=CANON
        elif self.path=="/jellyfin/Users/Me":
            payload=b'{"Id":"synthetic-user-123"}'
        elif self.path=="/jellyfin/Items/canonical-series":
            payload=b'{"Id":"canonical-series","ProviderIds":{"Imdb":"tt1234567"}}'
        elif self.path.startswith("/jellyfin/UserItems/Resume"):
            payload=RESUME
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Set-Cookie","should-not-be-forwarded")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_POST(self):
        FakeAio.calls.append(("POST",self.path,"WRONG_METHOD"))
        self.send_response(405)
        self.end_headers()

class RelayContract(unittest.TestCase):
    def setUp(self):
        FakeAio.calls=[]
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sock=str(Path(self.temp.name)/"owned-relay.sock")
        upstream=ThreadingHTTPServer(("127.0.0.1",0),FakeAio)
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        th=threading.Thread(target=upstream.serve_forever,daemon=True)
        th.start()
        self.port=upstream.server_address[1]
        patch=mock.patch.object(mod,"UPSTREAM_PORT",self.port)
        patch.start()
        self.addCleanup(patch.stop)
        self.host=mod.UnixHTTPServer(self.sock,mod.build_handler("host",False))
        self.addCleanup(self.host.server_close)
        self.addCleanup(self.host.shutdown)
        threading.Thread(target=self.host.serve_forever,daemon=True).start()
        self.side=ThreadingHTTPServer(("127.0.0.1",0),
                  mod.build_handler("sidecar",False,self.sock))
        self.addCleanup(self.side.server_close)
        self.addCleanup(self.side.shutdown)
        threading.Thread(target=self.side.serve_forever,daemon=True).start()
    def call(self,method,path,token=TOKEN):
        connection=http.client.HTTPConnection("127.0.0.1",self.side.server_address[1],timeout=3)
        try:
            headers={"X-Emby-Token":token} if token else {}
            connection.request(method,path,headers=headers)
            rsp=connection.getresponse()
            return rsp.status,dict(rsp.getheaders()),rsp.read()
        finally:
            connection.close()
    def test_real_loopback_to_unix_to_aio_preserves_nextup_and_token(self):
        status,headers,raw=self.call("GET","/jellyfin/Shows/NextUp?Limit=20")
        self.assertEqual(status,200)
        self.assertEqual(raw,CANON)
        self.assertNotIn("set-cookie",headers)
        self.assertEqual(FakeAio.calls,[("GET","/jellyfin/Shows/NextUp?Limit=20",TOKEN)])
        self.assertNotIn(TOKEN,raw.decode())
    def test_user_me_and_series_lookup_are_exact_get(self):
        for path in ("/jellyfin/Users/Me","/jellyfin/Items/canonical-series"):
            code,_,raw=self.call("GET",path)
            self.assertEqual(code,200)
            self.assertTrue(json.loads(raw))
        self.assertEqual(len(FakeAio.calls),2)
    def test_rejects_all_mutating_methods_and_login_without_aio_contact(self):
        for method,path in (
            ("POST","/jellyfin/Users/AuthenticateByName"),
            ("POST","/jellyfin/Shows/NextUp"),
            ("PUT","/jellyfin/Items/canonical-series"),
            ("DELETE","/jellyfin/Shows/NextUp"),
        ):
            code,_,_=self.call(method,path)
            self.assertEqual(code,405)
        for path in ("/jellyfin/Users/AuthenticateByName",
                     "/jellyfin/Sessions/Playing",
                     "/jellyfin/Users/other/Items/Resume",
                     "/jellyfin/Shows/NextUp?Redirect=http://evil.invalid",
                     "/jellyfin/Shows/NextUp?Limit=999&unknown=1",
                     "/jellyfin/%2E%2E/Shows/NextUp",
                     "http://other.invalid/jellyfin/Shows/NextUp"):
            self.assertFalse(mod.route_allowed(path))
        self.assertEqual(self.call("GET","/jellyfin/Users/AuthenticateByName")[0],404)
        self.assertEqual(FakeAio.calls,[])
    def test_no_token_never_reaches_aio(self):
        status,_,_=self.call("GET","/jellyfin/Shows/NextUp?Limit=20",token=None)
        self.assertEqual(status,401)
        self.assertEqual(FakeAio.calls,[])
    def test_resume_default_denied_explicit_optin_bytes_exact(self):
        code,_,_=self.call("GET","/jellyfin/UserItems/Resume?Limit=20")
        self.assertEqual(code,404)
        self.assertEqual(FakeAio.calls,[])
        self.side.RequestHandlerClass=mod.build_handler("sidecar",True,self.sock)
        self.host.RequestHandlerClass=mod.build_handler("host",True)
        code,_,raw=self.call("GET","/jellyfin/UserItems/Resume?Limit=20")
        self.assertEqual(code,200)
        self.assertEqual(raw,RESUME)
        self.assertEqual(len(FakeAio.calls),1)
    def test_socket_lost_fails_closed_no_root_network_fallback(self):
        Path(self.sock).unlink()
        status,_,body=self.call("GET","/jellyfin/Shows/NextUp?Limit=20")
        self.assertEqual(status,502)
        self.assertIn(b"read_only_upstream_unavailable",body)
        self.assertEqual(FakeAio.calls,[])
    def test_no_silent_oversized_forward(self):
        with mock.patch.object(mod,"MAX_BODY",5):
            code,_,body=self.call("GET","/jellyfin/Shows/NextUp?Limit=20")
            self.assertEqual(code,502)
            self.assertIn(b"relay_response_too_large",body)
    def test_role_cli_never_performs_docker_or_nginx_mutations(self):
        source=SCRIPT.read_text()
        self.assertIn('SIDECAR_BIND="127.0.0.1"',source)
        self.assertIn('UPSTREAM_HOST="127.0.0.1"',source)
        self.assertIn('UPSTREAM_PORT=3240',source)
        self.assertIn("os.geteuid()==65534",source)
        for forbidden in ("docker run","docker network connect","nginx -s",
                          "sqlite3.connect","trakt.tv","curl | bash"):
            self.assertNotIn(forbidden,source)

if __name__=="__main__":unittest.main()
