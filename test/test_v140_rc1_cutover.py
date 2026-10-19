"""Mock-only RC.1 deployment test; NEVER loads images or changes HomeDocker."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
source = HERE / "scripts/deploy-v140-rc1-homedocker.py"
spec = importlib.util.spec_from_file_location("rc1_homedocker_cutover", source)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class ControlledCutoverTests(unittest.TestCase):
    def artifact(self, root, corrupt=False, unsafe=False):
        folder = root / "staged"
        folder.mkdir(mode=0o700)
        payload = b"synthetic-only-ci-image-not-a-real-docker-tar"
        (folder / "bridge-rc1.docker.tar").write_bytes(payload if not corrupt else b"changed")
        dig = hashlib.sha256(payload).hexdigest()
        (folder / "SHA256SUMS").write_text(dig + "  bridge-rc1.docker.tar\n")
        checkout = "9" * 40
        image_id = "sha256:" + "a" * 64
        metadata = {
            "source_pr_head_sha": ops.PR_HEAD,
            "source_checkout_sha": checkout,
            "image_tag": "homedocker-trakt-bridge:v1.4.0-rc.1-ci-" + checkout[:12],
            "local_image_id": image_id,
            "publication": "NONE",
            "stable_release_hold": "ACTIVE" if not unsafe else "REMOVED",
            "registry_manifest_digest": "NOT_AVAILABLE_UNPUBLISHED",
        }
        (folder / "IMAGE-METADATA.txt").write_text(
            "".join(k + "=" + v + "\n" for k, v in metadata.items()))
        return folder, metadata

    def test_exact_artifact_sha_and_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            folder, m = self.artifact(Path(td))
            out = ops.artifact_identity(folder)
            self.assertEqual(out["tag"], m["image_tag"])
            self.assertEqual(out["image_id"], m["local_image_id"])

    def test_bad_checksum_and_release_hold_refuse_before_apply(self):
        for variant in ("corrupt", "unsafe"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as td:
                    folder, m = self.artifact(
                        Path(td), corrupt=variant=="corrupt",
                        unsafe=variant=="unsafe")
                    with self.assertRaises(ops.Stop):
                        ops.artifact_identity(folder)

    def test_no_apply_by_default_and_explicit_authorization(self):
        info = {"tag": "fixture", "image_id": "sha256:" + "a" * 64}
        fixture = (info, {"Id": "bridge"}, ["fake", "compose"],
                   Path("/fixture/.env"), "aio_id")
        with patch.object(ops, "preflight", return_value=fixture), \
             patch.object(ops, "apply") as apply:
            with patch("sys.argv", ["deploy-v140-rc1-homedocker.py"]), \
                 patch("builtins.print"):
                ops.main()
            apply.assert_not_called()
            with patch("sys.argv", [
                "deploy-v140-rc1-homedocker.py", "--apply",
                "--confirm", "not_authorized"]):
                with self.assertRaises(ops.Stop):
                    ops.main()
            apply.assert_not_called()

    def test_real_apply_requires_exact_phrase(self):
        artifact = {"tag": "fixture", "image_id": "sha256:" + "a" * 64}
        fixture = (artifact, {"Id": "bridge"}, ["fake", "compose"],
                   Path("/fixture/.env"), "aio_id")
        with patch.object(ops, "preflight", return_value=fixture), \
             patch.object(ops, "runtime_ready", return_value=True), \
             patch.object(ops, "apply") as apply, \
             patch("builtins.print"):
            with patch("sys.argv", ["deploy-v140-rc1-homedocker.py",
                  "--apply", "--confirm", ops.APPROVAL]):
                ops.main()
            apply.assert_called_once()

    def test_gateway_config_must_be_disabled_even_in_compose(self):
        for payload, expected_ok in (
            ({"JELLYFIN_GATEWAY_ENABLED": "false"}, True),
            ({"JELLYFIN_GATEWAY_ENABLED": "true"}, False),
            ({"JELLYFIN_GATEWAY_LEGACY_IMPORT": "apply"}, False),
            ({"JELLYFIN_GATEWAY_RESUME_PASSTHROUGH": "yes"}, False),
        ):
            with self.subTest(payload=payload):
                result = json.dumps({
                    "services": {"trakt-bridge": {"environment": payload}}
                })
                with patch.object(ops.old, "run", return_value=result):
                    if expected_ok:
                        ops.assert_gateway_off(["fixture", "compose"])
                    else:
                        with self.assertRaises(ops.Stop):
                            ops.assert_gateway_off(["fixture", "compose"])

    def test_cutover_failure_restores_rc6_using_private_env(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = {"tag": "fixture:rc1",
                        "image_id": "sha256:" + "a" * 64,
                        "archive": Path(td) / "fake-image.tar"}
            original = {"Config": {"Image": ops.RC6}}
            command = ["sudo", "-n", "docker", "compose", "fixture"]
            envfile = Path(td) / ".env"

            def harmless_run(*args):
                if args[-3:] == ("config", "--format", "json"):
                    return json.dumps({"services": {
                        "trakt-bridge": {"image": "incorrect-image"}}})
                return ""

            with patch.object(ops.Path, "home", return_value=Path(td)), \
                 patch.object(ops.old, "run", side_effect=harmless_run), \
                 patch.object(ops, "verify_loaded_image"), \
                 patch.object(ops.old, "set_image"), \
                 patch.object(ops, "rollback") as rb, \
                 patch("builtins.print"):
                with self.assertRaisesRegex(
                        ops.Stop, "CUTOVER_FAILED_AND_RC6_RESTORED"):
                    ops.apply(artifact, original, command, envfile, "aio")
                rb.assert_called_once()
                self.assertEqual(rb.call_args.args[1], envfile)

    def test_rc6_rollback_uses_existing_compose_and_readiness(self):
        with patch.object(ops.old, "run", return_value="") as run, \
             patch.object(ops, "runtime_ready", return_value=True), \
             patch.object(ops.old, "container", return_value={
                 "Config": {"Image": ops.RC6}
             }), patch("builtins.print"):
            ops.rollback(Path("/fixture/previous.env"),
                         Path("/fixture/.env"), ["fixture", "compose"])
        argv = [call.args for call in run.call_args_list]
        self.assertTrue(any(args[:4] == (
            "sudo", "-n", "cp", "-p") for args in argv))
        self.assertTrue(any(
            "up" in args and "--no-deps" in args
            and "--pull" in args and "never" in args
            and "--force-recreate" in args and "trakt-bridge" in args
            for args in argv))

    def test_no_broad_deployment_or_history_mutation_in_script(self):
        text = source.read_text()
        for forbidden in (
            "docker system prune", "docker volume prune", "docker compose down",
            "delete from processed_events", "UPDATE watch_state",
            "TRAKT_CLIENT_SECRET=", "restic backup", "gh release create",
            "docker push", "docker pull", "sudo rm -rf",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("--no-deps", text)
        self.assertIn("--pull", text)
        self.assertIn("AUTHORIZE_V140_RC1_BRIDGE_ONLY_CUTOVER", text)
        self.assertIn("AUTOMATIC_ROLLBACK_FAILED", text)


if __name__ == "__main__":
    unittest.main()
