"""Offline-only safeguards for the RC2 Safari Origin hotfix runbook."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy-v140-rc2-origin-hotfix-homedocker.py"
spec = importlib.util.spec_from_file_location("origin_hotfix", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class TestOriginHotfix(unittest.TestCase):
    def test_missing_confirmation_fails_before_any_host_activity(self):
        with patch("sys.argv", ["script", "--run-id", "39219468879",
                                "--expected-head", "a" * 40, "--apply"]):
            with self.assertRaisesRegex(ops.Stop, "EXPLICIT_HOTFIX_APPROVAL_REQUIRED"):
                ops.main()

    def test_dry_run_cannot_mutate(self):
        mock_state = ("artifact", "prior", "compose", "env", "source", "aio")
        with patch("sys.argv", ["script", "--run-id", "39219468879",
                                "--expected-head", "a" * 40]), \
             patch.object(ops, "inspect", return_value=mock_state), \
             patch.object(ops, "apply") as apply, \
             patch("builtins.print"):
            ops.main()
        apply.assert_not_called()

    def test_gateway_read_only_probe_must_never_toggle_live_state(self):
        with patch.object(ops.old, "run", return_value="SIMULATED") as run:
            ops.js_probe(expect_fallback=True)
            code = run.call_args.args[-1]
        self.assertIn("body:'state=invalid'", code)
        self.assertIn("'sec-fetch-site':'same-origin'", code)
        self.assertIn("deriveProfileKey", code)
        self.assertIn("filteringEnabled!==false", code)
        self.assertIn("CROSS_SITE_POST=REJECTED", code)
        self.assertNotIn("body:'state=on'", code)
        self.assertNotIn("body:'state=off'", code)

    def test_image_pinning_same_writer_and_rollback_invariants(self):
        code = SCRIPT.read_text()
        self.assertIn("v1.4.0-rc.2-ci-fa64a91ad827", code)
        # The imported rc2.artifact() verifies exact PR head, SHA256 and
        # source metadata; this wrapper must actually invoke it.
        self.assertIn("rc2.artifact(run_id, expected_head)", code)
        self.assertIn("SECOND_WRITER_MOUNT_DETECTED", code)
        self.assertIn('"--force-recreate", "trakt-bridge"', code)
        self.assertIn("RC2_ORIGIN_FIX_ROLLBACK=HEALTHY", code)
        self.assertIn("AIO_CLIENT_ROUTING_CHANGED=NO", code)
        for prohibited in ("docker compose down", "docker system prune",
                           "docker volume prune", "nginx -s reload"):
            self.assertNotIn(prohibited, code)


if __name__ == "__main__":
    unittest.main()
