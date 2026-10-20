"""Offline mock regressions for v1.4.0 HomeDocker two-Compose stable deployment.

Does not call real Docker, GHCR, AIO, Trakt or production SQLite.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy-v1.4.0-homedocker.py"
spec = importlib.util.spec_from_file_location("stable_v140", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class StableCutoverSafetyTests(unittest.TestCase):
    def test_exact_stable_and_rc2_digest_and_compose(self):
        self.assertTrue(ops.STABLE_REF.endswith(
            "sha256:61c28bd22155950cacfd7ac47e5d5259a74e55c5b99c3dc4b63d95be524f2c77"))
        self.assertTrue(ops.PREVIOUS_REF.endswith("v1.4.0-rc.2-ci-a9defdd6c899"))
        self.assertEqual(ops.PREVIOUS_ID,
            "sha256:91ac17af1edc15297d45caad466b76d07f2edb4091314e68260694b6b87c417c")
        self.assertEqual([p.name for p in ops.FILES], ["compose.yml", "compose.gateway.yml"])
        self.assertEqual(ops.compose_command()[-4:],
                         ["-f", str(ops.FILES[0]), "-f", str(ops.FILES[1])])
        self.assertNotIn("docker system prune", SCRIPT.read_text())
        self.assertNotIn("docker volume prune", SCRIPT.read_text())
        self.assertNotIn("rm -f", SCRIPT.read_text())

    def test_atomic_image_change_keeps_other_env_keys_and_mode(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            before = (f"TRAKT_BRIDGE_IMAGE={ops.PREVIOUS_REF}\n"
                      "TRAKT_CLIENT_SECRET=SYNTHETIC_TEST_SECRET\n"
                      "JELLYFIN_GATEWAY_ENABLED=true\n")
            env.write_text(before)
            env.chmod(0o600)
            with patch.object(ops, "ENV", env), patch.object(ops, "DIR", Path(td)):
                ops.set_image(ops.STABLE_REF)
                after = env.read_text()
                self.assertIn("TRAKT_CLIENT_SECRET=SYNTHETIC_TEST_SECRET", after)
                self.assertIn("JELLYFIN_GATEWAY_ENABLED=true", after)
                self.assertIn("TRAKT_BRIDGE_IMAGE=" + ops.STABLE_REF, after)
                self.assertEqual(env.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(ops.Stop, "IMAGE_UPDATE_WOULD_BE_NOOP"):
                    ops.set_image(ops.STABLE_REF)
            self.assertNotIn(ops.PREVIOUS_REF, after)

    def test_composed_diff_rejects_gateway_network_change(self):
        source = {"services": {"trakt-bridge": {
            "image": ops.PREVIOUS_REF, "networks": ["default", "media_core"],
            "environment": {"JELLYFIN_GATEWAY_ENABLED": "true"}}},
                  "networks": {"default": {}, "media_core": {"external": True}}}
        import json
        expected = json.loads(json.dumps(source))
        expected["services"]["trakt-bridge"]["image"] = ops.STABLE_REF
        with patch.object(ops, "rendered", return_value=expected):
            ops.verify_composed_diff(source)
        changed = json.loads(json.dumps(expected))
        changed["services"]["trakt-bridge"]["networks"] = ["default"]
        with patch.object(ops, "rendered", return_value=changed):
            with self.assertRaisesRegex(ops.Stop, "COMPOSE_DIFF"):
                ops.verify_composed_diff(source)

    def test_compose_env_file_image_key_follows_pinned_image_only(self):
        """An actual .env edit changes both service.image and env_file image key."""
        import copy
        old = {
            "services": {"trakt-bridge": {
                "image": ops.PREVIOUS_REF,
                "environment": {
                    "TRAKT_BRIDGE_IMAGE": ops.PREVIOUS_REF,
                    "JELLYFIN_GATEWAY_ENABLED": "true",
                    "TRAKT_CLIENT_SECRET": "synthetic-secret-never-log",
                },
                "networks": ["default", "media_core"],
            }},
            "networks": {"default": {}, "media_core": {"external": True}},
        }
        expected = copy.deepcopy(old)
        expected["services"]["trakt-bridge"]["image"] = ops.STABLE_REF
        expected["services"]["trakt-bridge"]["environment"]["TRAKT_BRIDGE_IMAGE"] = ops.STABLE_REF
        self.assertEqual(ops.expected_composed_image_change(old), expected)
        self.assertEqual(old["services"]["trakt-bridge"]["environment"]["TRAKT_BRIDGE_IMAGE"],
                         ops.PREVIOUS_REF)
        with patch.object(ops, "rendered", return_value=expected):
            self.assertEqual(ops.verify_composed_diff(old), expected)
        changed = copy.deepcopy(expected)
        changed["services"]["trakt-bridge"]["environment"]["JELLYFIN_GATEWAY_ENABLED"] = "false"
        with patch.object(ops, "rendered", return_value=changed):
            with self.assertRaisesRegex(ops.Stop, "COMPOSE_DIFF"):
                ops.verify_composed_diff(old)
        tampered_baseline = copy.deepcopy(old)
        tampered_baseline["services"]["trakt-bridge"]["environment"]["TRAKT_BRIDGE_IMAGE"] = "evil"
        with self.assertRaisesRegex(ops.Stop, "COMPOSE_ENV_IMAGE_BASELINE_DRIFT"):
            ops.expected_composed_image_change(tampered_baseline)

    def test_runtime_own_image_env_can_change_but_other_bridge_env_cannot(self):
        stable_id = "sha256:" + "e" * 64
        baseline = {
            "source": "/synthetic/bridge-data",
            "networks": {"homedocker-trakt-bridge_default", "media_core_net"},
            "settings": {
                "TRAKT_BRIDGE_IMAGE": ops.PREVIOUS_REF,
                "TRAKT_CLIENT_ID": "synthetic-profile",
                "JELLYFIN_GATEWAY_ENABLED": "true",
                "JELLYFIN_GATEWAY_FIRST_PAGE_MODE": "short_first_page_only",
            },
            "aio": {"Id": "synthetic-aio-id", "Image": "synthetic-aio-image"}
        }
        new = {
            "Config": {"Image": ops.STABLE_REF,
                       "Env": [
                           "TRAKT_BRIDGE_IMAGE=" + ops.STABLE_REF,
                           "TRAKT_CLIENT_ID=synthetic-profile",
                           "JELLYFIN_GATEWAY_ENABLED=true",
                           "JELLYFIN_GATEWAY_FIRST_PAGE_MODE=short_first_page_only",
                       ]},
            "Image": stable_id,
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "NetworkSettings": {"Networks": {
                "homedocker-trakt-bridge_default": {},
                "media_core_net": {},
            }, "Ports": {"7000/tcp": [], "7101/tcp": None}},
            "Mounts": [{"Destination": "/app/data", "Source": "/synthetic/bridge-data"}],
        }
        with patch.object(ops, "assert_http") as http, \
             patch.object(ops, "inspect", side_effect=lambda name:
                          new if name == ops.APP else baseline["aio"]), \
             patch.object(ops, "no_other_writer") as single:
            self.assertIs(ops.verify_new(baseline, stable_id, version="1.4.0",
                                         image_ref=ops.STABLE_REF, retries=1), new)
            http.assert_called_once()
            single.assert_called_once()
        new["Config"]["Env"][0] = "TRAKT_BRIDGE_IMAGE=unrelated-image"
        with patch.object(ops, "assert_http"), \
             patch.object(ops, "inspect", return_value=new):
            with self.assertRaisesRegex(ops.Stop, "IMAGE_ENV_NOT_EXPECTED_REF"):
                ops.verify_new(baseline, stable_id, version="1.4.0",
                               image_ref=ops.STABLE_REF, retries=1)

    def test_apply_auto_rolls_back_old_env_after_compose_failure(self):
        import json
        image_id = "sha256:" + "a" * 64
        state = {"config": {"services": {"trakt-bridge": {"image": ops.PREVIOUS_REF}}},
                 "bridge": {}, "aio": {}, "source": "/synthetic", "networks": set(),
                 "settings": {}}
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "compose.env.before").write_bytes(b"synthetic old env")
            calls = []
            def mocked_docker(*args, **kwargs):
                if args[0] == "pull":
                    return ""
                if args[:2] == ("image", "inspect"):
                    return json.dumps([{"Id": image_id, "RepoDigests": [ops.STABLE_REF]}])
                raise AssertionError("unexpected Docker call")
            def compose_run(*args, **kwargs):
                calls.append(args)
                if len([a for a in calls if "up" in a]) == 1:
                    raise ops.Stop("SYNTHETIC_COMPOSE_FAILURE")
                return ""
            with patch.object(ops, "docker", side_effect=mocked_docker), \
                 patch.object(ops, "preflight", return_value=state), \
                 patch.object(ops, "protected_backup", return_value=folder), \
                 patch.object(ops, "set_image", return_value=None), \
                 patch.object(ops, "verify_composed_diff", return_value=None), \
                 patch.object(ops, "rendered", return_value=state["config"]), \
                 patch.object(ops, "run", side_effect=compose_run), \
                 patch.object(ops, "replace_env_bytes") as restore, \
                 patch.object(ops, "verify_new", return_value={}) as verify:
                with self.assertRaisesRegex(ops.Stop, "CUTOVER_FAILED_RC2_RESTORED"):
                    ops.apply(state)
                restore.assert_called_once_with(b"synthetic old env")
                verify.assert_called_once()
                self.assertEqual(verify.call_args.kwargs["image_ref"], ops.PREVIOUS_REF)
                self.assertEqual(sum(1 for call in calls if "up" in call), 2)

    def test_missing_expected_keys_fail_closed(self):
        config = {
            "JELLYFIN_GATEWAY_ENABLED": "true",
            "JELLYFIN_GATEWAY_LEGACY_IMPORT": "off",
            "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH": "false",
            "JELLYFIN_GATEWAY_FIRST_PAGE_MODE": "short_first_page_only"
        }
        self.assertEqual(config, ops.EXPECTED_GATEWAY)
        self.assertTrue(all(isinstance(k, str) for k in config))


if __name__ == "__main__":
    unittest.main()
