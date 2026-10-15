import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "cleanup-rc7-canaries.py"
spec = importlib.util.spec_from_file_location("rc7_cleanup", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestExactCanaryCleanup(unittest.TestCase):
    @staticmethod
    def fixture(image=mod.CANARIES[0][1], port=mod.CANARIES[0][2]):
        return {
            "Name": "/" + mod.CANARIES[0][0],
            "Id": "container-id",
            "Image": "sha256:correct",
            "Config": {"Image": image},
            "HostConfig": {
                "PortBindings": {"7101/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": port}
                ]},
                "NetworkMode": "media_core_net",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "RestartPolicy": {"Name": "no"}
            },
            "Mounts": [{
                "Destination": "/run/identity-proof.json",
                "RW": False,
            }],
            "State": {"Running": True}
        }

    def test_explicit_four_only_and_latest_excluded_default(self):
        self.assertEqual(len(mod.CANARIES), 4)
        self.assertEqual([i[2] for i in mod.CANARIES], [
            "17131", "17132", "17133", "17134"
        ])
        self.assertEqual([i[0] for i in mod.CANARIES[:3]], [
            "homedocker-nextup-canary",
            "homedocker-nextup-auth-canary",
            "homedocker-nextup-c4fix-canary",
        ])
        self.assertNotIn("trakt-bridge", [i[0] for i in mod.CANARIES])
        self.assertNotIn("aiostreams", [i[0] for i in mod.CANARIES])

    def test_correct_isolated_container_passes(self):
        self.assertEqual(mod.validate(
            self.fixture(), mod.CANARIES[0][1], "17131"
        ), "running")

    def test_refuses_drift_or_reused_name(self):
        for change in [
            lambda x: x["HostConfig"]["PortBindings"]["7101/tcp"][0].update(
                {"HostIp": "0.0.0.0"}
            ),
            lambda x: x["Config"].update({"Image": "unknown:latest"}),
            lambda x: x["HostConfig"].update({"NetworkMode": "host"}),
            lambda x: x["HostConfig"].update({"ReadonlyRootfs": False}),
            lambda x: x["HostConfig"].update({"Privileged": True}),
            lambda x: x["HostConfig"]["RestartPolicy"].update(
                {"Name": "always"}
            ),
            lambda x: x["Mounts"][0].update({"RW": True}),
        ]:
            with self.subTest(change=change):
                candidate = self.fixture()
                change(candidate)
                with self.assertRaisesRegex(RuntimeError, "CANARY_PREFLIGHT_FAILED"):
                    mod.validate(candidate, mod.CANARIES[0][1], "17131")

    def test_absent_is_safe(self):
        self.assertEqual(mod.validate(None, mod.CANARIES[0][1], "17131"), "absent")

    def test_production_baseline_requires_both_running(self):
        valid = {
            "trakt-bridge": {"Id": "a", "Image": "sha256:a", "State": {"Running": True}},
            "aiostreams": {"Id": "b", "Image": "sha256:b", "State": {"Running": True}},
        }
        with mock.patch.object(mod, "inspect", side_effect=lambda x: valid.get(x)):
            self.assertEqual(mod.production_state(), {
                "trakt-bridge": ("a", "sha256:a", True),
                "aiostreams": ("b", "sha256:b", True),
            })
        valid["aiostreams"]["State"]["Running"] = False
        with mock.patch.object(mod, "inspect", side_effect=lambda x: valid.get(x)):
            with self.assertRaisesRegex(RuntimeError, "PRODUCTION_NOT_RUNNING"):
                mod.production_state()

    def test_inventory_never_mutates(self):
        with mock.patch.object(mod, "inspect", return_value=None), \
             mock.patch.object(mod, "image_info", return_value=None):
            result = mod.inventory(False)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(row["status"] == "absent" for row in result))


if __name__ == "__main__":
    unittest.main()
