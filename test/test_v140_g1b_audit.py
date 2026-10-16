"""G1B: preflight and offline orchestration security checks (no Docker/network)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "audit-v140-g1b-isolated-replay.py"
spec = importlib.util.spec_from_file_location("g1b_host", TARGET)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class G1BSafety(unittest.TestCase):
    def test_confirm_gate_prevents_any_network_or_docker(self):
        for mode in ("login", "existing-token"):
            p = subprocess.run(
                [sys.executable, "-B", str(TARGET), "--mode", mode],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(p.returncode, 5)
            self.assertIn("EXPLICIT_", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_preflight_rejects_confirmation(self):
        p = subprocess.run(
            [sys.executable, "-B", str(TARGET), "--mode",
             "preflight", "--confirm", "A_STALE_CONFIRMATION"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 5)
        self.assertIn("PREFLIGHT_CANNOT_ENABLE_AUTH", p.stderr)

    def test_pinned_image_not_mutable_latest(self):
        self.assertIn("@sha256:", mod.IMAGE_PIN)
        self.assertNotIn(":latest", mod.IMAGE_PIN)

    def test_live_mode_unavailable_without_tty(self):
        g1a = type("A", (), {"TOKEN": __import__('re').compile(r".{16,1024}\Z")})
        with patch.object(mod.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(mod.Stop):
                mod.replay_snapshot(g1a, "existing-token")

    def test_runner_no_existing_temp_container_accepted(self):
        with patch.object(mod.subprocess, "run", return_value=type(
            "Done", (), {"returncode": 0})()) as run:
            with self.assertRaises(mod.Stop) as ctx:
                mod.run_offline("sha256:" + "a"*64, {
                    "profile": "a"*24, "legacy": {}, "user": "fakeuser123",
                    "nextup": {"Items": []}, "details": {},
                })
            self.assertEqual(str(ctx.exception),
                             "G1B_CONTAINER_NAME_ALREADY_OCCUPIED")
            self.assertEqual(run.call_count, 1)

    def test_offline_command_is_ephemeral_no_network_no_hostport(self):
        captured = []
        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""
        with patch.object(mod.subprocess, "run") as run:
            def execute(args, **kwargs):
                captured.append(args)
                if args[:3] == ["docker", "container", "inspect"]:
                    result = Completed()
                    result.returncode = 1
                    return result
                if args[:2] == ["docker", "run"]:
                    # Force a controlled fail after capturing the exact argv.
                    result = Completed()
                    result.returncode = 5
                    return result
                raise AssertionError("unexpected command")
            run.side_effect = execute
            with self.assertRaises(mod.Stop):
                mod.run_offline("sha256:" + "a"*64, {
                    "profile": "a"*24, "legacy": {}, "user": "fakeuser123",
                    "nextup": {"Items": []}, "details": {},
                })
        argv = captured[1]
        self.assertIn("--rm", argv)
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network")+1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user")+1], "1000:1000")
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("--network=host", argv)
        self.assertNotIn("-p", argv)
        self.assertNotIn("--env-file", argv)
        self.assertNotIn("-e", argv)
        self.assertNotIn("/var/run/docker.sock", " ".join(argv))
        self.assertNotIn("fakeuser123", " ".join(argv))

if __name__ == "__main__":
    unittest.main()
