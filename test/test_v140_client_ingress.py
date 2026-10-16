import importlib.util
import json
import pathlib
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timezone

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-client-ingress.py"
spec = importlib.util.spec_from_file_location("client_path_audit_v140", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ClientIngressAudit(unittest.TestCase):
    NOW = datetime(2026, 10, 15, 3, 40, 0, tzinfo=timezone.utc)

    def test_classifies_exact_jellyfin_paths_without_exposing_queries(self):
        self.assertEqual(module.classify(
            "/jellyfin/Shows/NextUp?api_key=secret"), "jellyfin_nextup")
        self.assertEqual(module.classify(
            "/jellyfin/UserItems/Resume?StartIndex=20&token=secret"), "jellyfin_resume_modern")
        self.assertEqual(module.classify(
            "/Users/11111111abcdef00/Items/Resume"), "jellyfin_resume_legacy")
        self.assertEqual(module.classify(
            "/jellyfin/Items/opaqueid?token=secret"), "jellyfin_items_other")
        self.assertIsNone(module.classify("/api/private/native/nextup?token=secret"))

    def test_log_scan_redacts_secrets_and_does_not_attribute_client(self):
        with TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)
            rows = [
                '192.0.2.1 - - [15/Oct/2026:10:39:12 +0700] "GET /jellyfin/Shows/NextUp?api_key=ULTRASECRET HTTP/1.1" 200 123 "-" "Odin/1.4"',
                '192.0.2.2 - - [15/Oct/2026:10:39:13 +0700] "GET /jellyfin/UserItems/Resume?userId=PRIVATEUSER HTTP/1.1" 401 33 "-" "Mozilla/5.0"',
                '192.0.2.2 - - [15/Oct/2026:10:39:13 +0700] "GET /jellyfin/Users/abcde1234567/Items/Resume HTTP/1.1" 200 22 "-" "AIOStreams Desktop"',
                '192.0.2.3 - - [15/Oct/2026:10:39:14 +0700] "GET /api/private/native/nextup?token=SECRET HTTP/1.1" 200 22 "-" "Odin"',
                '192.0.2.4 - - [15/Oct/2026:08:00:00 +0700] "GET /jellyfin/Shows/NextUp HTTP/1.1" 200 22 "-" "Odin"',
            ]
            (path / "access.log").write_text("\n".join(rows) + "\n")
            (path / "system.log.gz").write_text("untrusted compressed logs")
            (path / "access.log.1").write_text("")
            result = module.audit(path, minutes=10, now=self.NOW)
        self.assertEqual(result["candidate_jellyfin_requests"], 3)
        self.assertEqual(result["nginx_log_files_scanned"], 2)
        self.assertEqual(result["api_path_groups"]["jellyfin_nextup"], 1)
        self.assertEqual(result["api_path_groups"]["jellyfin_resume_modern"], 1)
        self.assertEqual(result["api_path_groups"]["jellyfin_resume_legacy"], 1)
        self.assertEqual(result["status_families"]["4xx"], 1)
        self.assertEqual(result["untrusted_user_agent_hints"]["possible_odin"], 1)
        self.assertFalse(result["client_identification_confirmed"])
        self.assertFalse(result["gateway_routing_confirmed"])
        serialized = json.dumps(result)
        for private in (
            "ULTRASECRET", "PRIVATEUSER", "SECRET", "abcde1234567",
            "192.0.2.1", "Odin/1.4", "api_key", "access.log",
        ):
            self.assertNotIn(private, serialized)

    def test_symlinked_log_ignored(self):
        with TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            a = folder / "a.log"
            a.write_text("")
            (folder / "link.log").symlink_to(a)
            self.assertEqual(module.log_candidates(folder), [a])

    def test_invalid_window_fails_closed(self):
        for n in (-10, 0, 1441):
            with self.assertRaises(ValueError):
                module.audit(minutes=n)

if __name__ == "__main__":
    unittest.main()
