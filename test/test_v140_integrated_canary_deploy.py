import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-v140-integrated-canary.py"
spec = importlib.util.spec_from_file_location("v140_integrated_r23a", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class R23IntegratedCanary(unittest.TestCase):
    def test_tag_requires_verified_full_sha(self):
        self.assertEqual(mod.image_tag("a"*40),
                         "homedocker-trakt-bridge:v140-r23a-" + "a"*12)
        for bad in ("", "abc", "Z"*40, "a"*39):
            with self.assertRaises(mod.Stop):
                mod.image_tag(bad)

    def test_config_contains_fake_secrets_and_no_real_aio_identity(self):
        values = mod.docker_env()
        self.assertEqual(values["JELLYFIN_GATEWAY_ENABLED"], "true")
        self.assertEqual(values["JELLYFIN_GATEWAY_LEGACY_IMPORT"], "off")
        self.assertEqual(values["AIO_RECONCILER_MODE"], "off")
        self.assertEqual(values["AIO_HISTORY_ECHO_GUARD"], "false")
        self.assertEqual(values["JELLYFIN_GATEWAY_RESUME_PASSTHROUGH"], "false")
        self.assertEqual(values["JELLYFIN_GATEWAY_UPSTREAM_URL"],
                         "http://127.0.0.1:9999/jellyfin")
        self.assertNotIn("TRAKT_ACCESS_TOKEN", values)
        self.assertEqual(values["JELLYFIN_GATEWAY_PROFILE_ID"], "c"*24)
        self.assertNotEqual(mod.docker_env()["BRIDGE_SECRET_KEY"],
                            values["BRIDGE_SECRET_KEY"])
        self.assertNotEqual(mod.docker_env()["TRAKT_CLIENT_SECRET"],
                            values["TRAKT_CLIENT_SECRET"])

    def test_backup_r22_private_manifest_only_no_data_leak(self):
        with TemporaryDirectory() as temp:
            base = Path(temp) / "r22"
            base.mkdir(mode=0o700)
            folder = base / "20261015T123200Z"
            folder.mkdir(mode=0o700)
            for name, contents in (
                ("manifest.json", json.dumps({
                    "image_ref":mod.RC6,
                    "restore_rehearsal":"isolated_sqlite_backup_and_integrity_pass",
                    "sqlite_snapshot":{"profile_rows":1}
                })),
                ("bridge.db", "SENSITIVE_CIPHERTEXT"),
                ("compose.env.rc6", "TRAKT_SECRET=SENSITIVE"),
            ):
                f = folder / name
                f.write_text(contents)
                f.chmod(0o600)
            with mock.patch.object(mod, "R22_ROOT", base):
                self.assertEqual(mod.check_r22(), 1)
            self.assertTrue((folder / "bridge.db").exists())

    def test_failed_absent_backup_is_blocked(self):
        with TemporaryDirectory() as temp:
            with mock.patch.object(mod, "R22_ROOT", Path(temp) / "nonexistent"):
                with self.assertRaisesRegex(mod.Stop, "not found"):
                    mod.check_r22()

    def test_apply_uses_single_internal_network_and_no_prod_bind_mount(self):
        captured = []
        def run(*argv, **kwargs):
            captured.append(argv)
            return "ok"
        mock_meta = {
            "State": {"Running": True},
            "HostConfig": {"ReadonlyRootfs": True},
            "NetworkSettings":{"Networks":{mod.NETWORK:{}}},
            "Mounts":[{"Type":"tmpfs", "Destination":"/app/data"}],
        }
        state = {"tag":mod.image_tag("a"*40)}
        with mock.patch.object(mod, "execute", side_effect=run), \
             mock.patch.object(mod, "safe_exec") as exec_mock, \
             mock.patch.object(mod, "container_meta", return_value=mock_meta), \
             mock.patch.object(mod.time, "sleep"):
            exec_mock.return_value.returncode = 0
            result = mod.launch(Path("/tmp/fake-checkout"), state)
        self.assertEqual(result["new_containers_running"], 1)
        self.assertFalse(result["identity_normalization_qualified"])
        self.assertFalse(result["single_client_routing_enabled"])
        self.assertEqual(len(captured), 3)
        build, network, runtime = captured
        self.assertEqual(build[0:2], ("docker", "build"))
        self.assertEqual(network[:3], ("docker", "network", "create"))
        self.assertIn("--internal", network)
        self.assertEqual(runtime[:3], ("docker", "run", "-d"))
        self.assertIn("--read-only", runtime)
        self.assertIn("--tmpfs", runtime)
        self.assertIn("--cap-drop", runtime)
        self.assertIn("--network", runtime)
        self.assertEqual(runtime[runtime.index("--network")+1], mod.NETWORK)
        self.assertNotIn("--mount", runtime)
        self.assertNotIn("--privileged", runtime)
        self.assertNotIn("/var/lib/docker/volumes", str(runtime))
        self.assertIn("127.0.0.1:17041:7000", runtime)
        self.assertIn("127.0.0.1:17141:7101", runtime)
        self.assertIn("TRAKT_CLIENT_ID=r23a-pilot-invalid-client", runtime)
        self.assertEqual(result["production_containers_restarted"], 0)

    def test_existing_resources_stop_without_replacing(self):
        with mock.patch.object(mod.os, "geteuid", return_value=0), \
             mock.patch.object(mod, "source_sha", return_value="a"*40), \
             mock.patch.object(mod, "container_meta",
                               return_value={"Name":"/"+mod.NAME}):
            with self.assertRaisesRegex(mod.Stop, "already exists"):
                mod.preflight(Path(__file__).resolve().parents[1])

if __name__ == "__main__":
    unittest.main()
