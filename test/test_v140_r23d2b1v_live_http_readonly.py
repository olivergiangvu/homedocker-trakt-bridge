"""B1V read-only live HTTP comparator: synthetic verification only; no network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit-v140-r23d2b1v-real-client-readonly.py"
spec=importlib.util.spec_from_file_location("b1v",SCRIPT)
b1v=importlib.util.module_from_spec(spec)
spec.loader.exec_module(b1v)

USER="userA12345678"
CANON={"Id":"canonicalEp","SeriesId":"canonicalSeries","Type":"Episode",
       "UserData":{"PlaybackPositionTicks":444,"LastPlayedDate":"2026-10-15"}}
ALIAS={"Id":"aliasEp","SeriesId":"aliasSeries","Type":"Episode",
       "UserData":{"PlaybackPositionTicks":0}}
ORIGINAL={"Items":[ALIAS,CANON],"TotalRecordCount":2,"StartIndex":0,
          "Opaque":"source-value"}
FILTERED={**ORIGINAL,"Items":[CANON],"TotalRecordCount":1}
RESUME={"Items":[{"Id":"movie","Type":"Movie","UserData":{"PlaybackPositionTicks":444}}],
        "TotalRecordCount":41,"StartIndex":0}

def raw(value):
    return json.dumps(value,indent=2).encode()

class Contract(unittest.TestCase):
    def src(self,view=ORIGINAL,resume=RESUME,alternate=None):
        calls=[]
        def get(path):
            calls.append(path)
            if path=="/Users/Me":return 200,raw({"Id":USER})
            if path==b1v.NEXTUP:
                if alternate is not None and calls.count(path)==2:
                    return 200,raw(alternate)
                return 200,raw(view)
            if path==b1v.RESUME:return 200,raw(resume)
            raise AssertionError("unexpected path "+path)
        return get,calls
    def gate(self,view=FILTERED,resume=RESUME,resume_status=200):
        calls=[]
        def get(path):
            calls.append(path)
            if path==b1v.NEXTUP:return 200,raw(view)
            if path==b1v.RESUME:return (resume_status,raw(resume) if resume_status==200 else b"")
            raise AssertionError("unexpected path")
        return get,calls
    def test_exact_verified_source_item_preservation(self):
        source,a=self.src()
        gateway,b=self.gate()
        result=b1v.safe_compare(source,gateway)
        self.assertEqual(result["nextup_result"],"SOURCE_ITEM_SUBSET_CONTRACT_PASS")
        self.assertEqual(result["removed_in_projection"],1)
        self.assertTrue(result["source_nextup_stable_during_probe"])
        self.assertEqual(a,["/Users/Me",b1v.NEXTUP,b1v.NEXTUP])
        self.assertEqual(b,[b1v.NEXTUP])
        self.assertEqual(result["history_or_playback_writes_requested"],0)
        self.assertFalse(result["actual_client_playback_or_scrobble_confirmed"])
        self.assertNotIn(USER,json.dumps(result))
        self.assertNotIn("canonicalEp",json.dumps(result))
    def test_changed_source_returns_inconclusive_not_a_false_pass(self):
        source,_=self.src(alternate={**ORIGINAL,"Items":[CANON]})
        gateway,_=self.gate()
        result=b1v.safe_compare(source,gateway)
        self.assertEqual(result["nextup_result"],"INCONCLUSIVE_SOURCE_CHANGED")
        self.assertNotIn("gateway_items",result)
    def test_modified_resume_position_must_abort_if_source_is_stable(self):
        source,_=self.src()
        different={**RESUME,"Items":[{**RESUME["Items"][0],
                         "UserData":{"PlaybackPositionTicks":777}}]}
        gateway,_=self.gate(resume=different)
        with self.assertRaisesRegex(b1v.Stop,"RESUME_GATEWAY_BODY_NOT_PASSTHROUGH"):
            b1v.safe_compare(source,gateway,True)
    def test_resume_raw_passthrough_and_disabled(self):
        source,_=self.src()
        gateway,_=self.gate()
        result=b1v.safe_compare(source,gateway,True)
        self.assertEqual(result["resume_result"],"RAW_BYTES_MATCH_STABLE_SOURCE")
        self.assertEqual(result["resume_items"],1)
        gateway,_=self.gate(resume_status=404)
        result=b1v.safe_compare(source,gateway,True)
        self.assertEqual(result["resume_result"],"GATEWAY_RESUME_DISABLED_404")
    def test_gateway_cannot_modify_item_fields_or_invent_ids(self):
        source,_=self.src()
        for view in (
            {**FILTERED,"Items":[{**CANON,"UserData":{"PlaybackPositionTicks":0}}]},
            {**FILTERED,"Items":[{"Id":"added"}]},
        ):
            gateway,_=self.gate(view=view)
            with self.assertRaises(b1v.Stop):
                b1v.safe_compare(source,gateway)
    def test_duplicate_items_rejected(self):
        source,_=self.src(view={**ORIGINAL,"Items":[ALIAS,ALIAS]})
        gateway,_=self.gate(view=FILTERED)
        with self.assertRaisesRegex(b1v.Stop,"DUPLICATE_SOURCE_ITEM_ID"):
            b1v.safe_compare(source,gateway)
    def test_unknown_user_stop(self):
        def source(path):
            return 200,raw({"Id":"!"}) if path=="/Users/Me" else (200,raw(ORIGINAL))
        gateway,_=self.gate()
        with self.assertRaisesRegex(b1v.Stop,"SOURCE_USER_ID_INVALID"):
            b1v.safe_compare(source,gateway)
    def test_host_preflight_get_health_only_and_no_credentials(self):
        calls=[]
        def get(origin,path,token=None):
            calls.append((origin,path,token))
            return 200,raw({"status":"ok"})
        result=b1v.preflight(get)
        self.assertEqual(calls,[("aio","/health",None),("gateway","/health",None)])
        self.assertFalse(result["credentials_requested"])
        self.assertFalse(result["source_or_gateway_history_read"])
    def test_only_fixed_loopback_sources_and_get_routes(self):
        for origin,path in (("trakt","/health"),("aio","/Sessions/Playing"),
                            ("gateway","/Users/Me"),("aio","//evil"),
                            ("aio","/Shows/NextUp?Limit=2000")):
            with self.assertRaisesRegex(b1v.Stop,"UNAPPROVED_NETWORK_TARGET"):
                b1v.fixed_get(origin,path)
        self.assertEqual(b1v.AIO,"http://127.0.0.1:3240/jellyfin")
        self.assertEqual(b1v.GATEWAY,"http://127.0.0.1:17141")
    def test_explicit_confirmation_is_required_before_prompt_or_request(self):
        p=subprocess.run([sys.executable,"-B",str(SCRIPT),"--compare"],
                         capture_output=True,text=True,timeout=4)
        self.assertEqual(p.returncode,5)
        self.assertIn("EXPLICIT_LIVE_READONLY_CONFIRMATION_REQUIRED",p.stderr)
        p=subprocess.run([sys.executable,"-B",str(SCRIPT),"--include-resume"],
                         capture_output=True,text=True,timeout=4)
        self.assertEqual(p.returncode,5)
        self.assertIn("RESUME_REQUIRES_COMPARE",p.stderr)
    def test_all_report_flags_prevent_live_claim(self):
        source,_=self.src()
        gateway,_=self.gate()
        r=b1v.safe_compare(source,gateway)
        for k in ("production_gateway_routing_confirmed",
                  "actual_client_playback_or_scrobble_confirmed",
                  "stable_v140_release_authorized"):
            self.assertIs(r[k],False)

if __name__=="__main__":unittest.main()
