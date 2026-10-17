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
        self.assertIn("log_format odin_groups '$odin_category $status $odin_family $odin_items_subroute $odin_upstream_headers';", c)
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
            "nextup 200 shows not_items received", "nextup 200 shows not_items received", "resume 200 users not_items received",
            "userdata 200 users not_items received", "auth 200 users not_items received", "other 405 images not_items missing", "other 503 media not_items received"])
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

    def test_nextup_route_specific_http_200_and_3xx_4xx_are_aggregated(self):
        result = o.counter_report([
            "nextup 302 shows not_items received", "nextup 200 shows not_items received", "resume 200 users not_items received", "auth 401 users not_items received",
            "other 302 images not_items received", "other 404 media not_items received", "other 405 media not_items received", "other 200 items items_collection received",
        ])
        self.assertTrue(result["client_nextup_path_observed"])
        self.assertTrue(result["nextup_http_200_observed"])
        self.assertEqual(result["nextup_http_200_count"], 1)
        self.assertEqual(result["response_class_by_route"]["nextup"]["3xx"], 1)
        self.assertEqual(result["response_class_by_route"]["nextup"]["2xx"], 1)
        self.assertEqual(result["response_class_by_route"]["auth"]["4xx"], 1)
        self.assertEqual(result["response_class_by_route"]["resume"]["2xx"], 1)
        self.assertEqual(result["response_class_by_route"]["other"]["4xx"], 2)
        for category, amount in [
            ("nextup", result["observed_nextup"]),
            ("resume", result["observed_resume"]),
            ("userdata", result["observed_userdata"]),
            ("auth", result["observed_authentication"]),
            ("other", result["observed_other"]),
        ]:
            self.assertEqual(sum(result["response_class_by_route"][category].values()), amount)
        summary = json.dumps(result)
        for private in ("Authorization", "/jellyfin/", "Imdb",
                        "UserId", "tt44051354", "secret"):
            self.assertNotIn(private, summary)

    def test_nextup_get_observed_but_redirect_does_not_qualify_200(self):
        result = o.counter_report([
            "nextup 302 shows not_items received", "nextup 404 shows not_items received", "resume 200 users not_items received", "other 200 items items_collection received"])
        self.assertEqual(result["observed_nextup"], 2)
        self.assertTrue(result["client_nextup_path_observed"])
        self.assertEqual(result["nextup_http_200_count"], 0)
        self.assertFalse(result["nextup_http_200_observed"])
        self.assertEqual(result["gateway_forwarded_requests"], 0)
        self.assertFalse(result["real_gateway_client_qualified"])

    def test_bounded_other_5xx_families_no_uri_or_user_data(self):
        result = o.counter_report([
            "other 502 media not_items missing", "other 503 items item_detail received", "other 504 media not_items missing",
            "other 404 users not_items received", "other 304 images not_items received", "nextup 200 shows not_items received",
            "resume 200 users not_items received",
        ])
        self.assertEqual(result["observed_requests"], 7)
        self.assertEqual(result["nextup_http_200_count"], 1)
        self.assertEqual(result["response_class_by_route"]["other"]["5xx"], 3)
        families = result["other_errors_by_endpoint_family"]
        self.assertEqual(families["media"], {"4xx": 0, "5xx": 2})
        self.assertEqual(families["items"], {"4xx": 0, "5xx": 1})
        self.assertEqual(families["users"], {"4xx": 1, "5xx": 0})
        self.assertEqual(families["images"], {"4xx": 0, "5xx": 0})
        self.assertEqual(sum(x["5xx"] for x in families.values()), 3)
        output = json.dumps(result)
        for hidden in ("/jellyfin/", "Authorization", "user123", "tt44051354"):
            self.assertNotIn(hidden, output)
        self.assertFalse(result["real_gateway_client_qualified"])

    def test_nginx_error_family_mapping_is_strictly_static(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = o.configuration(Path(tmp))
        self.assertIn("map $uri $odin_family {", config)
        self.assertIn("default aio_other;", config)
        self.assertIn("jellyfin_other;", config)
        self.assertIn("$odin_category $status $odin_family", config)
        for forbidden in ("$request_uri", "$args", "$remote_addr",
                          "$http_x_emby_token", "$http_cookie", "$request_body"):
            self.assertNotIn(forbidden, config)
        with self.assertRaises(o.Stop):
            o.counter_report(["other 502 unauthorised_dynamic_family not_items received"])

    def test_items_5xx_origins_and_subroutes_are_only_constant_counters(self):
        report = o.counter_report([
            "nextup 200 shows not_items received",
            "resume 200 users not_items received",
            "other 500 items item_detail received",
            "other 502 items item_subresource missing",
            "other 503 items libraries received",
            "other 304 images not_items received",
        ])
        self.assertEqual(report["observed_requests"], 6)
        self.assertEqual(report["observed_nextup"], 1)
        self.assertEqual(report["nextup_http_200_count"], 1)
        self.assertEqual(report["items_5xx_by_subroute"]["item_detail"], 1)
        self.assertEqual(report["items_5xx_by_subroute"]["item_subresource"], 1)
        self.assertEqual(report["items_5xx_by_subroute"]["libraries"], 1)
        self.assertEqual(report["items_5xx_upstream_response_headers"], {
            "received": 2, "missing": 1,
        })
        self.assertEqual(report["other_errors_by_endpoint_family"]["items"]["5xx"], 3)
        self.assertFalse(report["real_gateway_client_qualified"])
        sanitized = json.dumps(report)
        for banned in ("jellyfin/", "tt12345678", "UserId",
                       "Authorization", "item_unique_id_abc", "http://"):
            self.assertNotIn(banned, sanitized)

    def test_rejects_family_or_subroute_mismatches_and_private_log_lines(self):
        for line in (
            "other 500 items not_items received",
            "other 502 images item_detail received",
            "other 500 items item_detail invalid",
            "other 500 items item_detail received path/query",
            "other 500 items item_detail",
        ):
            with self.subTest(line=line), self.assertRaises(o.Stop):
                o.counter_report([line])

    def test_nginx_origin_mapping_is_static_and_no_raw_upstream_timing_logged(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = o.configuration(Path(temporary))
        self.assertIn("map $uri $odin_items_subroute {", config)
        self.assertIn("map $upstream_header_time $odin_upstream_headers {", config)
        self.assertIn("item_detail;", config)
        self.assertIn("item_subresource;", config)
        self.assertIn("libraries;", config)
        self.assertIn("default missing;", config)
        self.assertIn("$odin_items_subroute $odin_upstream_headers", config)
        self.assertNotIn("$upstream_header_time'", config)
        for forbidden in ("$request_uri", "$args", "$upstream_addr",
                          "$remote_addr", "$http_cookie",
                          "$http_x_emby_token", "$http_authorization"):
            self.assertNotIn(forbidden, config)

    def test_unknown_raw_log_line_fails_closed_and_cannot_show_request(self):
        with self.assertRaises(o.Stop):
            o.counter_report(["other 500 /jellyfin/Shows/NextUp?token=SECRET not_items received"])
        with self.assertRaises(o.Stop):
            o.counter_report(["auth 200 users not_items received", "nextup invalidstatus shows"])


if __name__ == "__main__":
    unittest.main()
