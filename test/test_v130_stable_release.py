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
        # Published v1.3.0 remains fixed in the stable deploy/publish scripts.
        # Development source may identify itself as an untagged RC.1 candidate.
        # Keep the list explicit: no implicit authorization for future tags.
        self.assertIn(pkg["version"], ("1.3.0", "1.4.0-rc.1", "1.4.0-rc.2"))
        self.assertIn(
            "export const APP_VERSION = '" + pkg["version"] + "';", src
        )

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

    def test_exact_release_manifest_digest_must_match(self):
        exact = deploy.PUBLISHED_STABLE_REF
        self.assertEqual(deploy.attested_release_digest([exact]), exact)
        self.assertEqual(
            deploy.attested_release_digest([
                deploy.IMAGE + "@sha256:" + "a"*64, exact
            ]), exact)
        for bad in (
            [], None, "not-a-list", [deploy.IMAGE+"@sha256:"+"b"*64],
            [exact, None], [12345],
        ):
            with self.subTest(bad=bad), self.assertRaises(deploy.Stop):
                deploy.attested_release_digest(bad)

    def test_published_stable_tag_drift_stops_before_backup_or_compose_write(self):
        # A mutable tag moved to a new GHCR digest must NEVER be promoted.
        from unittest.mock import patch
        changed=deploy.IMAGE+"@sha256:"+"c"*64
        expected_inspect=json.dumps([{"RepoDigests":[changed]}])
        with patch.object(deploy.shutil,"which",return_value="/usr/bin/gh"), \
             patch.object(deploy,"run",return_value="{}") as command, \
             patch.object(deploy,"docker",return_value=expected_inspect) as inspect:
            with self.assertRaisesRegex(deploy.Stop,"reviewed published digest"):
                deploy.stable_image()
        self.assertTrue(command.called)
        inspect.assert_called_once_with("image","inspect",deploy.TAG)

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

    def test_root_owned_docker_volume_uses_readonly_privileged_stat(self):
        source=(ROOT / "scripts/deploy-v1.3.0-homedocker.py").read_text()
        self.assertIn('run("sudo", "-n", "python3", "-B", "-c", check_mount, src)',source)
        self.assertIn("ROOT_ONLY_BRIDGE_VOLUME_DB=VERIFIED_READONLY",source)
        self.assertNotIn('Path(mounts[0]["Source"]).is_dir()',source)
        self.assertNotIn("chmod -R",source)
        self.assertNotIn("chown -R",source)
        code=source.split('    check_mount = r"""',1)[1].split('"""',1)[0]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"protected-docker-data"
            root.mkdir(mode=0o700)
            db=root/"bridge.db"
            db.write_bytes(b"fixture only; no DB mutations required")
            p=subprocess.run([sys.executable,"-B","-c",code,str(root)],
                             capture_output=True,text=True,timeout=3)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.strip(),"ROOT_ONLY_BRIDGE_VOLUME_DB=VERIFIED_READONLY")
            self.assertEqual(db.read_bytes(),b"fixture only; no DB mutations required")
            db.unlink()
            p=subprocess.run([sys.executable,"-B","-c",code,str(root)],
                             capture_output=True,text=True,timeout=3)
            self.assertNotEqual(p.returncode,0)

    def test_volume_root_symlink_or_db_symlink_rejected(self):
        source=(ROOT / "scripts/deploy-v1.3.0-homedocker.py").read_text()
        code=source.split('    check_mount = r"""',1)[1].split('"""',1)[0]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"volume-data"
            root.mkdir()
            target=Path(td)/"db-target"
            target.write_text("do-not-follow")
            (root/"bridge.db").symlink_to(target)
            for path in (root,Path(td)/"link"):
                if path!=root: path.symlink_to(root, target_is_directory=True)
                p=subprocess.run([sys.executable,"-B","-c",code,str(path)],
                                 capture_output=True,text=True,timeout=3)
                self.assertNotEqual(p.returncode,0)
            self.assertEqual(target.read_text(),"do-not-follow")

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
                    "alias_drop_effective": True, "canonical_resumes": 1,
                    "canonical_episode_rows": 10, "alias_episode_rows": 8
                }]
            }
            with patch.object(deploy.Path, "home", return_value=Path(td)), \
                 patch.object(deploy, "run", return_value=json.dumps(good)):
                deploy.rc7_identity_guard()
            completed = json.loads(json.dumps(good))
            completed["pairs"][0]["canonical_resumes"] = 0
            completed["pairs"][0]["canonical_watched"] = 10
            with patch.object(deploy.Path, "home", return_value=Path(td)), \
                 patch.object(deploy, "run", return_value=json.dumps(completed)):
                deploy.rc7_identity_guard()
            alias_gone = json.loads(json.dumps(good))
            alias_gone["pairs"][0]["status"] = "SINGLE_TIMELINE"
            alias_gone["pairs"][0]["alias_episode_rows"] = 0
            alias_gone["pairs"][0]["alias_drop_effective"] = False
            with patch.object(deploy.Path, "home", return_value=Path(td)), \
                 patch.object(deploy, "run", return_value=json.dumps(alias_gone)):
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
