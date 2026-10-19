"""RC3-G: exact scoped AIO source-candidate ranking read-only tests."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/audit-v140-rc3-nextup-candidate-rank-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3g_rank", SOURCE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
FIXTURES = ROOT / "test/test_v140_rc3_alias_timeline_coverage.py"
f_spec = importlib.util.spec_from_file_location("rc3e_fixtures_for_g", FIXTURES)
fixtures = importlib.util.module_from_spec(f_spec)
f_spec.loader.exec_module(fixtures)


def store(*, only_canonical=False, dropped_at=None, extra_series=0):
    rows = [("tt12345678", 1, 0)]
    rows.append(("tt87654321", 0, 0) if only_canonical else ("tt87654321", 1, 0))
    aio, bridge = fixtures.store(rows)
    for col in ("sort_at INTEGER", "item_key TEXT", "match_key TEXT",
                "dropped INTEGER", "dropped_at INTEGER"):
        aio.execute("ALTER TABLE watch_state ADD COLUMN " + col)
    aio.execute(
        "UPDATE watch_state SET sort_at=100, item_key='e|fixture1', dropped=0 "
        "WHERE series_key='s|tt12345678'"
    )
    aio.execute(
        "UPDATE watch_state SET sort_at=90, item_key='e|fixture2', dropped=0 "
        "WHERE series_key='s|tt87654321'"
    )
    aio.execute("ALTER TABLE watch_state ADD COLUMN episode INTEGER")
    aio.execute("UPDATE watch_state SET episode=1")
    if extra_series:
        aio.executemany(
            "INSERT INTO watch_state "
            "(uuid,persona,series_key,kind,played,position_ms,sort_at,episode,dropped) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [("test-user", "test-persona", "s|dummy-" + str(i), "episode",
              1, 0, 1000 + i, 1, 0) for i in range(extra_series)],
        )
    if dropped_at is not None:
        aio.execute(
            "INSERT INTO watch_state "
            "(uuid,persona,item_key,kind,played,position_ms,dropped,dropped_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("test-user", "test-persona", "s|tt87654321", "series",
             0, 0, 1, dropped_at),
        )
    return aio, bridge


class RankTests(unittest.TestCase):
    def test_both_verified_spellings_in_watched_ranking(self):
        aio, bridge = store()
        try:
            result = audit.aggregate_candidate_rank(aio, bridge)
            self.assertEqual(result["verified_alias_groups"], 1)
            self.assertEqual(result["ranked_candidate_series"], 2)
            self.assertEqual(result["groups_with_both_candidate_spellings"], 1)
            self.assertEqual(result["groups_with_both_ranked_within_200"], 1)
            self.assertEqual(result["single_group_canonical_band"], "1_TO_50")
            self.assertEqual(result["single_group_alias_band"], "1_TO_50")
            self.assertEqual(result["single_group_effective_drop_count"], 0)
            self.assertNotIn("tt87654321", str(result))
        finally:
            aio.close()
            bridge.close()

    def test_watch_state_row_without_played_or_position_not_candidate(self):
        aio, bridge = store(only_canonical=True)
        try:
            result = audit.aggregate_candidate_rank(aio, bridge)
            self.assertEqual(result["groups_with_only_one_candidate_spelling"], 1)
            self.assertEqual(result["single_group_alias_band"], "NO_WATCHED_CANDIDATE")
        finally:
            aio.close()
            bridge.close()

    def test_candidate_after_200_is_not_claimed_nextup(self):
        aio, bridge = store(extra_series=210)
        try:
            result = audit.aggregate_candidate_rank(aio, bridge)
            self.assertEqual(result["groups_with_both_candidate_spellings"], 1)
            self.assertEqual(result["groups_with_both_ranked_within_200"], 0)
            self.assertEqual(result["single_group_canonical_band"], "BEYOND_200")
            self.assertEqual(result["single_group_alias_band"], "BEYOND_200")
        finally:
            aio.close()
            bridge.close()

    def test_new_drop_effective_old_drop_not(self):
        for at, expected in ((300,1), (5,0)):
            with self.subTest(drop_time=at):
                aio, bridge = store(dropped_at=at)
                try:
                    result = audit.aggregate_candidate_rank(aio, bridge)
                    self.assertEqual(result["single_group_effective_drop_count"], expected)
                    self.assertEqual(result["groups_with_any_effective_series_drop"], expected)
                finally:
                    aio.close()
                    bridge.close()

    def test_scope_does_not_count_other_users(self):
        aio, bridge = store(only_canonical=True)
        try:
            aio.execute(
                "INSERT INTO watch_state "
                "(uuid,persona,series_key,kind,played,position_ms,sort_at,episode) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("other-user","test-persona","s|tt87654321","episode",1,0,5000,1),
            )
            result = audit.aggregate_candidate_rank(aio, bridge)
            self.assertEqual(result["single_group_alias_band"], "NO_WATCHED_CANDIDATE")
        finally:
            aio.close()
            bridge.close()

    def test_no_http_no_mutation_and_untracked_cli_credentials(self):
        script = SOURCE.read_text()
        self.assertIn("core.ro(core.AIO_DB)", script)
        self.assertIn("core.ro(core.BRIDGE_DB)", script)
        self.assertIn("RC3G_SECRETS_PRINTED=NO", script)
        self.assertNotIn("urllib.request", script)
        self.assertNotIn("docker exec", script)
        self.assertNotIn("UPDATE watch_state", script)
        self.assertNotIn("DELETE FROM watch_state", script)


if __name__ == "__main__":
    unittest.main()
