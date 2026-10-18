"""G2-1B Odin shadow launcher: no-host, no-auth, no-listener unit tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-v140-g2-odin-shadow-host.py"
spec = importlib.util.spec_from_file_location("g2_shadow", SCRIPT)
g2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g2)


class OdinShadowLauncherTests(unittest.TestCase):
    def test_live_login_without_distinct_consent_stops_before_baseline(self):
        completed = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                    "--mode", "login"], capture_output=True,
                                   text=True, timeout=10)
        self.assertEqual(completed.returncode, 5)
        self.assertIn("G2_SHADOW_STOP_EXPLICIT_ONE_AIO_LOGIN_REQUIRED",
                      completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_live_existing_token_without_distinct_consent_stops(self):
        completed = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                    "--mode", "existing-token"],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 5)
        self.assertIn("G2_SHADOW_STOP_EXPLICIT_EXISTING_TOKEN_REQUIRED",
                      completed.stderr)

    def test_preflight_cannot_use_live_consent(self):
        completed = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                    "--mode", "preflight", "--confirm",
                                    g2.CONFIRM_LOGIN],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 5)
        self.assertIn("G2_SHADOW_STOP_PREFLIGHT_MUST_NOT_HAVE_CONFIRM",
                      completed.stderr)

    def test_preflight_contract_does_not_authenticate_or_spawn(self):
        sample = {"shows": 17, "groups": 1}
        with patch.object(g2, "baseline", return_value=(sample, None, "node")), \
             patch.object(g2, "authenticate") as authentication, \
             patch.object(g2, "run_operator") as launch, \
             patch.object(g2.sys, "argv",
                          [str(SCRIPT), "--mode", "preflight"]):
            result = g2.main()
        authentication.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(result["status"], "SHADOW_PREFLIGHT_PASS_NO_LISTENER")
        self.assertTrue(result["host_node_v24_ready"])
        self.assertEqual(result["alias_groups"], 1)
        self.assertEqual(result["aio_login_posts"], 0)
        self.assertFalse(result["new_listener_started"])
        self.assertEqual(result["production_writes"], 0)
        self.assertTrue(result["stable_release_hold"])

    def test_child_must_be_privilege_dropped_to_original_operator(self):
        with patch.object(g2.os, "geteuid", return_value=0), \
             patch.dict(g2.os.environ,
                        {"SUDO_UID": "1000", "SUDO_GID": "1000"}, clear=True):
            self.assertEqual(g2.drop_to_operator(), (1000, 1000))
        with patch.object(g2.os, "geteuid", return_value=0), \
             patch.dict(g2.os.environ,
                        {"SUDO_UID": "0", "SUDO_GID": "0"}, clear=True):
            with self.assertRaises(g2.Stop):
                g2.drop_to_operator()
        with patch.object(g2.os, "geteuid", return_value=1000):
            with self.assertRaises(g2.Stop):
                g2.drop_to_operator()

    def test_report_accepts_only_anonymous_counters_and_denies_release(self):
        report = {"audit": g2.RESULT,
                  "status": "SHADOW_SESSION_FINISHED_NOT_RELEASED",
                  **{k: 0 for k in g2.FIELDS},
                  "real_odin_gateway_client_qualified": False,
                  "shadow_canary_complete": False,
                  "filter_response_sent_to_odin": False,
                  "release_hold_active": True,
                  "private_url": "http://private.invalid/?token=NEVER_PRINT"}
        report["verified_legacy_show_count"] = 17
        report["verified_legacy_alias_groups"] = 1
        safe = g2.checked_report(json.dumps(report))
        self.assertNotIn("private_url", safe)
        self.assertNotIn("NEVER_PRINT", json.dumps(safe))
        self.assertTrue(safe["release_hold_active"])
        for key, value in [
            ("production_sqlite_writes", 1),
            ("forwarded_watch_state_writes", 1),
            ("filter_response_sent_to_odin", True),
            ("real_odin_gateway_client_qualified", True),
            ("release_hold_active", False),
            ("shadow_attempted", "credential"),
        ]:
            corrupt = dict(report)
            corrupt[key] = value
            with self.subTest(key=key), self.assertRaises(g2.Stop):
                g2.checked_report(json.dumps(corrupt))

    def test_never_accepts_raw_serialized_source_as_report(self):
        for text in [
            "not-json", "{}", "[]",
            json.dumps({"audit": g2.RESULT, "status": "UNAUTHORIZED"}),
        ]:
            with self.assertRaises((g2.Stop, AttributeError)):
                g2.checked_report(text)


if __name__ == "__main__":
    unittest.main()
