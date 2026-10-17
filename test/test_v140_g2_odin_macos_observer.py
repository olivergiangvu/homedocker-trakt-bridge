"""G2-1A: no-production-change Odin/macOS temporary nginx observer tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-v140-g2-odin-macos-observer.py"
spec = importlib.util.spec_from_file_location("g2_odin_observer", SCRIPT)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class OdinObserverTests(unittest.TestCase):
    def test_observe_mode_requires_explicit_operator_approval(self):
        p = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--mode", "observe"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 5)
        self.assertIn("EXPLICIT_TEMPORARY_OBSERVER_APPROVAL_REQUIRED", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_preflight_never_creates_nginx_listener_or_authentication(self):
        with patch.object(o, "g2_preflight", return_value={
            "status": "G2_ROUTE_BASELINE_READY_NO_APPLY"
        }) as g2, patch.object(o, "nginx_path", return_value="/usr/sbin/nginx"), \
             patch.object(o, "run_observer") as runner, \
             patch.object(sys, "argv", [str(SCRIPT), "--mode", "preflight"]):
            r = o.main()
        self.assertEqual(g2.call_count, 1)
        runner.assert_not_called()
        self.assertEqual(r["status"], "ODIN_OBSERVER_PREPARED_NOT_RUNNING")
        self.assertFalse(r["production_nginx_touched"])
        self.assertFalse(r["observer_running"])
        self.assertFalse(r["client_modified"])
        self.assertFalse(r["real_gateway_client_qualified"])

    def test_observer_explicitly_rejects_root_user(self):
        with patch.object(o.os, "geteuid", return_value=0):
            with self.assertRaises(o.Stop) as ctx:
                o.observe_unprivileged_precheck()
            self.assertEqual(str(ctx.exception),
                             "OBSERVER_MUST_RUN_AS_NORMAL_USER")

    def test_only_loopback_listener_and_aio_port_are_built_into_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = o.configuration(Path(tmp))
        self.assertIn("listen 127.0.0.1:17431;", c)
        self.assertIn("proxy_pass http://127.0.0.1:3240;", c)
        self.assertNotIn("0.0.0.0:17431", c)
        self.assertNotIn("listen 80;", c)
        self.assertNotIn("listen 443;", c)
        self.assertNotIn("docker", c)
        self.assertIn("worker_processes  1;", c)
        self.assertIn("proxy_buffering off;", c)

    def test_log_is_categorical_status_only_never_raw_sensitive_traffic(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = o.configuration(Path(tmp))
        self.assertIn("log_format odin_groups '$odin_category $status';", c)
        self.assertIn("access_log " + tmp + "/counts.log odin_groups;", c)
        for secret_var in ("$request_uri", "$args", "$remote_addr", "$http_authorization",
                           "$http_x_emby_token", "$http_cookie", "$user_agent",
                           "$request_body", "$http_referer"):
            self.assertNotIn(secret_var, c)
        self.assertIn("error_log /dev/null crit;", c)

    def test_write_suppression_while_allowing_one_login_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = o.configuration(Path(tmp))
        self.assertIn('default 0;', c)
        self.assertIn('"~^(GET|HEAD|OPTIONS):" 1;', c)
        self.assertIn('"~^POST:/jellyfin/Users/AuthenticateByName$" 1;', c)
        self.assertIn('if ($odin_allowed = 0) { return 405; }', c)
        self.assertNotIn("POST:/jellyfin/Sessions/Playing", c)
        self.assertNotIn("POST:/jellyfin/Users/Items", c)
        # AIO watch history writes never go through temporary observer.

    def test_counter_report_is_anonymous_and_does_not_claim_gateway(self):
        r = o.counter_report([
            "nextup 200", "nextup 200", "resume 200",
            "userdata 200", "auth 200", "other 405", "other 503"])
        self.assertEqual(r["status"], "ODIN_OBSERVER_FINISHED_NO_GATEWAY_INTERCEPT")
        self.assertEqual(r["observed_requests"], 7)
        self.assertEqual(r["observed_nextup"], 2)
        self.assertEqual(r["observed_resume"], 1)
        self.assertEqual(r["observed_authentication"], 1)
        self.assertEqual(r["observed_other"], 2)
        self.assertEqual(r["response_classes"]["4xx"], 1)
        self.assertEqual(r["response_classes"]["5xx"], 1)
        self.assertTrue(r["client_nextup_path_observed"])
        self.assertEqual(r["gateway_forwarded_requests"], 0)
        self.assertFalse(r["real_gateway_client_qualified"])
        serialized = json.dumps(r)
        for token in ("tt44051354", "authorization", "secret_username",
                      "movie_title", "/jellyfin/Shows/NextUp"):
            self.assertNotIn(token, serialized)

    def test_unknown_raw_log_line_fails_closed_and_cannot_show_request(self):
        with self.assertRaises(o.Stop):
            o.counter_report(["GET /jellyfin/Shows/NextUp?token=SECRET 200"])
        with self.assertRaises(o.Stop):
            o.counter_report(["auth 200", "nextup invalidstatus"])


if __name__ == "__main__":
    unittest.main()
