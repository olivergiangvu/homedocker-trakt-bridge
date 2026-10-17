"""G1C host preflight + Docker runtime safety tests, no Docker/network."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "audit-v140-g1c-direct-aio.py"
spec = importlib.util.spec_from_file_location("g1c_operator", PATH)
g1c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g1c)


class G1CBoundaries(unittest.TestCase):
    def test_auth_confirmation_required_before_any_inspection(self):
        for mode in ("login", "existing-token"):
            result = subprocess.run([sys.executable, "-B", str(PATH),
                                     "--mode", mode],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 5)
            self.assertIn("G1C_STOP_EXPLICIT_", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_preflight_rejects_auth_confirmation(self):
        result = subprocess.run(
            [sys.executable, "-B", str(PATH), "--mode", "preflight",
             "--confirm", g1c.CONFIRM_TOKEN],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 5)
        self.assertIn("PREFLIGHT_REJECTS_AUTH_CONFIRM", result.stderr)

    def test_aio_topology_only_accepts_loopback_and_separate_netns(self):
        aio = {"State": {"Running": True}, "Config": {"Image": g1c.AIO_REF},
               "HostConfig": {
                   "NetworkMode": "homedocker_media",
                   "PortBindings": {"3000/tcp": [
                       {"HostIp": "127.0.0.1", "HostPort": "3240"}]}}}
        with patch.object(g1c, "docker_inspect", return_value=aio):
            self.assertTrue(g1c.aio_topology())
            for mode in ("host", "none", "container:other"):
                aio["HostConfig"]["NetworkMode"] = mode
                with self.assertRaises(g1c.Stop):
                    g1c.aio_topology()
            aio["HostConfig"]["NetworkMode"] = "homedocker_media"
            aio["HostConfig"]["PortBindings"]["3000/tcp"][0]["HostIp"] = "0.0.0.0"
            with self.assertRaises(g1c.Stop):
                g1c.aio_topology()

    def test_preflight_reports_counts_and_no_credential_calls(self):
        class StubAIO:
            def aio_request(self, path):
                self.paths.append(path)
                return {}
            def __init__(self): self.paths = []
        class StubBridge:
            def inspect_production(self): return ("sha256:" + "a"*64, Path("/irrelevant"))
            def snapshot(self, db, other):
                return {"shows": 17, "groups": 1, "profile": "a"*24,
                        "legacy": {"shows": {}}}
        a, b = StubAIO(), StubBridge()
        def importer(name, path):
            return a if "g1-live" in path else b
        with patch.object(g1c, "aio_topology"), \
             patch.object(g1c, "include", side_effect=importer), \
             patch.object(g1c, "temp_absent"), \
             patch.object(sys, "argv", [str(PATH), "--mode", "preflight"]):
            status = g1c.main()
        self.assertEqual(status["status"], "PREFLIGHT_READY_NO_AUTH_NO_CONTAINER")
        self.assertEqual(status["verified_alias_groups"], 1)
        self.assertEqual(status["new_login_sessions"], 0)
        self.assertEqual(status["new_containers"], 0)
        self.assertEqual(a.paths, ["/System/Info/Public"])
        self.assertNotIn("a"*24, json.dumps(status))

    def test_g1c_docker_is_unprivileged_ephemeral_and_shares_only_aio_netns(self):
        user = "private_user_1234"
        token = "sensitive_aio_access_token_123456"
        record = {"profile": "a"*24, "legacy": {"shows": {}},
                  "user": user, "token": token}
        called = []
        class Completed:
            returncode = 1
            stdout = "{}"
        approved = {
            "status": "DIRECT_AIO_GATEWAY_GET_PASS",
            "actual_v140_gateway_authenticated_aio_get": True,
            "exact_configured_user_matched_users_me": True,
            "gateway_preserved_real_nextup": True,
            "source_stable_during_gateway_probe": True,
            "independent_user_to_profile_ownership_proven": False,
            "real_client_qualified": False,
            "production_sqlite_writes": 0,
            "watch_history_or_playback_writes_requested": 0,
            "trakt_http_calls": 0,
            "no_public_port_or_nginx_route": True,
            "user_id_profile_id_imdb_ids_or_token_printed": False,
            "g1_complete": False,
            "aio_http_methods": ["GET"],
            "verified_alias_groups_in_ram": 1,
        }
        def fake(args, **kw):
            called.append((args, kw))
            v = Completed()
            if args[:2] == ["docker", "run"]:
                v.returncode = 0
                v.stdout = json.dumps(approved)
            return v
        with patch.object(g1c.subprocess, "run", side_effect=fake):
            out = g1c.run_probe("sha256:" + "a"*64, record)
        self.assertEqual(out["status"], "DIRECT_AIO_GATEWAY_GET_PASS")
        self.assertEqual(len(called), 3)
        cmd, opts = called[1]
        self.assertEqual(cmd[:2], ["docker", "run"])
        for flag in ("--rm", "-i", "--read-only", "--cap-drop",
                     "--security-opt", "--user", "--pids-limit"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--network")+1], "container:aiostreams")
        self.assertNotIn("-p", cmd)
        self.assertNotIn("--privileged", cmd)
        self.assertNotIn("--env-file", cmd)
        self.assertNotIn("-e", cmd)
        self.assertNotIn(token, " ".join(cmd))
        self.assertNotIn(user, " ".join(cmd))
        self.assertIn(token, opts["input"])
        self.assertTrue(opts["capture_output"])
        self.assertTrue(opts["text"])

    def test_candidate_name_occupied_stops_without_docker_run(self):
        done = type("Result", (), {"returncode": 0})()
        with patch.object(g1c.subprocess, "run", return_value=done) as mock:
            with self.assertRaises(g1c.Stop) as e:
                g1c.temp_absent()
        self.assertEqual(str(e.exception), "G1C_TEMP_CONTAINER_NAME_IN_USE")
        self.assertEqual(mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
