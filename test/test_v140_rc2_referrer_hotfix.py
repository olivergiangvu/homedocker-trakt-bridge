"""Offline only: setup Referrer-Policy hotfix keeps the attested rollback path."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy-v140-rc2-referrer-hotfix-homedocker.py"
spec = importlib.util.spec_from_file_location("referrer_hotfix_test", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestReferrerHotfix(unittest.TestCase):
    def test_exact_existing_image_attested_and_rollback_unchanged(self):
        self.assertEqual(
            module.ops.OLD_TAG,
            "homedocker-trakt-bridge:v1.4.0-rc.2-ci-d278050b2baf")
        source = SCRIPT.read_text()
        self.assertIn("ops.inspect(args.run_id, args.expected_head)", source)
        self.assertIn("ops.apply(*state)", source)
        self.assertIn("PUBLIC_NGINX_HEADER_NOT_YET_QUALIFIED", source)
        self.assertNotIn("docker compose down", source)
        self.assertNotIn("docker system prune", source)
        self.assertNotIn("nginx -s reload", source)

    def test_preflight_does_not_mutate(self):
        with patch("sys.argv", ["test", "--run-id", "39227222222",
                                "--expected-head", "a"*40]), \
             patch.object(module.ops, "inspect", return_value=(1,2,3,4,5,6)), \
             patch.object(module.ops, "apply") as apply, \
             patch("builtins.print"):
            module.main()
        apply.assert_not_called()

    def test_apply_requires_confirmation(self):
        with patch("sys.argv", ["test", "--run-id", "39227222222",
                                "--expected-head", "a"*40, "--apply"]):
            with self.assertRaisesRegex(module.ops.Stop,
                                        "EXPLICIT_REFERRER_HOTFIX_APPROVAL_REQUIRED"):
                module.main()

    def test_secret_free_read_only_policy_probes(self):
        with patch.object(module, "original_probe", return_value="SAFE") as old_probe, \
             patch.object(module.ops.old, "run",
                          return_value="AUTHENTICATED_SETUP_REFERRER_POLICY=OK") as run:
            result = module.probe(expect_fallback=False)
            self.assertIn("SAFE", result)
            self.assertIn("REFERRER_POLICY=OK", result)
            old_probe.assert_called_once_with(expect_fallback=True)
            js = run.call_args.args[-1]
            self.assertIn("'no-referrer'", js)
            self.assertIn("encodeURIComponent(config.adminKey)", js)
            self.assertIn("SETUP_KEY_NOT_EMITTED=TRUE", js)
            self.assertNotIn("state=on", js)
            self.assertNotIn("state=off", js)
        with patch.object(module, "original_probe", return_value="SAFE"), \
             patch.object(module.ops.old, "run", return_value="OK") as run:
            module.probe(expect_fallback=True)
            self.assertIn("'strict-origin'", run.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
