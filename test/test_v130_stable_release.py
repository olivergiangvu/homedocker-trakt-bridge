import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def import_script(name, stem):
    spec = importlib.util.spec_from_file_location(stem, ROOT / "scripts" / name)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


publish = import_script("publish-v1.3.0-tag.py", "publish_v13")
deploy = import_script("deploy-v1.3.0-homedocker.py", "deploy_v13")


class StablePromotionRegressionTests(unittest.TestCase):
    def test_runtime_metadata_and_source_version_match(self):
        pkg = json.loads((ROOT / "package.json").read_text())
        src = (ROOT / "src/config.mjs").read_text()
        self.assertEqual(pkg["version"], "1.3.0")
        self.assertIn("export const APP_VERSION = '1.3.0';", src)

    def test_release_notes_document_nonwriting_guards(self):
        s = (ROOT / "docs/releases/v1.3.0.md").read_text()
        for word in ("AIO_RECONCILER_MODE=compare",
                     "CANONICAL_ON_DEMAND_VERIFY=false",
                     "Issue #70", "latest",
                     "schema", "rollback", "24h"):
            self.assertIn(word, s)

    def test_tag_needs_exact_main_sha_and_completed_push_ci(self):
        sha = "a" * 40
        ci = [{
            "head_sha": sha, "event": "push",
            "status": "completed", "conclusion": "success",
        }]
        pkg = {"version": "1.3.0"}
        config = "export const APP_VERSION = '1.3.0';"
        publish.check(sha, sha, ci, pkg, config)
        with self.assertRaisesRegex(RuntimeError, "main changed"):
            publish.check("b" * 40, sha, ci, pkg, config)
        with self.assertRaisesRegex(RuntimeError, "main-push CI"):
            publish.check(sha, sha, [{**ci[0], "conclusion": "failure"}], pkg, config)
        with self.assertRaisesRegex(RuntimeError, "package.json"):
            publish.check(sha, sha, ci, {"version": "1.3.0-rc.6"}, config)

    def test_deployment_requires_exact_safe_trakt_compare_flags(self):
        good = {"Config": {"Env": [
            "PULL_IDENTITY_MODE=trakt", "AIO_RECONCILER_MODE=compare",
            "CANONICAL_ON_DEMAND_VERIFY=false"
        ]}}
        deploy.check_flags(good)
        for wrong in ("AIO_RECONCILER_MODE=off",
                      "PULL_IDENTITY_MODE=aiostreams",
                      "CANONICAL_ON_DEMAND_VERIFY=true"):
            changed = json.loads(json.dumps(good))
            key = wrong.split("=")[0]
            changed["Config"]["Env"] = [
                x if not x.startswith(key + "=") else wrong
                for x in changed["Config"]["Env"]
            ]
            with self.assertRaises(deploy.Stop):
                deploy.check_flags(changed)

    def test_stable_deploy_does_not_touch_aio_or_run_prune(self):
        src = (ROOT / "scripts/deploy-v1.3.0-homedocker.py").read_text()
        self.assertIn('"--no-deps"', src)
        self.assertIn('"--pull", "never"', src)
        self.assertIn("old.backup(new)", src)
        self.assertIn('check_ready("1.3.0-rc.6")', src)
        self.assertIn('check_ready("1.3.0")', src)
        self.assertNotIn("docker system prune", src)
        self.assertNotIn("docker volume prune", src)
        self.assertNotIn("container rm", src)

    def test_release_notes_does_not_falsely_claim_live_stable_deployed(self):
        text = (ROOT / "docs/releases/v1.3.0.md").read_text()
        self.assertIn("do not", text.lower())
        self.assertIn("Actual HomeDocker RC6 production evidence", text)


if __name__ == "__main__":
    unittest.main()
