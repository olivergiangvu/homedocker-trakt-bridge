import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "monitor-identity-drift.py"
spec = importlib.util.spec_from_file_location("identity_drift_rc7", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class IdentityDriftMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.aio_path = str(Path(self.tmp.name) / "aio.sqlite")
        self.bridge_path = str(Path(self.tmp.name) / "bridge.sqlite")
        self.aio = sqlite3.connect(self.aio_path)
        self.bridge = sqlite3.connect(self.bridge_path)
        self.aio.executescript("""
            CREATE TABLE watch_sinks (
                uuid TEXT, persona TEXT, addon_name TEXT,
                addon_instance_id TEXT, retired_at INTEGER
            );
            CREATE TABLE watch_state (
                uuid TEXT, persona TEXT, item_key TEXT, kind TEXT, series_key TEXT,
                played INTEGER, position_ms INTEGER, match_key TEXT, sort_at INTEGER,
                dropped INTEGER, dropped_at INTEGER
            );
        """)
        self.bridge.executescript("""
            CREATE TABLE profiles (id TEXT, access_token_enc TEXT);
            CREATE TABLE media_cache (
                cache_key TEXT, payload_json TEXT, expires_at INTEGER
            );
        """)
        self.aio.execute("INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)", (
            "private-sink-uuid", "", monitor.SINK_NAME, monitor.SINK_INSTANCE
        ))
        self.bridge.execute("INSERT INTO profiles VALUES (?,?)", (
            "private-profile-id", "this-is-not-a-real-secret"
        ))
        self.aio.commit()
        self.bridge.commit()

    def tearDown(self):
        self.aio.close()
        self.bridge.close()
        self.tmp.cleanup()

    def episode(self, imdb, ep, played, pos, sort_at, *, uuid="private-sink-uuid",
                match=None, series_key=None):
        self.aio.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, "", f"e|{imdb}:1:{ep}", "episode", series_key or "s|" + imdb,
             int(played), pos, match, sort_at, 0, None)
        )
        self.aio.commit()

    def put_fixture(self):
        for ep in range(1, 10):
            self.episode(monitor.FIXTURE[2], ep, True, 0, 1000 + ep)
        self.episode(monitor.FIXTURE[2], 10, False, 346626, 1020)
        for ep in range(1, 9):
            self.episode(monitor.FIXTURE[1], ep, True, 0, 900 + ep)

    def put_drop(self, time=1100, dropped=True):
        self.aio.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("private-sink-uuid", "", "s|" + monitor.FIXTURE[1], "series",
             "s|" + monitor.FIXTURE[1], 0, 0, None, 0,
             int(dropped), time)
        )
        self.aio.commit()

    def run_audit(self):
        with contextlib.closing(monitor.ro(self.aio_path)) as aio, contextlib.closing(
            monitor.ro(self.bridge_path)
        ) as bridge:
            result = monitor.audit(aio, bridge)
        return result, result["pairs"][0]

    def test_b8_provenance_risk_without_drop(self):
        self.put_fixture()
        result, show = self.run_audit()
        self.assertEqual(show["status"], "SPLIT_TIMELINE_RISK")
        self.assertEqual(result["attention"], 1)
        self.assertEqual((show["alias_episode_rows"], show["canonical_episode_rows"]), (8, 10))
        self.assertEqual((show["alias_watched"], show["canonical_watched"]), (8, 9))
        self.assertEqual(show["canonical_resumes"], 1)
        self.assertEqual(result["scope_hash"],\n                         __import__("hashlib").sha256(b"private-sink-uuid\x1f").hexdigest()[:12])

    def test_after_successful_owner_side_drop_no_alert(self):
        self.put_fixture()
        self.put_drop(1100)
        result, show = self.run_audit()
        self.assertEqual(show["status"], "MITIGATED_BY_DROP")
        self.assertTrue(show["alias_drop_effective"])
        self.assertEqual(result["attention"], 0)

    def test_playback_newer_than_drop_is_recurring_risk(self):
        self.put_fixture()
        self.put_drop(950)
        result, show = self.run_audit()
        self.assertEqual(show["status"], "MITIGATED_BY_DROP")
        self.assertEqual(result["attention"], 0)
        self.aio.execute(
            "UPDATE watch_state SET sort_at=1500 WHERE series_key=? AND kind='episode'",
            ("s|" + monitor.FIXTURE[1],)
        )
        self.aio.commit()
        result, show = self.run_audit()
        self.assertEqual(show["status"], "SPLIT_TIMELINE_RISK")
        self.assertEqual(result["attention"], 1)

    def test_two_active_resume_timelines_require_review_even_if_dropped(self):
        self.put_fixture()
        self.put_drop()
        self.aio.execute(
            "UPDATE watch_state SET played=0, position_ms=40000 "
            "WHERE kind='episode' AND series_key=? AND item_key LIKE '%:1:8'",
            ("s|" + monitor.FIXTURE[1],)
        )
        self.aio.commit()
        _, show = self.run_audit()
        self.assertEqual(show["status"], "DUAL_RESUME_REVIEW")

    def test_missing_alias_history_is_not_labeled_as_split(self):
        self.episode(monitor.FIXTURE[2], 10, False, 346626, 1020)
        _, show = self.run_audit()
        self.assertEqual(show["status"], "SINGLE_TIMELINE")

    def test_legacy_local_tmdb_item_keys_are_detected_by_series_key(self):
        self.put_fixture()
        self.aio.execute(
            "UPDATE watch_state SET item_key=REPLACE(item_key, ?, ?)"
            " WHERE series_key=? AND kind='episode'",
            (monitor.FIXTURE[1], "tmdb:276470", "s|" + monitor.FIXTURE[1])
        )
        self.aio.commit()
        _, show = self.run_audit()
        self.assertEqual(show["alias_episode_rows"], 8)
        self.assertEqual(show["status"], "SPLIT_TIMELINE_RISK")

    def test_scope_is_strict_and_does_not_leak_other_user(self):
        self.put_fixture()
        self.episode("tt99999999", 1, False, 5000, 1500, uuid="other-user")
        result, _ = self.run_audit()
        self.assertNotIn("private-sink-uuid", json.dumps(result))
        self.assertNotIn("other-user", json.dumps(result))
        self.aio.execute(
            "INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)",
            ("other-user", "", monitor.SINK_NAME, monitor.SINK_INSTANCE)
        )
        self.aio.commit()
        with self.assertRaisesRegex(monitor.AuditError, "one active"):
            self.run_audit()

    def test_sqlite_connections_strictly_read_only(self):
        with contextlib.closing(monitor.ro(self.aio_path)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM watch_state")

    def test_learned_alias_requires_exact_provider_ids_and_fails_on_conflict(self):
        self.bridge.execute(
            "INSERT INTO media_cache VALUES (?,?,CAST(strftime('%s','now') AS INTEGER)+3600)",
            ("identity-alias:v1:private-profile-id", json.dumps({
                "shows": {"263102": {
                    "preferredMetaId": "tt44094505", "traktImdb": "tt44051354"
                }}
            }))
        )
        self.bridge.commit()
        _, show = self.run_audit()
        self.assertEqual(show["source"], "learned-bridge")
        self.bridge.execute(
            "UPDATE media_cache SET payload_json=?",
            (json.dumps({"shows": {"263102": {
                "preferredMetaId": "tt99999999", "traktImdb": "tt44051354"
            }}}),)
        )
        self.bridge.commit()
        with self.assertRaisesRegex(monitor.AuditError, "conflicts"):
            self.run_audit()

    def test_no_remote_or_autorepair_functions_exist(self):
        self.assertFalse(hasattr(monitor, "request"))
        self.assertFalse(hasattr(monitor, "apply"))
        self.assertFalse(hasattr(monitor, "undo"))


if __name__ == "__main__":
    unittest.main()
