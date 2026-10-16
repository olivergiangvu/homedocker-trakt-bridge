"""Synthetic/offline tests for the G1A live AIO input audit. NO Docker/network."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "audit-v140-g1-live-inputs.py"
spec = importlib.util.spec_from_file_location("g1_live", PATH)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CANON = "tt44051354"
ALIAS = "tt44094505"
A = "useraaaaaaaaaaaaaa"
LEGACY = {
    "shows": {
        "263102": {"traktImdb": CANON, "preferredMetaId": ALIAS,
                   "updatedAt": 1720000000000},
        "263103": {"traktImdb": "tt12345678", "preferredMetaId": "tt12345678",
                   "updatedAt": 1720000000000},
    }
}
PAIRS = {
    "shows": 2,
    "aliases": 1,
    "owners": {CANON: 263102, ALIAS: 263102, "tt12345678": 263103},
}
NEXT = {
    "Items": [
        {"Id": "aliasEpisode", "SeriesId": "a_series", "Type": "Episode",
         "UserData": {"PlaybackPositionTicks": 0}},
        {"Id": "canonEpisode", "SeriesId": "b_series", "Type": "Episode",
         "UserData": {"PlaybackPositionTicks": 2100}},
    ],
    "TotalRecordCount": 2,
}
META = {
    "/Items/a_series": {"Id": "a_series", "Type": "Series", "ProviderIds": {"Imdb": ALIAS}},
    "/Items/b_series": {"Id": "b_series", "Type": "Series", "ProviderIds": {"Imdb": CANON}},
}

class Safety(unittest.TestCase):
    def test_exact_legacy_validation(self):
        count, pairs, owners = app.load_legacy(LEGACY)
        self.assertEqual(count, 2)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(owners[ALIAS], owners[CANON])

    def test_conflict_fails_closed(self):
        bad = {"shows": {**LEGACY["shows"], "263104": {
            "traktImdb": ALIAS, "preferredMetaId": "tt99384721",
            "updatedAt": 1720000000000
        }}}
        with self.assertRaises(app.Stop):
            app.load_legacy(bad)

    def test_invalid_false_provenance_fails_closed(self):
        for stamp in (True, None, 0, "1000"):
            raw = {"shows": {"1": {
                "traktImdb": CANON, "preferredMetaId": ALIAS,
                "updatedAt": stamp}}}
            with self.subTest(stamp=stamp), self.assertRaises(app.Stop):
                app.load_legacy(raw)

    def test_positive_live_source_evidence_not_gateway_claim(self):
        c = app.classify_nextup(NEXT["Items"],
                                {"a_series": ALIAS, "b_series": CANON},
                                PAIRS["owners"])
        self.assertEqual(c["live_alias_pair_candidates"], 1)
        self.assertEqual(c["live_active_resume_winner_candidates"], 1)

    def test_two_active_resumes_stay_ambiguous(self):
        rows = [{**x, "UserData": {"PlaybackPositionTicks": 100}}
                for x in NEXT["Items"]]
        c = app.classify_nextup(rows,
            {"a_series": ALIAS, "b_series": CANON}, PAIRS["owners"])
        self.assertEqual(c["live_alias_pair_candidates"], 1)
        self.assertEqual(c["live_active_resume_winner_candidates"], 0)
        self.assertEqual(c["live_ambiguous_or_unproven_pairs"], 1)

    def test_wrong_spelling_never_counts_as_pair(self):
        c = app.classify_nextup(NEXT["Items"],
            {"a_series": CANON, "b_series": CANON}, PAIRS["owners"])
        self.assertEqual(c["live_alias_pair_candidates"], 0)

    def test_restricted_methods_and_network_routes_fail_before_request(self):
        for path, method in (
            ("/Sessions/Playing", "POST"),
            ("/Users/Me", "POST"),
            ("/Items/../secret", "GET"),
            ("https://example.com", "GET"),
            ("/Shows/NextUp?StartIndex=1", "GET")
        ):
            with self.subTest(path=path), self.assertRaises(app.Stop):
                app.aio_request(path, method=method)

    def test_missing_confirmation_rejected_before_docker(self):
        for mode in ("login", "existing-token"):
            p = subprocess.run([sys.executable, "-B", str(PATH), "--mode", mode],
                               capture_output=True, text=True, timeout=6)
            self.assertEqual(p.returncode, 5)
            self.assertIn("G1A_STOP_EXPLICIT_", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_mocked_live_read_only_input_audit_never_claims_mapping(self):
        paths = []
        def read(path, token=None, **kwargs):
            paths.append((path, kwargs.get("method", "GET")))
            result = {
                "/Users/Me": {"Id": A},
                "/Shows/NextUp?Limit=20": NEXT,
                **META,
            }[path]
            return result
        with patch.object(app.sys.stdin, "isatty", return_value=True), \
             patch.object(app.getpass, "getpass", return_value="synthetic_test_token_long"), \
             patch.object(app, "aio_request", side_effect=read):
            out = app.live_inputs(PAIRS, "existing-token")
        self.assertTrue(out["aio_session_user_authenticated"])
        self.assertEqual(out["authentication_post_requests"], 0)
        self.assertEqual(out["status"], "LIVE_PAIR_PRESENT_NEEDS_CANDIDATE_GATEWAY")
        self.assertEqual(out["live_active_resume_winner_candidates"], 1)
        self.assertFalse(out["profile_identity_binding_verified"])
        self.assertFalse(out["active_candidate_identity_graph_verified"])
        self.assertFalse(out["g1_complete"])
        self.assertEqual(out["playback_or_history_writes_requested"], 0)
        self.assertEqual(out["production_sqlite_writes"], 0)
        self.assertTrue(all(method == "GET" for _, method in paths))
        self.assertNotIn(A, str(out))
        self.assertNotIn(CANON, str(out))
        self.assertNotIn(ALIAS, str(out))

    def test_live_changed_sample_inconclusive(self):
        def read(path, token=None, **kwargs):
            if path == "/Users/Me": return {"Id": A}
            if path == "/Shows/NextUp?Limit=20":
                read.count += 1
                return NEXT if read.count == 1 else {"Items": [], "TotalRecordCount": 0}
            return META[path]
        read.count = 0
        with patch.object(app.sys.stdin, "isatty", return_value=True), \
             patch.object(app.getpass, "getpass", return_value="synthetic_test_token_long"), \
             patch.object(app, "aio_request", side_effect=read):
            out = app.live_inputs(PAIRS, "existing-token")
        self.assertEqual(out["status"], "SOURCE_CHANGED_INCONCLUSIVE")
        self.assertFalse(out["source_stable_during_probe"])
        self.assertFalse(out["g1_complete"])

if __name__ == "__main__":
    unittest.main()
