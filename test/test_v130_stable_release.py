import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_root_only_compose_checker_accepts_interpolated_image_safely(self):
        source = (ROOT / "scripts/deploy-v1.3.0-homedocker.py").read_text()
        self.assertIn('run("sudo", "-v")', source)
        self.assertIn('cmd = ["sudo", "-n", "docker", "compose"', source)
        self.assertIn("ROOT_ONLY_COMPOSE_ENV=VALIDATED_WITHOUT_SECRET_OUTPUT", source)
        code = source.split('    check = r"""', 1)[1].split('"""', 1)[0]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envfile = root / ".env"
            compose = root / "compose.yml"
            envfile.write_text("TRAKT_BRIDGE_IMAGE=secret-digest\nTRAKT_CLIENT_SECRET=DO_NOT_PRINT\n")
            envfile.chmod(0o600)
            compose.write_text("services:\n  trakt-bridge:\n    image: ${TRAKT_BRIDGE_IMAGE:-fallback}\n")
            p = subprocess.run(
                [sys.executable, "-c", code, str(envfile), str(compose)],
                capture_output=True, text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("ROOT_ONLY_COMPOSE_ENV=", p.stdout)
            self.assertNotIn("DO_NOT_PRINT", p.stdout)
            compose.write_text("services:\n  trakt-bridge:\n    image: fixed:latest\n")
            p = subprocess.run(
                [sys.executable, "-c", code, str(envfile), str(compose)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(p.returncode, 0)
            compose.write_text("image: ${TRAKT_BRIDGE_IMAGE}\n")
            envfile.write_text("TRAKT_BRIDGE_IMAGE=one\nTRAKT_BRIDGE_IMAGE=two\n")
            p = subprocess.run(
                [sys.executable, "-c", code, str(envfile), str(compose)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(p.returncode, 0)

    def test_rc7_mitigation_is_required_before_and_after_stable(self):
        from unittest.mock import patch
        source = (ROOT / "scripts/deploy-v1.3.0-homedocker.py").read_text()
        self.assertEqual(source.count("    rc7_identity_guard()"), 2)
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "monitor-identity-drift.py"
            m.write_text("placeholder")
            good = {
                "mode": "SQLITE_READ_ONLY", "attention": 0, "database_writes": 0,
                "pairs": [{
                    "alias_imdb": "tt44094505",
                    "canonical_imdb": "tt44051354",
                    "status": "MITIGATED_BY_DROP",
                    "alias_drop_effective": True, "canonical_resumes": 1
                }]
            }
            with patch.object(deploy.Path, "home", return_value=Path(td)), \
                 patch.object(deploy, "run", return_value=json.dumps(good)):
                deploy.rc7_identity_guard()
            bad = json.loads(json.dumps(good))
            bad["pairs"][0]["alias_drop_effective"] = False
            with patch.object(deploy.Path, "home", return_value=Path(td)), \
                 patch.object(deploy, "run", return_value=json.dumps(bad)):
                with self.assertRaises(deploy.Stop):
                    deploy.rc7_identity_guard()

    def test_release_notes_does_not_falsely_claim_live_stable_deployed(self):
        text = (ROOT / "docs/releases/v1.3.0.md").read_text()
        self.assertIn("do not", text.lower())
        self.assertIn("Actual HomeDocker RC6 production evidence", text)


if __name__ == "__main__":
    unittest.main()
