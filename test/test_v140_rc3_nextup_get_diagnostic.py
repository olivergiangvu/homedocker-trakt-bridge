"""Offline RC3-J transport diagnostic tests. No production networking."""
from io import BytesIO
import importlib.util
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/diagnose-v140-rc3-nextup-get-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3j_get",SCRIPT)
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeResponse:
    status=200
    def __init__(self,body=b'{"Items":[],"StartIndex":0,"TotalRecordCount":0}'):
        self.data=BytesIO(body)
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,n):return self.data.read(n)


class FakeOpener:
    def __init__(self, result):self.result=result
    def open(self,req,timeout):
        if isinstance(self.result,Exception):raise self.result
        return self.result


class GetDiagnosticsTests(unittest.TestCase):
    def test_silent_classification_of_timeout_auth_refusal_and_json(self):
        test=[
            (TimeoutError("token=SECRET"),"TIMEOUT"),
            (URLError(socket.timeout("hidden bearer")),"TIMEOUT"),
            (ConnectionRefusedError("hidden URL"),"TRANSPORT_OTHER"),
            (HTTPError("http://private",401,"no",{},None),"AUTH_REJECTED"),
            (ValueError("private identifier"),"JSON_INVALID"),
        ]
        for exc,want in test:
            with self.subTest(expected=want):
                self.assertEqual(probe.network_class(exc),want)
                self.assertNotIn("SECRET",probe.network_class(exc))
                self.assertNotIn("bearer",probe.network_class(exc))
                self.assertNotIn("private",probe.network_class(exc))

    def test_only_allowed_paths_and_exact_get_headers(self):
        obj=FakeOpener(FakeResponse())
        result=probe.read_get("secret","/Shows/NextUp?StartIndex=0&Limit=50",6,obj)
        self.assertEqual(result[0],"OK")
        self.assertEqual(result[1]["Items"],[])
        self.assertEqual(probe.read_get("secret","http://evil.invalid/",6,obj)[0],
                         "PATH_DENIED")

    def test_reader_reports_nonjson_separately_from_transport(self):
        obj=FakeOpener(FakeResponse(b'<html>secret</html>'))
        self.assertEqual(probe.read_get("secret",probe.NEXTUP,6,obj)[0],
                         "JSON_INVALID")
        obj=FakeOpener(FakeResponse(b'{"Items":'))
        self.assertEqual(probe.read_get("secret",probe.NEXTUP,6,obj)[0],
                         "JSON_INVALID")

    def test_reader_classifies_timeout_without_exposing_secret(self):
        obj=FakeOpener(URLError(socket.timeout("secret")))
        answer=probe.read_get("secret",probe.NEXTUP,6,obj)
        self.assertEqual(answer[0],"TIMEOUT")
        self.assertIsNone(answer[1])

    def test_only_retries_nextup_after_timeout_once(self):
        called=[]
        def fake_get(_token,path,timeout):
            called.append((path,timeout))
            if path==probe.USER:
                return "OK",{"Id":"mapped"},15
            return ("TIMEOUT",None,6000) if timeout==6 else \
                   ("OK",{"Items":[]},1000)
        with patch.object(probe.core,"credential",return_value=("private","mapped")), \
             patch.object(probe.core,"bridge_gate",return_value=True), \
             patch.object(probe,"read_get",side_effect=fake_get):
            with patch("builtins.print") as out:
                status=probe.main()
        self.assertEqual(status,0)
        self.assertEqual(called,[(probe.USER,6),(probe.NEXTUP,6),(probe.NEXTUP,12)])
        self.assertNotIn("private",str(out.call_args_list))

    def test_fail_closed_if_user_mapping_incorrect_no_nextup_attempt(self):
        called=[]
        def fake_get(_token,path,timeout):
            called.append(path)
            return "OK",{"Id":"someone-else"},1
        with patch.object(probe.core,"credential",return_value=("private","mapped")), \
             patch.object(probe.core,"bridge_gate",return_value=True), \
             patch.object(probe,"read_get",side_effect=fake_get):
            with patch("builtins.print"):
                status=probe.main()
        self.assertEqual(status,2)
        self.assertEqual(called,[probe.USER])

    def test_script_does_not_write_history_or_client_routing(self):
        src=SCRIPT.read_text()
        for banned in ["docker compose","/PlaybackInfo", "INSERT INTO",
                       "UPDATE watch_state","TraktClient","method='POST'"]:
            self.assertNotIn(banned,src)
        for needed in ["MAX_BODY = 1024*1024",
                       "RC3J_SECRETS_PRINTED=NO","RC3J_METADATA_LOOKUPS=0",
                       "core.bridge_gate()"]:
            self.assertIn(needed,src)


if __name__=="__main__":
    unittest.main()
