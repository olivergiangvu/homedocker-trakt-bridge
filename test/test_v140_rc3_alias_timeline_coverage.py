"""RC3-E coverage audit only: synthetic SQLite stores, no production DB, no network."""
import importlib.util
from pathlib import Path
import sqlite3
import json
import time
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-alias-timeline-coverage-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3e_coverage", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def store(rows, graph=None):
    aio = sqlite3.connect(":memory:")
    bridge = sqlite3.connect(":memory:")
    aio.executescript(
        "CREATE TABLE watch_sinks "
        "(uuid TEXT, persona TEXT, addon_name TEXT, addon_instance_id TEXT, retired_at INT);"
        "CREATE TABLE watch_state "
        "(uuid TEXT, persona TEXT, series_key TEXT, kind TEXT, played INT, position_ms INT);"
    )
    bridge.executescript(
        "CREATE TABLE profiles (id TEXT, access_token_enc TEXT);"
        "CREATE TABLE media_cache (cache_key TEXT, payload_json TEXT, expires_at INT);"
    )
    aio.execute("INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)",
                ("test-user", "test-persona", audit.SINK_NAME, audit.SINK_INSTANCE))
    bridge.execute("INSERT INTO profiles VALUES (?,?)", ("test-profile", "encrypted"))
    graph = graph or {"shows": {
        "1": {"canonicalImdb": "tt12345678", "aliases": ["tt87654321"]},
        "2": {"canonicalImdb": "tt11112222", "aliases": []},
    }, "conflicts": []}
    bridge.execute("INSERT INTO media_cache VALUES (?,?,?)", (
        "identity-graph:v1:test-profile", json.dumps(graph), int(time.time())+3600))
    aio.executemany("INSERT INTO watch_state VALUES (?,?,?,?,?,?)", [
        ("test-user", "test-persona", "s|" + imdb,
         "episode", played, pos)
        for imdb, played, pos in rows
    ])
    return aio, bridge


class CoverageTests(unittest.TestCase):
    def test_both_timelines_and_dual_resume_classified_but_not_as_nextup(self):
        aio, bridge = store([
            ("tt12345678", 1, 0),("tt87654321", 1, 0),
            ("tt12345678", 0, 1000),("tt87654321", 0, 2000),
        ])
        try:
            result = audit.aggregate_from_db(aio,bridge)
            self.assertEqual(result["graph_shows"], 2)
            self.assertEqual(result["verified_alias_groups"], 1)
            self.assertEqual(result["alias_groups_with_both_timelines"], 1)
            self.assertEqual(result["alias_groups_with_dual_active_resumes"], 1)
            self.assertEqual(result["canonical_episode_rows"], 2)
            self.assertEqual(result["alias_episode_rows"], 2)
            self.assertTrue(all(type(v) is int for v in result.values()))
            self.assertNotIn("tt12345678", str(result))
        finally:
            aio.close()
            bridge.close()

    def test_canonical_only_or_alias_only_distinguished(self):
        for sample, expected in [
            ([("tt12345678", 1, 0)], "alias_groups_canonical_timeline_only"),
            ([("tt87654321", 1, 0)], "alias_groups_alias_timeline_only"),
            ([], "alias_groups_with_no_episode_rows"),
        ]:
            with self.subTest(expected=expected):
                aio, bridge = store(sample)
                try:
                    report = audit.aggregate_from_db(aio,bridge)
                    self.assertEqual(report[expected],1)
                    self.assertEqual(report["alias_groups_with_both_timelines"],0)
                finally:
                    aio.close()
                    bridge.close()

    def test_user_scope_prevents_other_persona_spelling_from_counting(self):
        aio, bridge = store([("tt12345678", 1, 0)])
        aio.execute("INSERT INTO watch_state VALUES (?,?,?,?,?,?)", (
            "other-user", "test-persona", "s|tt87654321", "episode", 0, 500))
        try:
            result = audit.aggregate_from_db(aio,bridge)
            self.assertEqual(result["alias_groups_with_both_timelines"],0)
        finally:
            aio.close()
            bridge.close()

    def test_identity_conflict_prevents_any_timeline_collapse_claim(self):
        graph = {"shows":{
            "1":{"canonicalImdb":"tt12345678","aliases":["tt87654321"]},
            "2":{"canonicalImdb":"tt87654321","aliases":[]},
        },"conflicts":[]}
        with self.assertRaisesRegex(audit.AuditStop,"IDENTITY_GRAPH_CONFLICT"):
            audit.verified_alias_groups(graph)

    def test_quarantined_identity_is_not_claimed(self):
        graph = {"shows":{
            "1":{"canonicalImdb":"tt12345678","aliases":["tt87654321"]},
        },"conflicts":[{"id":"tt87654321","reason":"manual"}]}
        with self.assertRaisesRegex(audit.AuditStop,"IDENTITY_GRAPH_CONFLICT"):
            audit.verified_alias_groups(graph)

    def test_exact_sink_and_connected_profile_required(self):
        aio, bridge = store([])
        try:
            aio.execute("DELETE FROM watch_sinks")
            with self.assertRaisesRegex(audit.AuditStop,"WATCH_SINK_NOT_UNIQUE"):
                audit.aggregate_from_db(aio,bridge)
        finally:
            aio.close()
            bridge.close()

    def test_code_enforces_read_only_and_no_output_identifiers(self):
        text = PATH.read_text()
        self.assertIn('"?mode=ro"', text)
        self.assertIn('PRAGMA query_only=ON', text)
        self.assertIn('SINK_INSTANCE = "e3fe3b0"', text)
        self.assertNotIn("docker exec", text)
        self.assertNotIn("requests.post", text)
        self.assertNotIn("db.execute('UPDATE", text)
        self.assertIn("RC3E_SECRETS_PRINTED=NO", text)


if __name__ == "__main__":
    unittest.main()
