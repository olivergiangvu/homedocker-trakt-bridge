"""Synthetic-only regression: G3 one-writer preflight may not mutate host."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
script = ROOT / "scripts/audit-v140-rc1-one-writer.py"
spec = importlib.util.spec_from_file_location("v140_rc1_one_writer", script)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

RC6 = audit.RC6


def fixture():
    ident = "sha256:" + "a" * 64
    mount = {"Destination": "/app/data", "Type": "volume",
             "Source": "/fake/nonproduction/bridge"}
    bridge = {
        "Id": "the_existing_bridge", "Image": ident,
        "State": {"Running": True},
        "Config": {
            "Image": RC6,
            "Labels": {
                "com.docker.compose.project": "fixture",
                "com.docker.compose.service": "trakt-bridge",
                "com.docker.compose.project.working_dir": "/fixture",
                "com.docker.compose.project.config_files": "/fixture/compose.yml",
            },
            "Env": ["PULL_IDENTITY_MODE=trakt",
                    "AIO_RECONCILER_MODE=compare",
                    "CANONICAL_ON_DEMAND_VERIFY=false",
                    "TRAKT_CLIENT_SECRET=never_print_this"],
        },
        "Mounts": [mount],
    }
    image = {"Id": ident, "RepoDigests": [RC6]}
    return bridge, image, [bridge]


class OneWriterAuditTests(unittest.TestCase):
    def test_exact_rc6_and_no_second_writer(self):
        bridge, image, running = fixture()
        result = audit.assess(bridge, image, running)
        self.assertEqual(result["one_writer_preflight"], "PASS")
        self.assertFalse(result["authorizes_cutover"])
        self.assertEqual(result["production_writes"], 0)
        self.assertEqual(result["other_running_containers_sharing_bridge_data"], 0)
        self.assertNotIn("never_print_this", str(result))
        self.assertNotIn("/fake/nonproduction", str(result))
        self.assertNotIn(RC6, str(result))

    def test_name_agnostic_second_writer_sharing_data_stops(self):
        bridge, image, running = fixture()
        # Any other active container on this host reusing the same volume
        # is enough to STOP; container name is not a reliable ownership key.
        running.append({
            "Id": "other_service_not_named_trakt",
            "Mounts": [{"Source": bridge["Mounts"][0]["Source"],
                        "Destination": "/var/alternate", "Type": "volume"}],
        })
        out = audit.assess(bridge, image, running)
        self.assertEqual(out["one_writer_preflight"], "STOP_REVIEW")
        self.assertEqual(out["other_running_containers_sharing_bridge_data"], 1)

    def test_fails_closed_for_missing_digest_config_flags_and_mount(self):
        for change in ("tag", "local", "digest", "flags", "mount", "stop",
                       "compose"):
            with self.subTest(change=change):
                bridge, image, running = fixture()
                if change == "tag":
                    bridge["Config"]["Image"] = "ghcr.io/example:latest"
                elif change == "local":
                    image["Id"] = "sha256:" + "b" * 64
                elif change == "digest":
                    image["RepoDigests"] = []
                elif change == "flags":
                    bridge["Config"]["Env"][0] = "PULL_IDENTITY_MODE=aiostreams"
                elif change == "mount":
                    bridge["Mounts"] = []
                elif change == "stop":
                    bridge["State"]["Running"] = False
                elif change == "compose":
                    bridge["Config"]["Labels"] = {}
                out = audit.assess(bridge, image, running)
                self.assertEqual(out["one_writer_preflight"], "STOP_REVIEW")

    def test_main_docker_error_does_not_echo_secrets_or_run_writes(self):
        with patch.object(audit, "docker_json",
                          side_effect=RuntimeError("never_print_this")):
            with patch("builtins.print") as printed:
                self.assertEqual(audit.main(), 1)
            reported = printed.call_args.args[0]
            self.assertIn("STOP_REVIEW", reported)
            self.assertNotIn("never_print_this", reported)

    def test_script_never_contains_mutating_docker_operations(self):
        src = script.read_text()
        for forbidden in ("docker compose up", "docker load", "docker stop",
                          "docker rm", "docker run", "docker tag",
                          "subprocess.Popen", "restic backup", "sqlite3.connect"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
