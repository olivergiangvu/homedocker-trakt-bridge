import importlib.util
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from io import StringIO

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "audit-dangling-image-ownership.py"
spec = importlib.util.spec_from_file_location("dangling_docker_audit", SOURCE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def img(ident, layers=("base", "common"), *, title=None):
    labels = {"org.opencontainers.image.title": title} if title else None
    return {
        "Id": "sha256:" + ident * 64,
        "Size": 126 * 1048576,
        "Created": "2026-10-13T01:00:00Z",
        "RootFS": {"Layers": list(layers)},
        "Config": {"Labels": labels},
    }


class DanglingImageOwnershipTests(unittest.TestCase):
    def test_dangling_but_active_image_must_never_delete(self):
        bridge = img("a")
        containers = [{"Name": "/trakt-bridge", "Image": bridge["Id"]}]
        state, attached, hint = audit.classify(bridge, containers, [bridge])
        self.assertEqual(state, "IN_USE_NEVER_DELETE")
        self.assertEqual(attached, "trakt-bridge")
        self.assertEqual(hint, "")

    def test_unreferenced_is_review_not_safe_deletion(self):
        old = img("b", ("base", "common", "app-old"))
        current = img("c", ("base", "common", "app-new"))
        containers = [{"Name": "/xtream-vod-admin", "Image": current["Id"]}]
        state, refs, hint = audit.classify(old, containers, [current])
        self.assertEqual(state, "UNREFERENCED_REVIEW_ROLLBACK")
        self.assertEqual(refs, "-")
        self.assertIn("none", hint)

    def test_report_tolerates_images_without_oci_labels(self):
        out = StringIO()
        with redirect_stdout(out):
            audit.report([img("a")], [], [])
        self.assertIn("OCI_TITLE=unknown", out.getvalue())
        self.assertIn("NO_REMOVAL_OR_PULL=true", out.getvalue())
        self.assertIn("NOT an approval to delete", out.getvalue())

    def test_same_base_layers_are_only_a_weak_hint(self):
        a = img("a", ("a", "b", "c", "custom-old"))
        b = img("b", ("a", "b", "c", "custom-new"))
        containers = [{"Name": "/live-service", "Image": b["Id"]}]
        status, _, hint = audit.classify(a, containers, [b])
        self.assertEqual(status, "UNREFERENCED_REVIEW_ROLLBACK")
        self.assertIn("shared_base_layers=3/4", hint)


if __name__ == "__main__":
    unittest.main()
