"""Offline regression tests for RC3 host-only authenticated shadow auditor."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-nextup-shadow-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3_live_shadow", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def sample(size, *, mode="prefix", ignore_offset=False, drift=False):
    rows = [
        {"Id": "episode-" + str(i), "SeriesId": "series-" + str(i),
         "Type": "Episode", "UserData": {"PlaybackPositionTicks": 0}}
        for i in range(size)
    ]
    queries = []

    def read(path):
        from urllib.parse import urlsplit, parse_qs
        q = parse_qs(urlsplit(path).query)
        start = int(q["StartIndex"][0])
        limit = int(q["Limit"][0])
        queries.append((start, limit))
        page_rows = [dict(x) for x in rows[start:start + limit]]
        if drift and len(queries) == 3:
            page_rows[0]["Id"] = "different-episode-id"
        return {
            "StartIndex": 0 if ignore_offset else start,
            "Items": page_rows,
            "TotalRecordCount": size if mode == "global" else start + len(page_rows),
            "Count": len(page_rows),
        }

    return read, queries


class ShadowAuditTest(unittest.TestCase):
    def test_prefix_20_plus_5_and_replay(self):
        reader, queries = sample(25)
        result = audit.scan_pages(reader)
        self.assertEqual(result["status"], "SHADOW_STABLE_SHORT_TAIL_ONLY")
        self.assertEqual(result["counts"], [20, 5])
        self.assertEqual(result["requests"], 4)
        self.assertEqual(queries, [(0, 20), (20, 20), (0, 20), (20, 20)])

    def test_global_20_plus_5_and_replay(self):
        reader, _ = sample(25, mode="global")
        result = audit.scan_pages(reader)
        self.assertEqual(result["counts"], [20, 5])

    def test_full_paging_window_is_unproven(self):
        reader, queries = sample(80)
        result = audit.scan_pages(reader)
        self.assertEqual(result["status"], "UNPROVEN_FULL_WINDOW")
        self.assertEqual(result["counts"], [20, 20, 20, 20])
        self.assertEqual(len(queries), 4)

    def test_offset_not_honored(self):
        reader, _ = sample(30, ignore_offset=True)
        with self.assertRaisesRegex(audit.StopAudit, "STARTINDEX_NOT_HONORED"):
            audit.scan_pages(reader)

    def test_missing_startindex_is_distinguished_from_ignored_offset(self):
        reader, _ = sample(30)
        def no_echo(path):
            reply = reader(path)
            reply.pop("StartIndex")
            return reply
        with self.assertRaisesRegex(audit.StopAudit, "STARTINDEX_FIELD_ABSENT"):
            audit.scan_pages(no_echo)

    def test_startindex_type_bool_never_counts_as_zero(self):
        reader, _ = sample(30)
        def invalid_echo(path):
            reply = reader(path)
            reply["StartIndex"] = False
            return reply
        with self.assertRaisesRegex(audit.StopAudit, "STARTINDEX_ECHO_INVALID"):
            audit.scan_pages(invalid_echo)

    def test_full_page_replay_drift(self):
        reader, _ = sample(25, drift=True)
        with self.assertRaisesRegex(audit.StopAudit, "PAGINATION_SNAPSHOT_DRIFT"):
            audit.scan_pages(reader)

    def test_duplicate_episode_ids(self):
        reader, _ = sample(25)
        def broken(path):
            row = reader(path)
            if row["StartIndex"] == 20:
                row["Items"][0]["Id"] = "episode-0"
            return row
        with self.assertRaisesRegex(audit.StopAudit, "EPISODE_OVERLAP"):
            audit.scan_pages(broken)

    def test_only_authenticated_get_with_localhost_and_no_bearer_output(self):
        script = SCRIPT.read_text()
        self.assertIn('AIO = "http://127.0.0.1:3240/jellyfin"', script)
        self.assertIn('method="GET"', script)
        self.assertNotIn('method="POST"', script)
        self.assertNotIn("requests.post", script)
        self.assertNotIn("docker compose", script)
        self.assertNotIn("docker run", script)
        self.assertNotIn("cacheSet(", script)
        self.assertIn("NO", script)
        self.assertIn("R4C_SECRETS_PRINTED=NO", script)


if __name__ == "__main__":
    unittest.main()
