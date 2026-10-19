"""Mock-only HomeDocker Gateway provisioning acceptance; no Docker writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/provision-v140-rc2-gateway-homedocker.py"
spec = importlib.util.spec_from_file_location("gateway_ops", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

PROFILE = "a" * 24
USER = "11111111abcdef00"


class GatewayProvisionTests(unittest.TestCase):
    def test_override_only_adds_two_networks_and_safe_env(self):
        yaml, env = ops.override_yaml(USER, PROFILE)
        self.assertIn("external: true", yaml)
        self.assertIn("name: media_core_net", yaml)
        self.assertIn("JELLYFIN_GATEWAY_ENABLED", yaml)
        self.assertIn('"false"', yaml)
        self.assertNotIn("ports:", yaml)
        self.assertEqual(env["JELLYFIN_GATEWAY_RESUME_PASSTHROUGH"], "false")
        self.assertEqual(env["JELLYFIN_GATEWAY_LEGACY_IMPORT"], "off")
        self.assertNotIn("ADMIN_KEY", yaml)
        base = {
            "services": {"trakt-bridge": {
                "image": "homedocker-trakt-bridge:v1.4.0-rc.2-ci-" + "1" * 12,
                "networks": {"default": None},
                "environment": {"PULL_IDENTITY_MODE": "trakt"},
                "ports": [{"target":7000,"published":"7000","host_ip":"127.0.0.1"}],
                "volumes": [{"type":"volume","source":"bridge-data","target":"/app/data"}],
            }},
            "networks": {"default": {"name":ops.BASE_NETWORK}},
        }
        candidate = json.loads(json.dumps(base))
        candidate["services"]["trakt-bridge"]["networks"]["media_core"] = None
        # Real Docker Compose renders base 'default: null' but an override
        # that explicitly preserves it can render 'default: {}'.
        candidate["services"]["trakt-bridge"]["networks"]["default"] = {}
        candidate["services"]["trakt-bridge"]["environment"].update(env)
        candidate["networks"]["media_core"] = {"name": ops.AIO_NETWORK, "external": True}
        ops.validate_composed(base, candidate, env)
        mutated = json.loads(json.dumps(candidate))
        mutated["services"]["trakt-bridge"]["networks"]["default"] = {"aliases":["unsafe"]}
        with self.assertRaisesRegex(ops.Stop, "DEFAULT_NETWORK_CHANGED"):
            ops.validate_composed(base, mutated, env)
        mutated = json.loads(json.dumps(candidate))
        mutated["networks"]["default"]["name"] = "other_network"
        with self.assertRaisesRegex(ops.Stop, "PROJECT_DEFAULT_NETWORK_CHANGED"):
            ops.validate_composed(base, mutated, env)
        mutated = json.loads(json.dumps(candidate))
        mutated["networks"]["media_core"]["external"] = False
        with self.assertRaisesRegex(ops.Stop, "GATEWAY_NETWORK_MUST_BE_EXTERNAL"):
            ops.validate_composed(base, mutated, env)
        candidate["services"]["trakt-bridge"]["ports"].append({"published":"7101"})
        with self.assertRaisesRegex(ops.Stop, "BRIDGE_COMPOSE_FIELD_CHANGED"):
            ops.validate_composed(base, candidate, env)

    def test_scoped_filter_preference_read_only_and_graph_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            database = Path(td) / "bridge.db"
            db = sqlite3.connect(database)
            db.execute("CREATE TABLE profiles(id TEXT,access_token_enc TEXT)")
            db.execute("CREATE TABLE media_cache(cache_key TEXT,payload_json TEXT,expires_at INTEGER)")
            db.execute("INSERT INTO profiles VALUES (?,?)", (PROFILE,"opaque-encrypted"))
            shows = {str(i+1): {"canonicalImdb": "tt1234567", "aliases": []}
                     for i in range(17)}
            shows["1"]["aliases"] = ["tt7654321"]
            db.execute("INSERT INTO media_cache VALUES (?,?,strftime('%s','now')+3600)",
                       ("identity-graph:v1:" + PROFILE,
                        json.dumps({"revision":1,"shows":shows,"conflicts":[]})))
            db.commit()
            self.assertEqual(ops.graph_identity(database, USER), PROFILE)
            scope = r"\x1f".join((PROFILE, "aiostreams", ops.UPSTREAM, USER))
            key = ("gateway-filter:v1:" + PROFILE + ":"
                   + hashlib.sha256(scope.encode()).hexdigest()[:24])
            db.execute("INSERT INTO media_cache VALUES (?,?,strftime('%s','now')+3600)",
                       (key, json.dumps({"enabled":True})))
            db.commit()
            with self.assertRaisesRegex(ops.Stop, "FILTER_ALREADY_ON"):
                ops.graph_identity(database, USER)
            db.close()

    def test_check_mode_cannot_recreate_bridge(self):
        with patch.object(ops, "inspect", return_value={"preflight": True}), \
             patch.object(ops, "apply") as apply, \
             patch("sys.argv", ["script"]), \
             patch("builtins.print"):
            ops.main()
        apply.assert_not_called()

    def test_apply_requires_exact_confirmation(self):
        with patch("sys.argv", ["script", "--apply"]):
            with self.assertRaisesRegex(ops.Stop, "EXPLICIT_CONFIRMATION"):
                ops.main()

    def test_source_has_no_dangerous_host_operations(self):
        contents = SCRIPT.read_text()
        for banned in (
            "docker compose down", "docker system prune", "docker volume prune",
            "docker network connect", "docker network disconnect",
            "docker push", "gh release create", "docker run -d",
            "sqlite3.connect('/aio", "nginx -s reload",
        ):
            self.assertNotIn(banned, contents)
        self.assertIn("--force-recreate", contents)
        self.assertIn("--no-deps", contents)
        self.assertIn("BASE_COMPOSE_ROLLBACK=HEALTHY", contents)
        self.assertIn("CLIENT_ROUTING_CHANGED=NO", contents)


if __name__ == "__main__":
    unittest.main()
