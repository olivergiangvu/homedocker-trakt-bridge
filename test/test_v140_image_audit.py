import importlib.util
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-unused-images.py"
spec = importlib.util.spec_from_file_location("audit_images_v140", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

class ImageAuditTests(unittest.TestCase):
    def test_preserves_bridge_rollback_even_when_no_container_uses_it(self):
        self.assertEqual(
            audit.classify(["ghcr.io/example/homedocker-trakt-bridge:v1.3.0"]),
            "trakt_bridge_rollback_preserve",
        )

    def test_unknown_untagged_image_never_assumed_safe(self):
        self.assertEqual(audit.classify([]), "untagged_unknown_provenance")

    def test_rc7_canary_image_requires_manual_review(self):
        self.assertEqual(
            audit.classify(["homedocker-nextup-gateway:canary-old"]),
            "rc7_image_manual_review",
        )

    def test_audit_is_read_only_and_no_secret_or_registry_output(self):
        i1 = "sha256:" + "a" * 64
        i2 = "sha256:" + "b" * 64
        responses = {
            ("image", "ls", "-a", "--no-trunc", "--format", "{{.ID}}"):
                i1 + "\n" + i2 + "\n",
            ("ps", "-aq"): "container-id",
            ("inspect", "--format", "{{.Image}}", "container-id"): i1,
            ("image", "inspect", "--format", "{{json .RepoTags}}|{{.Size}}", i1):
                '["ghcr.io/private-repo/internal-service:v9"]|10485760',
            ("image", "inspect", "--format", "{{json .RepoTags}}|{{.Size}}", i2):
                '["ghcr.io/private-repo/homedocker-trakt-bridge:v1.3.0"]|20971520',
        }
        with mock.patch.object(audit, "docker", side_effect=lambda *a: responses[a]) as cli:
            out = audit.image_inventory()
        self.assertEqual(out["attached_image_ids"], 1)
        self.assertEqual(out["unused_image_ids"], 1)
        self.assertEqual(out["unused_review_candidates"][0]["role"], "trakt_bridge_rollback_preserve")
        self.assertFalse(out["unused_review_candidates"][0]["safe_to_delete_without_review"])
        self.assertNotIn("private-repo", str(out))
        self.assertTrue(all(args.args[0] not in ("rm", "rmi", "prune") for args in cli.call_args_list))
if __name__ == "__main__":
    unittest.main()
