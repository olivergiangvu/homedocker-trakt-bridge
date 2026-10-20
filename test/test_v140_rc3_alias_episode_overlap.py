"""RC3-F offline-only tests: episode coordinate overlap without changing watch-state."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE_TEST = ROOT / "test/test_v140_rc3_alias_timeline_coverage.py"
core_spec = importlib.util.spec_from_file_location("rc3e_testbase", CORE_TEST)
fixtures = importlib.util.module_from_spec(core_spec)
core_spec.loader.exec_module(fixtures)

SCRIPT = ROOT / "scripts/audit-v140-rc3-alias-episode-overlap-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3f_script", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def setup(rows, include_other_user=False):
    aio, bridge = fixtures.store([])
    aio.execute("ALTER TABLE watch_state ADD COLUMN season INTEGER")
    aio.execute("ALTER TABLE watch_state ADD COLUMN episode INTEGER")
    aio.executemany(
        "INSERT INTO watch_state "
        "(uuid, persona, series_key, kind, played, position_ms, season, episode) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [("test-user", "test-persona", "s|" + imdb, "episode", played,
          position, season, ep)
         for imdb, season, ep, played, position in rows],
    )
    if include_other_user:
        aio.execute(
            "INSERT INTO watch_state "
            "(uuid, persona, series_key, kind, played, position_ms, season, episode) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("other-user", "test-persona", "s|tt87654321", "episode", 0, 44, 5, 4),
        )
    return aio, bridge


class OverlapTests(unittest.TestCase):
    def test_observes_11_and_8_rows_with_partial_coordinate_overlap(self):
        rows = [("tt12345678", 1, n, 1, 0) for n in range(1, 12)]
        rows += [("tt87654321", 1, n, 1, 0) for n in range(5, 13)]
        aio, bridge = setup(rows)
        try:
            report = audit.aggregate_overlap(aio, bridge)
            self.assertEqual(report["verified_alias_groups"], 1)
            self.assertEqual(report["canonical_unique_episodes"], 11)
            self.assertEqual(report["alias_unique_episodes"], 8)
            self.assertEqual(report["overlap_episodes"], 7)
            self.assertEqual(report["canonical_only_episodes"], 4)
            self.assertEqual(report["alias_only_episodes"], 1)
            self.assertEqual(report["groups_with_exact_episode_overlap"], 1)
            self.assertEqual(report["overlap_played_disagreements"], 0)
            self.assertEqual(report["overlap_with_both_active_resumes"], 0)
            self.assertTrue(all(type(v) is int for v in report.values()))
            self.assertNotIn("tt87654321", str(report))
        finally:
            aio.close()
            bridge.close()

    def test_different_episode_coordinates_are_not_false_duplication(self):
        rows = [
            ("tt12345678", 1, 1, 1, 0),
            ("tt87654321", 2, 1, 1, 0),
        ]
        aio, bridge = setup(rows)
        try:
            report = audit.aggregate_overlap(aio, bridge)
            self.assertEqual(report["overlap_episodes"], 0)
            self.assertEqual(report["groups_without_exact_episode_overlap"], 1)
        finally:
            aio.close()
            bridge.close()

    def test_conflicting_played_and_active_resumes_reported_but_never_modified(self):
        rows = [
            ("tt12345678", 1, 3, 1, 0),
            ("tt87654321", 1, 3, 0, 200),
            ("tt12345678", 1, 4, 0, 90),
            ("tt87654321", 1, 4, 0, 40),
        ]
        aio, bridge = setup(rows)
        try:
            result = audit.aggregate_overlap(aio,bridge)
            self.assertEqual(result["overlap_episodes"], 2)
            self.assertEqual(result["overlap_played_disagreements"], 1)
            self.assertEqual(result["overlap_with_any_active_resume"], 2)
            self.assertEqual(result["overlap_with_both_active_resumes"], 1)
            self.assertEqual(aio.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0],4)
        finally:
            aio.close()
            bridge.close()

    def test_scope_other_user_is_ignored(self):
        aio, bridge = setup([("tt12345678", 5, 4, 1, 0)],include_other_user=True)
        try:
            result = audit.aggregate_overlap(aio, bridge)
            self.assertEqual(result["overlap_episodes"], 0)
            self.assertEqual(result["canonical_unique_episodes"], 1)
            self.assertEqual(result["alias_unique_episodes"], 0)
        finally:
            aio.close()
            bridge.close()

    def test_repeated_episode_coordinate_blocks_automated_reconciliation(self):
        rows = [("tt12345678", 1, 3, 1, 0),
                ("tt12345678", 1, 3, 1, 0)]
        aio, bridge = setup(rows)
        try:
            with self.assertRaisesRegex(audit.core.AuditStop,
                                        "AMBIGUOUS_EPISODE_COORDINATES"):
                audit.aggregate_overlap(aio, bridge)
        finally:
            aio.close()
            bridge.close()

    def test_null_season_never_claims_a_valid_overlap(self):
        rows = [("tt12345678", None, 3, 1, 0),
                ("tt87654321", 1, 3, 1, 0)]
        aio, bridge = setup(rows)
        try:
            report = audit.aggregate_overlap(aio, bridge)
            self.assertEqual(report["invalid_coordinate_rows"], 1)
            self.assertEqual(report["overlap_episodes"], 0)
        finally:
            aio.close()
            bridge.close()

    def test_no_network_and_no_sqlite_writes_in_script(self):
        src = SCRIPT.read_text()
        self.assertIn("core.ro(core.AIO_DB)", src)
        self.assertIn("core.ro(core.BRIDGE_DB)", src)
        self.assertNotIn("urllib.request", src)
        self.assertNotIn("requests.get", src)
        self.assertNotIn("requests.post", src)
        self.assertNotIn("docker exec", src)
        self.assertNotIn("UPDATE watch_state", src)
        self.assertNotIn("DELETE FROM watch_state", src)
        self.assertIn("RC3F_SECRETS_PRINTED=NO", src)


if __name__ == "__main__":
    unittest.main()
