"""B1V login via AIO Jellyfin user/password; ALL tests use fake in-memory opener."""
import contextlib
import io
import json
from pathlib import Path
import importlib.util
import unittest
from unittest import mock
from urllib import error

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit-v140-r23d2b1v-real-client-readonly.py"
sp=importlib.util.spec_from_file_location("b1v_login_under_test",SCRIPT)
mod=importlib.util.module_from_spec(sp)
sp.loader.exec_module(mod)

TOKEN="x"*32
USER="a123456789abcdef"
SECRET="B1V-not-a-real-password/1234"
USERNAME="my-alias/household"
class Response:
    def __init__(self,content,status=200):
        self.content=content
        self.status=status
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n=-1):return self.content[:n]
class FakeOpener:
    def __init__(self,body,status=200):
        self.body=body
        self.status=status
        self.requests=[]
    def open(self,req,timeout=None):
        self.requests.append((req,timeout))
        return Response(self.body,self.status)
class TTY(io.StringIO):
    def isatty(self):return True

class AuthenticationContract(unittest.TestCase):
    def test_correct_single_post_json_and_headers_with_no_credentials_in_url(self):
        response=json.dumps({"AccessToken":TOKEN,"User":{"Id":USER}}).encode()
        opener=FakeOpener(response)
        token,user=mod.login_with_username_password(USERNAME,SECRET,opener)
        self.assertEqual((token,user),(TOKEN,USER))
        self.assertEqual(len(opener.requests),1)
        req,timeout=opener.requests[0]
        self.assertEqual(req.get_method(),"POST")
        self.assertEqual(req.full_url,"http://127.0.0.1:3240/jellyfin/Users/AuthenticateByName")
        self.assertEqual(timeout,10)
        self.assertEqual(json.loads(req.data),{"Username":USERNAME,"Pw":SECRET})
        self.assertIn('MediaBrowser Client=',req.get_header("X-emby-authorization"))
        self.assertEqual(req.get_header("Content-type"),"application/json")
        self.assertNotIn(SECRET,req.full_url)
        self.assertNotIn(TOKEN,req.full_url)
    def test_failed_login_error_does_not_echo_credentials(self):
        class Fail:
            def open(self,req,timeout=None):
                raise error.HTTPError(req.full_url,401,"bad password",{},None)
        with self.assertRaisesRegex(mod.Stop,"AUTH_HTTP_401") as cm:
            mod.login_with_username_password(USERNAME,SECRET,Fail())
        self.assertNotIn(SECRET,str(cm.exception))
        self.assertNotIn(USERNAME,str(cm.exception))
    def test_no_redirect_or_proxy_used_for_real_http(self):
        self.assertEqual(mod.AIO,"http://127.0.0.1:3240/jellyfin")
        self.assertEqual(mod.GATEWAY,"http://127.0.0.1:17141")
        # urllib.build_opener deliberately omits an EMPTY ProxyHandler;
        # that is the correct behavior (no proxy routing is registered).
        self.assertIn('request.ProxyHandler({})',SCRIPT.read_text())
        self.assertFalse(any(isinstance(h,mod.request.ProxyHandler)
                             for h in mod.OPENER.handlers))
        self.assertTrue(any(isinstance(h,mod.request.HTTPRedirectHandler)
                            and h.redirect_request(None,None,None,None,None,None)
                                is None
                            for h in mod.OPENER.handlers))
    def test_malformed_or_oversize_login_response_stops_without_sending_token(self):
        for payload in (b"{}",b'{"AccessToken":"abcd","User":{"Id":"x"}}',
                        b'{"AccessToken":"'+TOKEN.encode()+b'","User":{}}',
                        b"{broken",b"x"*(mod.AUTH_MAX_BYTES+1)):
            with self.subTest(payload=payload[:15]):
                with self.assertRaises(mod.Stop):
                    mod.login_with_username_password(USERNAME,SECRET,FakeOpener(payload))
    def test_invalid_credential_input_fails_before_network(self):
        opener=FakeOpener(b"")
        for u,p in (("",SECRET),("a\nb",SECRET),(USERNAME,""),
                    (USERNAME,"abc\x00"),("a"*257,SECRET),
                    (USERNAME,"a"*1025)):
            with self.subTest(username_length=len(u),pw_length=len(p)):
                with self.assertRaisesRegex(mod.Stop,"INVALID_CREDENTIAL_INPUT"):
                    mod.login_with_username_password(u,p,opener)
        self.assertEqual(opener.requests,[])
    def test_auth_response_user_must_match_AIO_users_me(self):
        def src(path):
            if path=="/Users/Me":return 200,b'{"Id":"second-user-222"}'
            return 200,b'{"Items":[],"TotalRecordCount":0}'
        def gate(path):return 200,b'{"Items":[],"TotalRecordCount":0}'
        with self.assertRaisesRegex(mod.Stop,"AUTH_LOGIN_USER_MISMATCH"):
            mod.safe_compare(src,gate,expected_user_id=USER)
    def test_login_mode_main_one_post_and_get_only_with_redacted_output(self):
        out=io.StringIO()
        calls=[]
        def fake_login(u,p):
            calls.append(("LOGIN",u,p))
            return TOKEN,USER
        def fake_get(origin,path,token=None):
            self.assertEqual(token,TOKEN)
            calls.append(("GET",origin,path))
            if path=="/Users/Me":return 200,json.dumps({"Id":USER}).encode()
            return 200,b'{"Items":[],"TotalRecordCount":0}'
        with (
            mock.patch.object(mod.sys,"argv",["cli","--compare","--confirm",
                                               "LIVE_AUTH_LOGIN_GET_ONLY"]),
            mock.patch.object(mod.sys,"stdin",TTY("")),
            mock.patch("builtins.input",return_value=USERNAME),
            mock.patch.object(mod.getpass,"getpass",return_value=SECRET),
            mock.patch.object(mod,"preflight",return_value={"health":True}),
            mock.patch.object(mod,"login_with_username_password",side_effect=fake_login),
            mock.patch.object(mod,"fixed_get",side_effect=fake_get),
            contextlib.redirect_stdout(out),
        ):
            mod.main()
        payload=json.loads(out.getvalue()[out.getvalue().index("{"):])
        self.assertEqual(payload["authentication_post_requests"],1)
        self.assertTrue(payload["authentication_created_server_session"])
        self.assertEqual(payload["authentication_mode"],"USERNAME_PASSWORD_LOGIN")
        self.assertTrue(payload["comparison_get_only"])
        self.assertEqual(payload["history_or_playback_writes_requested"],0)
        self.assertEqual(payload["nextup_result"],"SOURCE_ITEM_SUBSET_CONTRACT_PASS")
        self.assertEqual(len([c for c in calls if c[0]=="LOGIN"]),1)
        self.assertEqual(len([c for c in calls if c[0]=="GET"]),4)
        for secret in (TOKEN,SECRET,USERNAME,USER):
            self.assertNotIn(secret,out.getvalue())
    def test_token_compatibility_requires_separate_explicit_opt_in(self):
        result=io.StringIO()
        def fake_get(origin,path,token=None):
            self.assertEqual(token,TOKEN)
            if path=="/Users/Me":return 200,json.dumps({"Id":USER}).encode()
            return 200,b'{"Items":[],"TotalRecordCount":0}'
        with (
            mock.patch.object(mod.sys,"argv",["cli","--compare","--existing-token",
                                     "--confirm","LIVE_READONLY_GET_ONLY"]),
            mock.patch.object(mod.sys,"stdin",TTY("")),
            mock.patch.object(mod.getpass,"getpass",return_value=TOKEN),
            mock.patch.object(mod,"preflight",return_value={"health":True}),
            mock.patch.object(mod,"fixed_get",side_effect=fake_get),
            contextlib.redirect_stdout(result),
        ):
            mod.main()
        payload=json.loads(result.getvalue())
        self.assertEqual(payload["authentication_mode"],"EXISTING_TOKEN")
        self.assertEqual(payload["authentication_post_requests"],0)
        self.assertFalse(payload["authentication_created_server_session"])
        self.assertNotIn(TOKEN,result.getvalue())

if __name__=="__main__":
    unittest.main()
