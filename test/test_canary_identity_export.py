import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export-canary-identity-proof.py"
spec = importlib.util.spec_from_file_location("export_proof", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ExportProofTests(unittest.TestCase):
    def setup_db(self, path, *, alias="tt44094505", expired=False,
                 connected=True, extra=False, conflict_field=None,
                 other_missing_canonical=False, malformed_other=False):
        db = sqlite3.connect(path)
        db.executescript("""
        CREATE TABLE media_cache (
          cache_key TEXT PRIMARY KEY, payload_json TEXT, expires_at INTEGER
        );
        CREATE TABLE profiles (
          id TEXT PRIMARY KEY, access_token_enc TEXT
        );
        """)
        pid = "a" * 24
        db.execute("INSERT INTO profiles VALUES (?,?)",
                   (pid, "encrypted-placeholder" if connected else None))
        d = {
            "shows": {"263102": {
                "traktImdb": "tt44051354",
                "preferredMetaId": alias,
                "updatedAt": 1791940800,
            }}
        }
        if extra or conflict_field or other_missing_canonical or malformed_other:
            d["shows"]["777"] = {
                "traktImdb": None if other_missing_canonical else "tt77777777",
                "preferredMetaId": "tt88888888",
                "updatedAt": 1791940800,
            }
            if conflict_field:
                d["shows"]["777"][conflict_field] = (
                    "tt44094505" if conflict_field == "preferredMetaId"
                    else "tt44051354"
                )
            if malformed_other:
                d["shows"]["777"] = None
        db.execute("INSERT INTO media_cache VALUES (?,?,?)",
                   ("identity-alias:v1:" + pid, json.dumps(d),
                    int(time.time()) - 1 if expired else
                    int(time.time()) + 86400))
        db.commit()
        return db

    def test_verified_rc6_fixture_only_outputs_nonsecret_proof(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.setup_db(Path(td) / "fixture.sqlite")
            try:
                proof = exporter.proof_from_db(db)
                self.assertEqual(proof["contract"],
                                 "homedocker-canary-identity-proof-v1")
                self.assertEqual(proof["graph"]["shows"]["263102"]["aliases"],
                                 ["tt44094505"])
                raw = json.dumps(proof)
                self.assertNotIn("encrypted-placeholder", raw)
                self.assertNotIn("updatedAt", raw)
                self.assertNotIn("expires_at", raw)
                self.assertNotIn("watched", raw)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM media_cache")
            finally:
                db.close()

    def test_unrelated_legacy_shows_are_ignored_with_exact_export_only(self):
        for opts in [{"extra": True}, {"other_missing_canonical": True}]:
            with self.subTest(opts=opts), tempfile.TemporaryDirectory() as td:
                db = self.setup_db(Path(td) / "fixture.sqlite", **opts)
                try:
                    proof = exporter.proof_from_db(db)
                    self.assertEqual(
                        list(proof["graph"]["shows"]), ["263102"]
                    )
                    raw = json.dumps(proof)
                    self.assertNotIn("tt77777777", raw)
                    self.assertNotIn("tt88888888", raw)
                    self.assertNotIn("encrypted-placeholder", raw)
                finally:
                    db.close()

    def test_expired_or_disconnected_cannot_export(self):
        for opts in [{"expired": True}, {"connected": False},
                     {"alias": "tt99999999"},
                     {"conflict_field": "preferredMetaId"},
                     {"conflict_field": "traktImdb"},
                     {"malformed_other": True}]:
            with self.subTest(opts=opts), tempfile.TemporaryDirectory() as td:
                db = self.setup_db(Path(td) / "fixture.sqlite", **opts)
                try:
                    with self.assertRaises(exporter.Stop):
                        exporter.proof_from_db(db)
                finally:
                    db.close()

    def test_missing_or_other_legacy_owner_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(Path(td) / "fixture.sqlite")
            db.execute(
                "CREATE TABLE media_cache (cache_key TEXT,"
                "payload_json TEXT, expires_at INTEGER)"
            )
            db.execute(
                "CREATE TABLE profiles (id TEXT,access_token_enc TEXT)"
            )
            with self.assertRaises(exporter.Stop):
                exporter.proof_from_db(db)
            db.close()


if __name__ == "__main__":
    unittest.main()
