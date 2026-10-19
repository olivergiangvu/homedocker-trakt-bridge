"""RC.2 upgrade mock tests: no real Docker, sqlite, Trakt, AIO or nginx."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy-v140-rc2-homedocker.py"
spec = importlib.util.spec_from_file_location("rc2_cutover", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

HEAD = "1" * 40


class RC2CutoverTests(unittest.TestCase):
    def staged(self, root, corrupt=False, release_hold="ACTIVE"):
        folder = root / ".local/share/homedocker/rc2-staging" / "12345678"
        folder.mkdir(parents=True, mode=0o700)
        content = b"fixture not a real Docker tar"
        (folder/"bridge-rc2.docker.tar").write_bytes(
            b"wrong" if corrupt else content)
        (folder/"SHA256SUMS").write_text(
            hashlib.sha256(content).hexdigest()+"  bridge-rc2.docker.tar\n")
        checkout = "2" * 40
        meta = {
            "source_pr_head_sha": HEAD,
            "source_checkout_sha": checkout,
            "image_tag": "homedocker-trakt-bridge:v1.4.0-rc.2-ci-"+checkout[:12],
            "local_image_id": "sha256:"+"a"*64,
            "publication": "NONE",
            "stable_release_hold": release_hold,
            "registry_manifest_digest": "NOT_AVAILABLE_UNPUBLISHED",
        }
        (folder/"IMAGE-METADATA.txt").write_text("".join(
            k+"="+v+"\n" for k,v in meta.items()))
        return folder, meta

    def test_archive_provenance_and_hash_pinned(self):
        with tempfile.TemporaryDirectory() as t:
            folder, meta = self.staged(Path(t))
            with patch.object(ops.Path, "home", return_value=Path(t)):
                result = ops.artifact("12345678", HEAD)
            self.assertEqual(result["tag"], meta["image_tag"])
            self.assertEqual(result["image_id"], meta["local_image_id"])
            self.assertEqual(result["archive"], folder/"bridge-rc2.docker.tar")

    def test_corruption_wrong_head_and_removed_hold_are_stops(self):
        for mode in ("corrupt", "head", "hold"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                self.staged(Path(td), corrupt=(mode=="corrupt"),
                            release_hold="REMOVED" if mode=="hold" else "ACTIVE")
                with patch.object(ops.Path, "home", return_value=Path(td)):
                    with self.assertRaises(ops.Stop):
                        ops.artifact("12345678", "3"*40 if mode=="head" else HEAD)

    def test_read_only_default_cannot_apply_or_load(self):
        data = {"archive": Path("/fixture"), "tag": "x", "image_id": "y"}
        previous = ({"Id":"bridge"}, ["compose"], Path("/fixture/.env"), "aio")
        with patch.object(ops, "artifact", return_value=data), \
             patch.object(ops, "exact_rc1_one_writer", return_value=previous), \
             patch.object(ops, "apply") as apply, \
             patch("builtins.print"), \
             patch("sys.argv", [
                 "rc2", "--run-id", "12345678", "--expected-head", HEAD]):
            ops.main()
        apply.assert_not_called()

    def test_apply_requires_exact_phrase(self):
        with patch("sys.argv", [
            "rc2", "--run-id", "12345678", "--expected-head", HEAD,
            "--apply", "--confirm", "unapproved"]):
            with self.assertRaisesRegex(ops.Stop, "APPROVAL_REQUIRED"):
                ops.main()

    def test_failed_rc2_upgrade_uses_rc1_env_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            data = {"archive": Path(td)/"image.tar", "tag": "rc2:image",
                    "image_id": "sha256:"+"b"*64}
            prior = {"Config":{"Image":"homedocker-trakt-bridge:v1.4.0-rc.1-ci-"+"a"*12},
                     "Image":"sha256:"+"c"*64}
            def execute(*args):
                if args[-3:] == ("config", "--format", "json"):
                    return json.dumps({"services":{
                        "trakt-bridge":{"image":"not-rc2"}}})
                return ""
            with patch.object(ops.Path, "home", return_value=Path(td)), \
                 patch.object(ops.old, "run", side_effect=execute), \
                 patch.object(ops.old, "docker", return_value=json.dumps(
                     [{"Id":data["image_id"]}])), \
                 patch.object(ops.old, "set_image"), \
                 patch.object(ops, "rollback") as rollback, \
                 patch("builtins.print"):
                with self.assertRaisesRegex(ops.Stop,
                                            "RC2_CUTOVER_FAILED_RC1_RESTORED"):
                    ops.apply(data, prior, ["compose"], Path(td)/".env", "aio")
                rollback.assert_called_once()
                self.assertEqual(rollback.call_args.args[3], prior["Config"]["Image"])

    def test_script_never_publishes_or_touches_aio_and_stable(self):
        content = SCRIPT.read_text()
        for banned in ("docker system prune", "docker volume prune",
                       "docker compose down", "gh release create",
                       "docker push", "sqlite3.connect",
                       "restic backup", "docker run -d"):
            self.assertNotIn(banned, content)
        self.assertIn("--no-deps", content)
        self.assertIn("--pull", content)
        self.assertIn("AUTOMATIC_RC1_ROLLBACK_FAILED", content)


if __name__ == "__main__":
    unittest.main()
