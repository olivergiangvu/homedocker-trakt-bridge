import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preview-v140-legacy-import.py"
spec = importlib.util.spec_from_file_location("legacy_preview_v140", SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
PROFILE = "a" * 24
OTHER = "b" * 24
NOW = 1792000000

def legacy(rows=None):
    return {
        "shows": rows or {
            "263102": {
                "traktImdb": "tt44051354",
                "preferredMetaId": "tt44094505",
                "updatedAt": NOW - 86400,
            }
        }
    }

def fixture(db, data=None, connected=True, graph=False, expiry=NOW + 3600):
    cx = sqlite3.connect(db)
    cx.executescript("""
        CREATE TABLE profiles (id TEXT PRIMARY KEY, access_token_enc TEXT);
        CREATE TABLE media_cache (cache_key TEXT PRIMARY KEY,
            payload_json TEXT, expires_at INTEGER);
    """)
    cx.execute("INSERT INTO profiles VALUES (?,?)",
               (PROFILE, "encrypted-secret" if connected else None))
    cx.execute("INSERT INTO media_cache VALUES (?,?,?)",
               ("identity-alias:v1:" + PROFILE,
                json.dumps(legacy() if data is None else data), expiry))
    if graph:
        cx.execute("INSERT INTO media_cache VALUES (?,?,?)",
                   ("identity-graph:v1:" + PROFILE,
                    '{"revision":1,"shows":{},"conflicts":[]}', expiry))
    cx.commit()
    cx.close()

class PreviewTests(unittest.TestCase):
    def test_ready_is_read_only_and_does_not_leak_alias_profile_or_token(self):
        with TemporaryDirectory() as temp:
            db = str(Path(temp) / "bridge.db")
            fixture(db)
            before = Path(db).read_bytes()
            state = preview.preview(db, NOW)
            after = Path(db).read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(state["status"], "DRY_RUN_READY")
        self.assertEqual(state["verified_show_candidates"], 1)
        self.assertEqual(state["alias_group_candidates"], 1)
        self.assertTrue(state["safe_to_consider_controlled_migration"])
        serialized = json.dumps(state)
        for secret in (PROFILE, "263102", "tt44051354", "tt44094505",
                       "encrypted-secret", "identity-alias:v1:"):
            self.assertNotIn(secret, serialized)
        self.assertFalse(state["migration_applied"])

    def test_preexisting_graph_not_overwritten(self):
        with TemporaryDirectory() as temp:
            db = str(Path(temp) / "bridge.db")
            fixture(db, graph=True)
            self.assertEqual(preview.preview(db, NOW)["status"],
                             "GRAPH_ALREADY_PRESENT_DO_NOT_OVERWRITE")

    def test_expired_legacy_fails_closed(self):
        with TemporaryDirectory() as temp:
            db = str(Path(temp) / "bridge.db")
            fixture(db, expiry=NOW-1)
            self.assertEqual(preview.preview(db, NOW)["status"],
                             "LEGACY_CACHE_EXPIRED")

    def test_disconnected_profile_fails_closed(self):
        with TemporaryDirectory() as temp:
            db = str(Path(temp) / "bridge.db")
            fixture(db, connected=False)
            self.assertEqual(preview.preview(db, NOW)["status"],
                             "PROFILE_NOT_CONNECTED")

    def test_cross_show_conflict_fails_closed(self):
        rows = legacy()["shows"]
        rows["263103"] = {
            "traktImdb": "tt11111111",
            "preferredMetaId": "tt44094505",
            "updatedAt": NOW - 86400,
        }
        state = preview.proposed_graph({"shows": rows})
        self.assertEqual(state[0], "CROSS_SHOW_IDENTITY_CONFLICT")

    def test_invalid_and_no_differing_aliases(self):
        self.assertEqual(preview.proposed_graph({"shows": {
            "263102": {"traktImdb": "garbage", "preferredMetaId": "tt44094505",
                       "updatedAt": NOW}
        }})[0], "INVALID_OR_UNVERIFIED_LEGACY_ROW")
        self.assertEqual(preview.proposed_graph({"shows": {
            "263102": {"traktImdb": "tt44051354", "preferredMetaId": "tt44051354",
                       "updatedAt": NOW}
        }})[0], "NO_DIFFERING_ALIASES")

    def test_multiple_connected_provenances_refuse_automatic_choice(self):
        with TemporaryDirectory() as temp:
            db = str(Path(temp) / "bridge.db")
            fixture(db)
            cx = sqlite3.connect(db)
            cx.execute("INSERT INTO media_cache VALUES (?,?,?)",
                       ("identity-alias:v1:" + OTHER,
                        json.dumps(legacy()), NOW + 3600))
            cx.commit()
            cx.close()
            self.assertEqual(preview.preview(db, NOW)["status"],
                             "LEGACY_PROFILE_COUNT_NOT_ONE")

    def test_symlinked_db_refused(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / "bridge.db"
            fixture(str(db))
            link = Path(temp) / "alias.db"
            link.symlink_to(db)
            with self.assertRaises(preview.PreviewFailure):
                preview.preview(str(link), NOW)

if __name__ == "__main__":
    unittest.main()
