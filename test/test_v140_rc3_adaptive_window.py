"""Offline tests for the optional authenticated AIO shadow candidate runner."""
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-adaptive-window-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3_adaptive_host", FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def item(n, series=None, ticks=0):
    return {"Id": "episode-" + str(n),
            "SeriesId": series or "series-" + str(n), "Type": "Episode",
            "UserData": {"PlaybackPositionTicks": ticks}}


def pages(count=50, both_active=False):
    rows = [item(i) for i in range(count)]
    rows[0] = item(0, "alias")
    rows[1] = item(1, "canonical", 20000)
    if both_active:
        rows[0]["UserData"]["PlaybackPositionTicks"] = 5000
    first = {"Items": rows[:20], "StartIndex": 0, "TotalRecordCount": 20}
    window = {"Items": rows, "StartIndex": 0, "TotalRecordCount": count}
    mapping = {"alias": "tt44094505", "canonical": "tt44051354"}
    graph = {"tt44094505": {"263102"}, "tt44051354": {"263102"}}
    return first, window, mapping, graph


class AdaptiveAuditTests(unittest.TestCase):
    def test_raw_full_50_proves_first_page_removal_candidate_not_total(self):
        p, w, meta, graph = pages()
        result = module.analyze(p, w, meta, graph)
        self.assertEqual(result["window_items"], 50)
        self.assertEqual(result["candidate_removed"], 1)
        self.assertTrue(result["first_page_visible_change"])
        self.assertTrue(result["able_to_fill_first_page"])
        self.assertFalse(result["total_known"])
        self.assertEqual(len(p["Items"]), 20)
        self.assertEqual(len(w["Items"]), 50)

    def test_short_25_window_observes_complete_looking_prefix_only(self):
        p, w, meta, graph = pages(25)
        result = module.analyze(p, w, meta, graph)
        self.assertEqual(result["candidate_removed"], 1)
        self.assertTrue(result["total_known"])

    def test_both_resumable_fails_open(self):
        p, w, meta, graph = pages(both_active=True)
        result = module.analyze(p, w, meta, graph)
        self.assertEqual(result["candidate_removed"], 0)
        self.assertFalse(result["first_page_visible_change"])

    def test_no_unverified_graph_mappings(self):
        p, w, meta, _ = pages()
        result = module.analyze(p, w, meta, {})
        self.assertEqual(result["candidate_removed"], 0)
        self.assertFalse(result["first_page_visible_change"])

    def test_conflict_quarantined_or_multi_owner_does_not_collapse(self):
        p, w, meta, graph = pages()
        graph["tt44094505"] = {"263102", "999"}
        result = module.analyze(p, w, meta, graph)
        self.assertEqual(result["candidate_removed"], 0)

    def test_mismatched_original_prefix_is_rejected(self):
        p, w, meta, graph = pages()
        p["Items"][0]["Id"] = "unexpected-id"
        with self.assertRaisesRegex(module.core.StopAudit, "WINDOW_PREFIX_UNSTABLE"):
            module.analyze(p, w, meta, graph)

    def test_only_root_only_get_and_read_only_sqlite_in_script(self):
        script = FILE.read_text()
        self.assertIn('?mode=ro', script)
        self.assertIn('PRAGMA query_only=ON', script)
        self.assertIn("core.bridge_gate()", script)
        self.assertIn('"/Users/Me"', script)
        self.assertIn('"/Shows/NextUp?StartIndex=0&Limit=50"', script)
        self.assertIn("MAX_ACTIVE = 4", script)
        self.assertNotIn("docker run", script)
        self.assertNotIn("docker compose", script)
        self.assertNotIn("db.execute('UPDATE", script)
        self.assertNotIn("TraktClient", script)
        self.assertIn("ADAPTIVE_SECRETS_PRINTED=NO", script)


if __name__ == "__main__":
    unittest.main()
