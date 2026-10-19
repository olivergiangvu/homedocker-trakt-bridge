"""Offline regressions for the AIO StartIndex echo vs actual page-content diagnostic."""
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/diagnose-v140-rc3-nextup-offset-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3_offset_probe", PATH)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def fixture(*, echo="none", repeat=False, overlap=False, mutate_replay=False):
    seen = 0
    def fetch(path):
        nonlocal seen
        seen += 1
        parsed = parse_qs(urlsplit(path).query)
        offset = int(parsed["StartIndex"][0])
        assert int(parsed["Limit"][0]) == 20
        effective = 0 if repeat else offset
        items = [
            {"Id": "episode-" + str(effective + i), "SeriesId":"series"}
            for i in range(20)
        ]
        if overlap and offset == 20:
            items[0]["Id"] = "episode-1"
        if mutate_replay and seen == 3:
            items[0]["Id"] = "changed-episode"
        output = {"Items":items,"TotalRecordCount":20+offset}
        if echo == "true":
            output["StartIndex"] = offset
        elif echo == "wrong":
            output["StartIndex"] = 0
        elif echo == "bool":
            output["StartIndex"] = False
        return output
    return fetch


class OffsetEchoDiagnosticTests(unittest.TestCase):
    def test_missing_echo_does_not_false_label_offset_ignored(self):
        result = diagnostic.diagnose(fixture())
        self.assertEqual(result["first_echo"], "ABSENT")
        self.assertEqual(result["second_echo"], "ABSENT")
        self.assertEqual(result["decision"], "MISSING_ECHO_OFFSETS_UNPROVEN")
        self.assertFalse(result["same_episode_sequence"])
        self.assertEqual(result["cross_page_overlap_count"], 0)
        self.assertTrue(result["first_replay_stable"])

    def test_explicit_offsets_are_observational_only(self):
        result = diagnostic.diagnose(fixture(echo="true"))
        self.assertEqual(result["decision"], "OFFSETS_OBSERVED_NOT_SNAPSHOT_PROOF")
        self.assertEqual(result["second_echo"], "MATCH")

    def test_repeated_offset_page_is_inconclusive_and_flagged(self):
        result = diagnostic.diagnose(fixture(repeat=True))
        self.assertEqual(result["decision"], "OFFSET_IGNORED_OR_CONTENT_REPEATED")
        self.assertTrue(result["same_episode_sequence"])
        self.assertEqual(result["cross_page_overlap_count"], 20)

    def test_mismatched_echo_is_separate_from_missing(self):
        result = diagnostic.diagnose(fixture(echo="wrong"))
        self.assertEqual(result["decision"], "EXPLICIT_OFFSET_ECHO_INVALID")
        self.assertEqual(result["second_echo"], "MISMATCH")

    def test_invalid_echo_type_is_rejected(self):
        result = diagnostic.diagnose(fixture(echo="bool"))
        self.assertEqual(result["decision"], "EXPLICIT_OFFSET_ECHO_INVALID")
        self.assertEqual(result["second_echo"], "INVALID_TYPE")

    def test_overlapping_pages_are_not_qualified(self):
        result = diagnostic.diagnose(fixture(overlap=True))
        self.assertEqual(result["decision"], "PAGES_OVERLAP_NO_SAFE_SLICE")
        self.assertEqual(result["cross_page_overlap_count"], 1)

    def test_replay_source_drift_is_distinguished(self):
        result = diagnostic.diagnose(fixture(mutate_replay=True))
        self.assertEqual(result["decision"], "SOURCE_CHANGED_DURING_DIAGNOSTIC")
        self.assertFalse(result["first_replay_stable"])

    def test_no_client_route_or_output_of_episode_ids(self):
        source = PATH.read_text()
        self.assertIn('core.bridge_gate()', source)
        self.assertIn('core.make_reader(', source)
        self.assertIn('"/Users/Me"', source)
        self.assertNotIn("docker compose", source)
        self.assertNotIn("method='POST'", source)
        self.assertNotIn('print(i0)', source)
        self.assertNotIn('print(token)', source)
        self.assertIn("R4C_DIAG_SECRETS_PRINTED=NO", source)


if __name__ == "__main__":
    unittest.main()
