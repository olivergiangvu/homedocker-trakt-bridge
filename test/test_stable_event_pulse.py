import contextlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-stable-event-pulse.py"
spec = importlib.util.spec_from_file_location("stable_event_pulse", SCRIPT)
pulse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pulse)


class StablePulseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "bridge.db")
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE profiles (id TEXT, access_token_enc TEXT);
            CREATE TABLE event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT, event_id TEXT, event TEXT, status TEXT,
                detail TEXT, created_at INTEGER
            );
        """)
        self.db.execute("INSERT INTO profiles VALUES (?,?)",
                        ("private-profile", "private-secret-not-for-logs"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, event, status, detail, at=10000, profile="private-profile"):
        self.db.execute(
            "INSERT INTO event_log (profile_id,event,status,detail,created_at) VALUES (?,?,?,?,?)",
            (profile, event, status, json.dumps(detail), at)
        )
        self.db.commit()

    def run_report(self, now=12000, hours=1):
        with contextlib.closing(pulse.database(self.path)) as db:
            return pulse.audit(db, now=now, hours=hours)

    def test_signal_counts_are_bounded_and_no_private_details_appear(self):
        self.add("pull", "stale", {"upstreamError": "rate_limited", "retryAfter": 60})
        self.add("pause", "ignored", {"ignored": "terminal_pause_requires_completion"})
        self.add("pause", "error", {"upstreamStatus": 422, "message": "private show title"})
        self.add("reconcile", "ignored", {
            "action": "aio-reconcile:compare-settled",
            "decision": "trakt_same_or_newer", "writesTrakt": False,
            "videoId": "secret episode id",
        })
        self.add("canonical:verification", "stale", {"reason": "stale"})
        self.add("played", "ok", {"id": "secret event"})
        report = self.run_report()
        stats = report["counts"]
        self.assertEqual(stats["explicit_rate_limit_signals"], 1)
        self.assertEqual(stats["explicit_terminal_422_errors"], 1)
        self.assertEqual(stats["terminal_ignored"], 1)
        self.assertEqual(stats["canonical_verifications"], 1)
        self.assertEqual(stats["compare_terminal"], 1)
        self.assertEqual(report["compare_decisions"]["trakt_same_or_newer"], 1)
        self.assertEqual(report["database_writes"], 0)
        text = json.dumps(report)
        for leak in ("private-profile", "private-secret-not-for-logs",
                     "private show title", "secret episode id", "secret event"):
            self.assertNotIn(leak, text)

    def test_compare_unknown_and_write_flag_raise_observable_warning(self):
        self.add("reconcile", "ignored", {
            "action": "aio-reconcile:compare-settled",
            "decision": "unexpected",
            "writesTrakt": True,
        })
        report = self.run_report()
        self.assertEqual(report["counts"]["compare_write_safety_warning"], 1)
        self.assertEqual(report["counts"]["compare_unknown_decision"], 1)

    def test_scope_and_window_isolation(self):
        self.add("pause", "error", {"upstreamStatus": 422}, at=10000)
        self.add("pull", "error", {"upstreamStatus": 429}, at=100)
        self.add("pull", "error", {"upstreamStatus": 429}, at=10000,
                 profile="other-user")
        report = self.run_report()
        self.assertEqual(report["window_events"], 1)
        self.assertEqual(report["counts"]["explicit_terminal_422_errors"], 1)
        self.assertEqual(report["counts"].get("explicit_rate_limit_signals", 0), 0)

    def test_readonly_and_single_profile_guards(self):
        with contextlib.closing(pulse.database(self.path)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM event_log")
        self.db.execute("INSERT INTO profiles VALUES (?,?)",
                        ("another", "another-secret"))
        self.db.commit()
        with self.assertRaisesRegex(pulse.PulseError, "exactly one"):
            self.run_report()


if __name__ == "__main__":
    unittest.main()
