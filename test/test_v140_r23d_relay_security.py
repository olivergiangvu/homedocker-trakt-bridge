import http.client
import importlib.util
import io
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve-v140-r23d-aio-get-relay.py"
spec = importlib.util.spec_from_file_location("r23d_relay_security", SCRIPT)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

TOKEN = "T" * 40

class RelaySecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        def upstream(path, token, agent, max_bytes, deadline):
            cls.calls.append((path, token, agent, max_bytes, deadline))
            if path.startswith("/jellyfin/Items/"):
                body = {"Type":"Series","Id":"series1234",
                        "ProviderIds":{"Imdb":"tt44051354"}}
            elif path == "/jellyfin/Users/Me":
                body = {"Id":"user000012345"}
            else:
                body = {"Items": [], "TotalRecordCount": 0}
            return 200, json.dumps(body).encode()
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            relay.handler_factory(
                "127.0.0.1", "127.0.0.1:TESTPORT", upstream
            ),
        )
        # In tests only, substitute exactly the local ephemeral host:port.
        cls.server.RequestHandlerClass = relay.handler_factory(
            "127.0.0.1", "127.0.0.1:" + str(cls.server.server_port), upstream
        )
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.calls.clear()

    def invoke(self, path, *, method="GET", token=TOKEN, host=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        hs = {
            "Host": host or ("127.0.0.1:" + str(self.server.server_port)),
        }
        if token is not None:
            hs["X-Emby-Token"] = token
        if headers:
            hs.update(headers)
        con.request(method, path, headers=hs, skip_host=True)
        resp = con.getresponse()
        body = resp.read()
        status = resp.status
        con.close()
        return status, body

    def test_only_get_allowed_paths_and_bound_request_size(self):
        for path in [
            "/jellyfin/Users/Me",
            "/jellyfin/Shows/NextUp",
            "/jellyfin/Shows/NextUp?Limit=50",
            "/jellyfin/Items/series1234",
        ]:
            status, body = self.invoke(path)
            self.assertEqual(status, 200, path)
            self.assertNotIn(TOKEN.encode(), body)
            self.assertIsInstance(json.loads(body), dict)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[1][-1], 22)
        self.assertEqual(self.calls[-1][-1], 10)

    def test_no_auth_no_upstream_and_does_not_echo_secret(self):
        for token in (None, "short", "bad\tspace"):
            with self.subTest(token=token):
                status, body = self.invoke("/jellyfin/Users/Me", token=token)
                self.assertEqual(status, 401)
                self.assertNotIn((token or "NoToken").encode(), body)
        self.assertEqual(len(self.calls), 0)

    def test_cannot_use_relay_to_login_write_history_fetch_resume_or_stream(self):
        for path in [
            "/jellyfin/Users/AuthenticateByName",
            "/jellyfin/UserItems/Resume",
            "/jellyfin/Users/abcd1234/Items/Resume",
            "/jellyfin/Items/series1234/PlaybackInfo",
            "/jellyfin/Items/series1234/Images/Primary",
            "/jellyfin/System/Info",
            "/jellyfin/Shows/NextUp?StartIndex=50",
            "/jellyfin/Shows/NextUp?Limit=51",
            "/jellyfin/Shows/NextUp?Limit=20&UserId=user",
            "/jellyfin/Shows/NextUp?Limit=0",
            "/jellyfin/Items/%2e%2e",
            "/jellyfin/Items/foo?x=1",
            "http://evil.test/jellyfin/Users/Me",
        ]:
            with self.subTest(path=path):
                status, _ = self.invoke(path)
                self.assertIn(status, (400, 403, 404))
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
            with self.subTest(method=method):
                status, _ = self.invoke("/jellyfin/Users/Me", method=method)
                self.assertEqual(status, 405)
        self.assertEqual(self.calls, [])

    def test_host_is_strict_and_cannot_spoof_wrong_container_source(self):
        status, _ = self.invoke("/jellyfin/Users/Me", host="evil.test")
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])
        handler = relay.handler_factory("127.0.0.2", "127.0.0.1:"+str(self.server.server_port),
                                        lambda *args: self.fail("should not reach upstream"))
        different = ThreadingHTTPServer(("127.0.0.1",0), handler)
        thread = threading.Thread(target=different.serve_forever, daemon=True)
        thread.start()
        try:
            con = http.client.HTTPConnection("127.0.0.1", different.server_port, timeout=3)
            con.request("GET","/jellyfin/Users/Me",
                        headers={"Host":"127.0.0.1:"+str(self.server.server_port),
                                 "X-Emby-Token":TOKEN})
            r=con.getresponse()
            self.assertEqual(r.status,403)
            r.read()
            con.close()
        finally:
            different.shutdown()
            different.server_close()
            thread.join(timeout=5)

    def test_allowed_scope_is_exact_and_remains_fixed_loopback_upstream(self):
        for arg in (
            "/jellyfin/Shows/NextUp?Limit=20",
            "/jellyfin/Items/ABC_d-e1234",
            "/jellyfin/Users/Me",
        ):
            self.assertIsNotNone(relay.path_to_upstream(arg))
        for arg in (
            "//remote.test/jellyfin/Users/Me",
            "/jellyfin/Shows/NextUp?SeriesId=abc",
            "/jellyfin/Shows/NextUp?Limit=999",
            "/jellyfin/Shows/NextUp?Limit=1&Limit=2",
            "/jellyfin/Items/%2Fetc%2Fpasswd",
        ):
            self.assertIsNone(relay.path_to_upstream(arg))
        self.assertEqual(relay.UPSTREAM, "http://127.0.0.1:3240/jellyfin")

    def test_redacts_upstream_errors_and_never_echos_body_or_token(self):
        old_handler = self.server.RequestHandlerClass
        def mock_fail(*args):
            raise RuntimeError("token=" + TOKEN + " /Users/Me")
        self.server.RequestHandlerClass = relay.handler_factory(
            "127.0.0.1", "127.0.0.1:"+str(self.server.server_port), mock_fail
        )
        try:
            status, body = self.invoke("/jellyfin/Users/Me")
            self.assertEqual(status,502)
            self.assertNotIn(TOKEN.encode(),body)
            self.assertNotIn(b"/Users/Me", body)
        finally:
            self.server.RequestHandlerClass = old_handler
        self.assertEqual(len(self.calls), 0)

if __name__ == "__main__":
    unittest.main()
