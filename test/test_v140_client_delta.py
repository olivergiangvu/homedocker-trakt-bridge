import importlib.util
import json
import pathlib
from tempfile import TemporaryDirectory
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-client-delta.py"
spec = importlib.util.spec_from_file_location("client_delta_v140", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ClientDeltaTest(unittest.TestCase):
    def test_only_new_appended_requests_and_exact_status_304_302(self):
        with TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            log = root / "access.log"
            pre = '192.0.2.3 - - [15/Oct/2026:10:39:12 +0700] "GET /jellyfin/Shows/NextUp?api_key=OLDSECRET HTTP/1.1" 200 123 "-" "Odin/1.0"\n'
            log.write_text(pre)
            previous = module.snapshot(root)
            extra = [
                '192.0.2.1 - - [15/Oct/2026:10:41:12 +0700] "GET /jellyfin/Shows/NextUp?api_key=ULTRASECRET HTTP/1.1" 304 0 "-" "Odin/9.9"',
                '192.0.2.2 - - [15/Oct/2026:10:41:13 +0700] "GET /jellyfin/UserItems/Resume?userId=PRIVATEUSER HTTP/1.1" 302 0 "-" "Odin/9.9"',
                '192.0.2.3 - - [15/Oct/2026:10:41:14 +0700] "GET /jellyfin/Items/abcdef23?token=SECRET HTTP/1.1" 200 20 "-" "AIOStreams Desktop"',
                '192.0.2.4 - - [15/Oct/2026:10:41:15 +0700] "GET /api/native?token=SECRET HTTP/1.1" 200 30 "-" "Odin"',
            ]
            with log.open("a") as writer:
                writer.write("\n".join(extra) + "\n")
            result = module.compare(root, previous)
        self.assertTrue(result["observation_complete"])
        self.assertEqual(result["new_jellyfin_candidate_requests"], 3)
        self.assertEqual(result["api_path_groups"]["jellyfin_nextup"], 1)
        self.assertEqual(result["api_path_groups"]["jellyfin_resume_modern"], 1)
        self.assertEqual(result["exact_status_counts"], {"200": 1, "302": 1, "304": 1})
        self.assertEqual(result["untrusted_user_agent_hints"]["possible_odin"], 2)
        self.assertFalse(result["client_identity_confirmed"])
        self.assertFalse(result["gateway_routing_confirmed"])
        output = json.dumps(result)
        for secret in ("ULTRASECRET", "PRIVATEUSER", "OLDSECRET",
                       "192.0.2", "Odin/9.9", "Items/abcdef23"):
            self.assertNotIn(secret, output)

    def test_changed_inode_or_file_removal_is_incomplete(self):
        with TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            file = root / "access.log"
            file.write_text("old\n")
            initial = module.snapshot(root)
            file.unlink()
            result = module.compare(root, initial)
            self.assertFalse(result["observation_complete"])
            self.assertEqual(result["files_changed_or_rotated"], 1)

    def test_new_logs_mark_incomplete_instead_of_claiming_zero(self):
        with TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "a.log").write_text("one\n")
            baseline = module.snapshot(root)
            (root / "b.log").write_text("two\n")
            result = module.compare(root, baseline)
            self.assertFalse(result["observation_complete"])
            self.assertEqual(result["files_created_since_snapshot"], 1)

    def test_oversized_delta_skips_unbounded_reads(self):
        with TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            p = root / "access.log"
            p.write_text("one\n")
            baseline = module.snapshot(root)
            with p.open("ab") as out:
                out.write(b"x" * (module.MAX_APPENDED_BYTES + 1))
            result = module.compare(root, baseline)
            self.assertFalse(result["observation_complete"])
            self.assertEqual(result["files_over_capacity"], 1)
            self.assertEqual(result["new_jellyfin_candidate_requests"], 0)

    def test_midline_boundary_does_not_count_spliced_entry(self):
        with TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            p = root / "access.log"
            p.write_text('192.0.2.1 "GET /jellyfin/Shows/NextUp HTTP/')
            baseline = module.snapshot(root)
            with p.open("a") as out:
                out.write('1.1" 200 12 "-" "Odin"\n')
                out.write('192.0.2.1 - - [15/Oct/2026:10:39:12 +0700] "GET /jellyfin/UserItems/Resume HTTP/1.1" 200 12 "-" "Odin"\n')
            result = module.compare(root, baseline)
            self.assertTrue(result["observation_complete"])
            self.assertEqual(result["new_jellyfin_candidate_requests"], 1)
            self.assertEqual(result["api_path_groups"], {"jellyfin_resume_modern": 1})

if __name__ == "__main__":
    unittest.main()
