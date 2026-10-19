"""RC3-H aggregate NextUp visibility diagnostic regression tests (no HTTP)."""
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-nextup-visibility-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3h_visible", FILE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def rows(*series):
    return [{"SeriesId": s, "Id":"episode-"+str(i), "Type":"Episode"}
            for i,s in enumerate(series)]


GROUPS = [["tt44051354","tt44094505"],["tt11112222","tt33334444"]]
METADATA = {
    "canonical":"tt44051354",
    "alias":"tt44094505",
    "other":"tt99998888",
    "group2_alias":"tt33334444",
    "group2_canon":"tt11112222",
}


class VisibilityTests(unittest.TestCase):
    def test_one_canonical_and_no_alias_distinguishes_direct_drop_hypothesis(self):
        sample=rows("canonical","other","other")
        report=audit.summarize_visibility(sample, METADATA, GROUPS)
        self.assertEqual(report["canonical_cards"],1)
        self.assertEqual(report["alias_cards"],0)
        self.assertEqual(report["groups_canonical_only_in_window"],1)
        self.assertEqual(report["groups_both_spellings_in_window"],0)
        self.assertEqual(report["groups_absent_from_window"],1)
        self.assertEqual(report["outside_verified_alias_group_cards"],2)

    def test_co_visible_alias_does_not_invent_routing_or_merge(self):
        sample=rows("canonical","alias","group2_alias")
        report=audit.summarize_visibility(sample, METADATA, GROUPS)
        self.assertEqual(report["groups_both_spellings_in_window"],1)
        self.assertEqual(report["groups_alias_only_in_window"],1)
        self.assertEqual(report["canonical_cards"],1)
        self.assertEqual(report["alias_cards"],2)
        self.assertNotIn("tt44094505", str(report))

    def test_alias_only_does_not_claim_canonical_present(self):
        report=audit.summarize_visibility(
            rows("alias"), METADATA, [GROUPS[0]])
        self.assertEqual(report["groups_alias_only_in_window"],1)
        self.assertEqual(report["groups_canonical_only_in_window"],0)

    def test_unverified_metadata_never_counts_as_alias(self):
        report=audit.summarize_visibility(
            rows("unknown"), {"unknown":None}, [GROUPS[0]])
        self.assertEqual(report["valid_imdb_metadata_cards"],0)
        self.assertEqual(report["groups_absent_from_window"],1)

    def test_collision_between_verified_groups_fails_closed(self):
        with self.assertRaisesRegex(audit.core.StopAudit,"MULTI_OWNER_IDENTITY"):
            audit.summarize_visibility(
                rows("canonical"), METADATA,
                [["tt44051354","tt44094505"],["tt44094505","tt11112222"]],
            )

    def test_no_write_no_output_identifiers_or_untrusted_urls(self):
        src=FILE.read_text()
        self.assertIn("core.credential()",src)
        self.assertIn("core.bridge_gate()",src)
        self.assertIn('"/Users/Me"',src)
        self.assertIn('"/Shows/NextUp?StartIndex=0&Limit=50"',src)
        self.assertIn('"?mode=ro"',src)
        self.assertIn("PRAGMA query_only=ON",src)
        self.assertNotIn("docker compose",src)
        self.assertNotIn("method='POST'",src)
        self.assertNotIn("UPDATE watch_state",src)
        self.assertNotIn("cacheSet(",src)
        self.assertIn("RC3H_SECRETS_PRINTED=NO",src)


if __name__=="__main__":
    unittest.main()
