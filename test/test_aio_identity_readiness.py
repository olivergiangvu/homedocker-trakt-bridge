from contextlib import closing
import importlib.util
import sqlite3
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-aio-identity-readiness.py"
spec = importlib.util.spec_from_file_location("aio_identity_readiness", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AioReadinessTests(unittest.TestCase):
    def test_exact_sink_scoped_aggregate_never_leaks_uuid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "aio.sqlite"
            with closing(sqlite3.connect(p)) as db:
                db.executescript("""
                    CREATE TABLE watch_sinks (
                      uuid TEXT, persona TEXT, addon_name TEXT,
                      addon_instance_id TEXT, retired_at INTEGER
                    );
                    CREATE TABLE watch_state (
                      uuid TEXT, persona TEXT, series_key TEXT,
                      item_key TEXT, match_key TEXT, kind TEXT,
                      played INTEGER, position_ms INTEGER
                    );
                """)
                db.execute("INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)",
                    ("private-uuid", "", "homedocker-trakt-bridge", "e3fe3b0"))
                for i in range(8):
                    db.execute("INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?)",
                        ("private-uuid", "", "s|tt44094505",
                         "e|private-%s" % i, None, "episode", 1, 0))
                for i in range(10):
                    db.execute("INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?)",
                        ("private-uuid", "", "s|tt44051354",
                         "e|canonical-%s" % i, None, "episode",
                         int(i < 9), 45000 if i == 9 else 0))
                db.execute("INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?)",
                    ("other-user", "other", "s|tt44094505", "e|other",
                     None, "episode", 0, 999))
                db.commit()
            with closing(module.read_only(str(p))) as db:
                result = module.audit_db(db)
                self.assertEqual(result["tt44094505"]["episodes"], 8)
                self.assertEqual(result["tt44051354"]["episodes"], 10)
                self.assertEqual(result["tt44051354"]["active_resume_rows"], 1)
                self.assertEqual(result["tt44094505"]["null_match_keys"], 8)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM watch_state")
            self.assertNotIn("private-uuid", str(result))
            self.assertNotIn("other-user", str(result))

    def test_whitelist_only_boolean_configuration(self):
        info = [{
            "Image": "sha256:abcdef123456789",
            "Config": {
                "Env": [
                    "TRAKT_CLIENT_SECRET=top-secret",
                    "ID_MAPPINGS_ENABLED=true",
                    "WATCH_STATE_PULL_ENABLED=false",
                ]
            }
        }]
        result = module.flags_from_container(info)
        self.assertEqual(result["id_mappings"], "enabled")
        self.assertEqual(result["watch_state_pull"], "disabled")
        self.assertEqual(result["watch_state_report"],
                         "default_enabled_per_upstream_source")
        self.assertNotIn("top-secret", str(result))

    def test_rejects_missing_match_key_schema(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "broken.sqlite"
            with closing(sqlite3.connect(p)) as db:
                db.execute("CREATE TABLE watch_state(uuid TEXT)")
                db.commit()
            with closing(module.read_only(str(p))) as db:
                with self.assertRaises(module.AuditFailure):
                    module.audit_db(db)


if __name__ == "__main__":
    unittest.main()
