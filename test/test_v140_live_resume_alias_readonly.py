import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-live-resume-alias-readonly.py"
spec = importlib.util.spec_from_file_location("r23b_resume_live", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TOKEN = "X" * 40
CANON = mod.support.KNOWN_PAIR[0]
ALIAS = mod.support.KNOWN_PAIR[1]
ONE = "a" * 32
TWO = "b" * 32
MOV = "c" * 32

def epi(num, series, ticks=1000):
    return {
        "Id": f"episode_{num}", "Type": "Episode", "SeriesId": series,
        "UserData": {"PlaybackPositionTicks": ticks},
    }

def movie(num, ticks=1000):
    return {
        "Id": f"movie_{num}", "Type": "Movie",
        "UserData": {"PlaybackPositionTicks": ticks},
    }

def fake_get(path, *, token, limit=mod.support.MAX_HTTP):
    assert token == TOKEN
    if path == "/Users/Me":
        return {"Id": "C"*16}, 1
    if path == "/UserItems/Resume?StartIndex=0&Limit=20":
        return {"Items": [epi(i, ONE) for i in range(1, 20)] + [movie(1)]}, 10
    if path == "/UserItems/Resume?StartIndex=20&Limit=20":
        return {"Items": [epi(i, TWO) for i in range(21, 40)] + [movie(2)]}, 20
    if path == "/Items/" + ONE:
        return {"Type": "Series", "Id": ONE, "ProviderIds": {"Imdb": CANON}}, 2
    if path == "/Items/" + TWO:
        return {"Type": "Series", "Id": TWO, "ProviderIds": {"Imdb": ALIAS}}, 2
    raise AssertionError("unexpected URL or method")

class R23B2ResumeProofTests(unittest.TestCase):
    def test_verified_pair_both_active_never_auto_deduplicate(self):
        with mock.patch.object(mod.support, "source_preflight", return_value={
            "status": "PRECHECK_PASS"
        }), mock.patch.object(mod.support, "fixed_get", side_effect=fake_get):
            r = mod.inspect_session(TOKEN, "/any/source/db")
        self.assertEqual(r["status"], "VERIFIED_ALIAS_PAIR_IN_RESUME_SAMPLE")
        self.assertEqual(r["resume_returned_items"], 40)
        self.assertEqual(r["resume_episode_items"], 38)
        self.assertEqual(r["resume_movie_items"], 2)
        self.assertEqual(r["resume_active_position_items"], 40)
        self.assertEqual(r["resume_repeated_opaque_item_ids_in_sample"], 0)
        self.assertEqual(r["resume_distinct_episode_series_references"], 2)
        self.assertEqual(r["verified_canonical_resume_cards"], 19)
        self.assertEqual(r["verified_alias_resume_cards"], 19)
        self.assertEqual(r["verified_canonical_active_resume_cards"], 19)
        self.assertEqual(r["verified_alias_active_resume_cards"], 19)
        self.assertFalse(r["safe_to_deduplicate_active_resume"])
        self.assertFalse(r["all_resume_pages_proven_complete"])
        self.assertEqual(r["aio_get_requests"], 5)
        exposed=json.dumps(r)
        for secret in (TOKEN, ONE, TWO, CANON, ALIAS, MOV):
            self.assertNotIn(secret, exposed)

    def test_one_alias_missing_does_not_claim_pair(self):
        data=[epi(i, ONE) for i in range(20)]
        def get(path,*,token,limit=0):
            if path == "/Users/Me":
                return {"Id":"D"*16}, 1
            if path == "/UserItems/Resume?StartIndex=0&Limit=20":
                return {"Items":data}, 3
            if path == "/UserItems/Resume?StartIndex=20&Limit=20":
                return {"Items":[]}, 1
            if path == "/Items/" + ONE:
                return {"Type":"Series","Id":ONE,
                        "ProviderIds":{"Imdb":CANON}},1
            raise AssertionError(path)
        with mock.patch.object(mod.support,"source_preflight"), \
             mock.patch.object(mod.support,"fixed_get",side_effect=get):
            result=mod.inspect_session(TOKEN,"ignored")
        self.assertEqual(result["status"],"VERIFIED_ALIAS_PAIR_NOT_OBSERVED_IN_RESUME_SAMPLE")
        self.assertFalse(result["both_verified_spellings_present"])
        self.assertEqual(result["verified_alias_resume_cards"],0)

    def test_page_offset_explicit_allowlist(self):
        with self.assertRaisesRegex(mod.support.Stop, "invalid Resume page offset"):
            mod.read_resume_page(TOKEN, 80)
        with self.assertRaisesRegex(mod.support.Stop, "Resume sample base offset not permitted"):
            mod.inspect_session(TOKEN, "ignored", 20)

    def test_continuation_requests_only_offsets_40_and_60(self):
        routes = []
        def source(path, *, token, limit=mod.support.MAX_HTTP):
            routes.append(path)
            self.assertEqual(token, TOKEN)
            if path == "/Users/Me":
                return {"Id": "Q"*16}, 1
            if path == "/UserItems/Resume?StartIndex=40&Limit=20":
                return {"Items":[epi(i, ONE) for i in range(40,59)] + [movie(40)]}, 17
            if path == "/UserItems/Resume?StartIndex=60&Limit=20":
                return {"Items":[epi(i, TWO) for i in range(60,79)] + [movie(60)]}, 23
            if path == "/Items/" + ONE:
                return {"Type":"Series","Id":ONE,"ProviderIds":{"Imdb":CANON}},1
            if path == "/Items/" + TWO:
                return {"Type":"Series","Id":TWO,"ProviderIds":{"Imdb":ALIAS}},1
            raise AssertionError("Unexpected or out of scope request")
        with mock.patch.object(mod.support,"source_preflight"), \
             mock.patch.object(mod.support,"fixed_get",side_effect=source):
            result=mod.inspect_session(TOKEN, "ignored", 40)
        self.assertEqual(result["resume_requested_offsets"],[40,60])
        self.assertEqual(result["resume_sample_base_offset"],40)
        self.assertEqual(result["resume_returned_items"],40)
        self.assertEqual(result["verified_canonical_resume_cards"],19)
        self.assertEqual(result["verified_alias_resume_cards"],19)
        self.assertTrue(result["both_verified_spellings_present"])
        self.assertFalse(result["safe_to_deduplicate_active_resume"])
        self.assertEqual(result["aio_get_requests"],5)
        self.assertFalse(any("StartIndex=0" in x or "StartIndex=20" in x for x in routes))
        for raw in (CANON,ALIAS,TOKEN,ONE,TWO):
            self.assertNotIn(raw,json.dumps(result))

    def test_page_shape_fail_closed(self):
        with mock.patch.object(mod.support,"fixed_get",
                               return_value=({"Items":[{"Id":None}]},1)):
            with self.assertRaises(mod.support.Stop):
                mod.read_resume_page(TOKEN,0)

    def test_unknown_types_or_position_rejected(self):
        for bad in (
            {"Id":"ep","Type":"MusicVideo","UserData":{"PlaybackPositionTicks":100}},
            {"Id":"ep","Type":"Episode","SeriesId":ONE,"UserData":{"PlaybackPositionTicks":"100"}},
            {"Id":"ep","Type":"Episode","SeriesId":ONE},
        ):
            with self.assertRaises(mod.support.Stop):
                mod.summarize_resume_rows([bad],{})

if __name__=="__main__":
    unittest.main()
