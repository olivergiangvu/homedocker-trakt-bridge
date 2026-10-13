import contextlib
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-known-show-nextup-origin.py"
spec = importlib.util.spec_from_file_location("b8_aio_source", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class KnownShowNextUpSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "aio.db")
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE watch_sinks (
                uuid TEXT, persona TEXT, addon_name TEXT,
                addon_instance_id TEXT, retired_at INTEGER
            );
            CREATE TABLE watch_state (
                uuid TEXT, persona TEXT, item_key TEXT, match_key TEXT,
                base_id TEXT, series_key TEXT, video_id TEXT,
                season INTEGER, episode INTEGER, played INTEGER,
                position_ms INTEGER, sort_at INTEGER, origin TEXT
            );
        """)
        self.db.execute("INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)", (
            "primary-private-uuid", "", "homedocker-trakt-bridge", "e3fe3b0"
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, base, episode, played, position=0, uuid="primary-private-uuid",
            item_base=None, match=None):
        item_base = item_base or base
        self.db.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, "", f"e|{item_base}:1:{episode}", match, base, f"s|{base}",
             f"{item_base}:1:{episode}", 1, episode, played,
             position, 1000 + episode, "import" if played else "local"),
        )
        self.db.commit()

    def run_audit(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.closing(audit.connect(self.path)) as ro:
                audit.audit(ro)
        return out.getvalue()

    def test_two_series_keys_detected_without_cross_scope_leak(self):
        self.add("tt44051354", 9, 1)
        self.add("tt44051354", 10, 0, 346626)
        self.add("tt44094505", 8, 1)
        self.add("tt99999999", 1, 1)
        self.add("tt44094505", 2, 1, uuid="someone-else")
        text = self.run_audit()
        self.assertIn("SERIES_KEY_GROUP_COUNT=2", text)
        self.assertIn("CANDIDATE_ROWS=3", text)
        self.assertIn("s|tt44051354", text)
        self.assertIn("s|tt44094505", text)
        self.assertNotIn("tt99999999", text)
        self.assertNotIn("primary-private-uuid", text)
        self.assertNotIn("someone-else", text)

    def test_real_home_server_18_row_dual_progression_regression(self):
        # Read-only synthetic reproduction of 2026-10-13 B8 output:
        # Trakt IMDb watched S01E01..E09 + E10 resume, AIO IMDb had
        # local TMDB video IDs watched only through E08. No match keys.
        for ep in range(1, 10):
            self.add("tt44051354", ep, 1)
        self.add("tt44051354", 10, 0, 346626)
        for ep in range(1, 9):
            self.add("tt44094505", ep, 1, item_base="tmdb:276470")
        output = self.run_audit()
        self.assertIn("CANDIDATE_ROWS=18", output)
        self.assertIn("SERIES_KEY_GROUP_COUNT=2", output)
        self.assertIn(
            "SERIES_GROUP KEY=s|tt44051354 ROWS=10 WATCHED=9 UNFINISHED=1",
            output
        )
        self.assertIn(
            "SERIES_GROUP KEY=s|tt44094505 ROWS=8 WATCHED=8 UNFINISHED=0",
            output
        )
        self.assertIn("ANCHOR_EP=S01E10", output)
        self.assertIn("ANCHOR_EP=S01E08", output)
        self.assertIn(
            "ROW KEY=e|tmdb:276470:1:8 MATCH=- BASE=tt44094505",
            output
        )
        self.assertIn(
            "ROW KEY=e|tt44051354:1:10 MATCH=- BASE=tt44051354",
            output
        )

    def test_other_source_provider_key_is_considered(self):
        self.add("tmdb:276470", 10, 0, 12000)
        text = self.run_audit()
        self.assertIn("SERIES_KEY_GROUP_COUNT=1", text)
        self.assertIn("s|tmdb:276470", text)

    def test_match_key_path_is_considered(self):
        self.add("tt55555555", 10, 0, 12000,
                 match="e|tt44051354:1:10")
        text = self.run_audit()
        self.assertIn("CANDIDATE_ROWS=1", text)
        self.assertIn("MATCH=e|tt44051354:1:10", text)
        self.assertIn("BASE=tt55555555", text)

    def test_rejects_ambiguous_scope(self):
        self.db.execute("INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)", (
            "second", "", "homedocker-trakt-bridge", "e3fe3b0"
        ))
        self.db.commit()
        with self.assertRaisesRegex(SystemExit, "exactly one active"):
            self.run_audit()

    def test_sqlite_connection_is_query_only(self):
        with contextlib.closing(audit.connect(self.path)) as ro:
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute("DELETE FROM watch_state")

    def test_masks_unknown_key(self):
        self.assertTrue(audit.safe_key("private-random").startswith("opaque:"))
        self.assertEqual(audit.safe_key("s|tt44051354"), "s|tt44051354")
        self.assertEqual(audit.safe_key("tt44094505"), "tt44094505")


if __name__ == "__main__":
    unittest.main()
