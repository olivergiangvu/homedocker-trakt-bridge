import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "backup-v140-rc6-rollback.py"
spec = importlib.util.spec_from_file_location("v140_rc6_recovery", SOURCE)
r22 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r22)

class R22RecoveryTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        config = root / "compose"
        config.mkdir()
        env = config / ".env"
        env.write_text("BRIDGE_SECRET_KEY=THIS_IS_PRIVATE_DO_NOT_LOG\n")
        compose = config / "compose.yml"
        compose.write_text("services:\n  trakt-bridge:\n    image: example\n")
        volume = root / "volume"
        volume.mkdir()
        db = volume / "bridge.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA user_version=1")
        con.execute("CREATE TABLE profiles(id TEXT PRIMARY KEY, access_token_enc TEXT)")
        con.execute("CREATE TABLE media_cache(cache_key TEXT PRIMARY KEY, payload_json TEXT)")
        con.execute("INSERT INTO profiles VALUES ('a', 'CIPHERTEXT_PRIVATE')")
        con.execute("INSERT INTO media_cache VALUES ('identity-alias:v1:abc', '{}')")
        con.commit()
        con.close()
        bridge = {
            "Name": "/trakt-bridge",
            "State": {"Running": True},
            "Config": {
                "Image": r22.RC6_REF,
                "Labels": {
                    "com.docker.compose.project": "homedocker-trakt-bridge",
                    "com.docker.compose.project.working_dir": str(config),
                    "com.docker.compose.project.config_files": str(compose),
                    "com.docker.compose.service": "trakt-bridge",
                },
            },
            "Image": "sha256:" + "a" * 64,
            "Mounts": [{"Destination": "/app/data", "Type": "volume",
                        "Source": str(volume)}],
        }
        aio = {"Name": "/aiostreams", "State": {"Running": True},
               "Config": {"Image": "ghcr.io/viren070/aiostreams:latest"}}
        legacy = {"Name": "/homedocker-resume-contract-canary",
                  "State": {"Running": True}, "Config": {}}
        return {
            "containers": {
                "trakt-bridge": bridge,
                "aiostreams": aio,
                "homedocker-resume-contract-canary": legacy
            },
            "config": config,
            "compose": compose,
            "env": env,
            "db": db,
        }

    def test_dry_run_collect_only_not_snapshot(self):
        with TemporaryDirectory() as td:
            fx = self.fixture(td)
            data = {k:p.read_bytes() for k,p in
                    (("db",fx["db"]),("env",fx["env"]))}
            with mock.patch.object(r22, "inspect", side_effect=fx["containers"].get):
                result = r22.collect()
            self.assertEqual(len(result["compose"]), 1)
            self.assertEqual(result["schema"], 1)
            self.assertEqual(fx["db"].read_bytes(), data["db"])
            self.assertEqual(fx["env"].read_bytes(), data["env"])

    def test_snapshot_and_restore_with_permission_and_source_integrity(self):
        with TemporaryDirectory() as td:
            fx = self.fixture(td)
            original_db = fx["db"].read_bytes()
            original_env = fx["env"].read_bytes()
            source = {
                "db": fx["db"], "compose": [fx["compose"]],
                "env": fx["env"], "image_ref": r22.RC6_REF,
                "image_id": "sha256:" + "a" * 64,
                "compose_project": "homedocker-trakt-bridge",
                "working_dir": str(fx["config"]), "schema": 1,
            }
            base = Path(td) / "r22"
            with mock.patch.object(r22.os, "geteuid", return_value=0):
                summary = r22.snapshot(source, root=base,
                                       stamp="20261015T130000Z")
            self.assertEqual(summary["status"],
                             "PRIVATE_SNAPSHOT_AND_SQLITE_RESTORE_REHEARSAL_PASS")
            self.assertFalse(summary["external_backup_or_full_restore_verified"])
            self.assertEqual(summary["production_db_writes"], 0)
            self.assertEqual(fx["db"].read_bytes(), original_db)
            self.assertEqual(fx["env"].read_bytes(), original_env)
            output = json.dumps(summary)
            for secret in ("THIS_IS_PRIVATE", "CIPHERTEXT_PRIVATE", str(base)):
                self.assertNotIn(secret, output)
            folder = base / "20261015T130000Z"
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600
                                for p in folder.iterdir()))
            self.assertFalse((folder / ".restore-rehearsal.db").exists())
            snap = sqlite3.connect(folder / "bridge.db")
            self.assertEqual(snap.execute(
                "SELECT COUNT(*) FROM profiles").fetchone()[0], 1)
            snap.close()
            with mock.patch.object(r22.os, "geteuid", return_value=0):
                with self.assertRaises(FileExistsError):
                    r22.snapshot(source, root=base,
                                 stamp="20261015T130000Z")

    def test_symlinked_db_or_env_is_rejected(self):
        with TemporaryDirectory() as td:
            fx = self.fixture(td)
            alias = fx["config"] / "key.env"
            alias.symlink_to(fx["env"])
            with self.assertRaisesRegex(r22.Stop, "symlinked"):
                r22.regular(alias)
            fx["db"].rename(fx["db"].with_suffix(".original"))
            fx["db"].symlink_to(fx["db"].with_suffix(".original"))
            with mock.patch.object(r22, "inspect", side_effect=fx["containers"].get):
                with self.assertRaisesRegex(r22.Stop, "symlinked"):
                    r22.collect()

    def test_non_root_and_existing_target_never_overwritten(self):
        with TemporaryDirectory() as td:
            with mock.patch.object(r22.os, "geteuid", return_value=1000):
                with self.assertRaisesRegex(r22.Stop, "requires sudo"):
                    r22.snapshot({}, root=Path(td)/"new")

    def test_source_compose_outside_project_rejected(self):
        with TemporaryDirectory() as td:
            fx = self.fixture(td)
            outsider = Path(td) / "evil.yml"
            outsider.write_text("secret")
            fx["containers"]["trakt-bridge"]["Config"]["Labels"][
                "com.docker.compose.project.config_files"] = str(outsider)
            with mock.patch.object(r22, "inspect", side_effect=fx["containers"].get):
                with self.assertRaisesRegex(r22.Stop, "outside project"):
                    r22.collect()

if __name__ == "__main__":
    unittest.main()
