import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-v140-legacy-import-offline.py"
spec = importlib.util.spec_from_file_location("r20_memory_import", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PROFILE = "a" * 24

def sample_db(path, conflict=False):
    now = int(time.time())
    rows = {
        str(i + 1): {
            "traktImdb": "tt" + str(i + 10000000),
            "preferredMetaId": "tt" + str(i + 10000000),
            "updatedAt": now - 300,
        } for i in range(14)
    }
    rows["263102"] = {
        "traktImdb": "tt44051354",
        "preferredMetaId": "tt44094505",
        "updatedAt": now - 300,
    }
    if conflict:
        rows["14"]["preferredMetaId"] = "tt44094505"
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE profiles (id TEXT PRIMARY KEY, access_token_enc TEXT);
        CREATE TABLE media_cache (cache_key TEXT PRIMARY KEY,
            payload_json TEXT, expires_at INTEGER);
    """)
    db.execute("INSERT INTO profiles VALUES (?,?)", (PROFILE, "encrypted-secret"))
    db.execute("INSERT INTO media_cache VALUES (?,?,?)", (
        "identity-alias:v1:" + PROFILE,
        json.dumps({"shows": rows}), now + 3600
    ))
    db.commit()
    db.close()

@unittest.skipUnless(shutil.which("node"), "Node runtime required in GitHub CI")
class R20OfflineMemoryTests(unittest.TestCase):
    def test_real_legacy_source_imports_exact_count_only_to_memory(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "bridge.db"
            sample_db(source)
            before = source.read_bytes()
            result = mod.audit(str(source))
            after = source.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "OFFLINE_MEMORY_IMPORT_PASS")
        self.assertEqual(result["imported_show_mappings"], 15)
        self.assertEqual(result["alias_groups"], 1)
        self.assertEqual(result["verified_alias_count"], 1)
        self.assertTrue(result["repeat_import_idempotent"])
        self.assertEqual(result["sqlite_writes"], 0)
        self.assertFalse(result["migration_persisted_to_production"])
        printed = json.dumps(result)
        for private in (PROFILE, "tt44051354", "tt44094505",
                        "263102", "encrypted-secret"):
            self.assertNotIn(private, printed)

    def test_real_identity_collision_rejected_without_any_writes(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "bridge.db"
            sample_db(source, conflict=True)
            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "legacy_preflight_not_ready"):
                mod.audit(str(source))
            self.assertEqual(source.read_bytes(), before)

    def test_explicit_docker_runner_never_pulls_or_mounts_production(self):
        from unittest import mock
        import subprocess
        proof = subprocess.CompletedProcess(
            args=["docker", "inspect"], returncode=0,
            stdout="sha256:" + ("f" * 64) + "|true\\n", stderr=""
        )
        with mock.patch.object(mod.subprocess, "run", return_value=proof) as run:
            cmd = mod.docker_runner()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(cmd[0:4], ["docker", "run", "--rm", "-i"])
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")
        self.assertIn("--read-only", cmd)
        self.assertEqual(cmd[cmd.index("--cap-drop") + 1], "ALL")
        self.assertEqual(cmd[cmd.index("--user") + 1], "1000:1000")
        self.assertEqual(cmd[cmd.index("--entrypoint") + 1], "/usr/local/bin/node")
        self.assertEqual(cmd[-2], "sha256:" + ("f" * 64))
        self.assertFalse(any(s in cmd for s in ("--env", "-e", "-p", "--publish")))
        self.assertEqual(sum(x == "--mount" for x in cmd), 2)
        self.assertTrue(all("/var/lib/docker/volumes" not in x for x in cmd))

    def test_docker_runner_requires_running_unchanged_sha_image(self):
        from unittest import mock
        import subprocess
        stopped = subprocess.CompletedProcess(
            args=["docker", "inspect"], returncode=0,
            stdout="sha256:" + ("f" * 64) + "|false\\n", stderr=""
        )
        with mock.patch.object(mod.subprocess, "run", return_value=stopped):
            with self.assertRaisesRegex(
                RuntimeError, "production_runtime_not_qualified"
            ):
                mod.docker_runner()

    def test_existing_graph_rejected(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "bridge.db"
            sample_db(source)
            cx = sqlite3.connect(source)
            cx.execute("INSERT INTO media_cache VALUES (?,?,?)", (
                "identity-graph:v1:" + PROFILE, "{}", int(time.time()) + 1000))
            cx.commit()
            cx.close()
            with self.assertRaisesRegex(ValueError, "scope_or_graph_precondition_failed"):
                mod.audit(str(source))

if __name__ == "__main__":
    unittest.main()
