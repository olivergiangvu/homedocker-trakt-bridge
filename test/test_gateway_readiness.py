import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "audit-gateway-readiness.py"
spec = importlib.util.spec_from_file_location("gateway_readiness", path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class GatewayReadinessAuditTests(unittest.TestCase):
    def test_finds_rc6_provenance_without_printing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dbfile = root / "bridge.db"
            with sqlite3.connect(dbfile) as db:
                db.execute(
                    "CREATE TABLE media_cache "
                    "(cache_key TEXT, payload_json TEXT)"
                )
                db.execute(
                    "INSERT INTO media_cache VALUES (?,?)",
                    (
                        "identity-alias:v1:secret-profile-abc",
                        '{"shows":{"263102":{"traktImdb":"tt44051354",'
                        '"preferredMetaId":"tt44094505"}}}',
                    )
                )
                db.execute(
                    "INSERT INTO media_cache VALUES (?,?)",
                    (
                        "some-other-key",
                        '{"tokens":"do-not-print-private-data"}',
                    )
                )
            info = {
                "Mounts": [{"Destination": "/app/data", "Source": str(root)}]
            }
            result = audit.sqlite_status(info)
            self.assertTrue(result["legacy_alias_known_show"])
            self.assertTrue(result["legacy_alias_matches"])
            self.assertTrue(result["legacy_canonical_matches"])
            self.assertFalse(result["verified_graph_present"])
            self.assertNotIn("secret-profile-abc", str(result))
            self.assertNotIn("do-not-print-private-data", str(result))

    def test_graph_detected_after_upgrade_without_exposing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with sqlite3.connect(root / "bridge.db") as db:
                db.execute("CREATE TABLE media_cache (cache_key TEXT, payload_json TEXT)")
                db.execute(
                    "INSERT INTO media_cache VALUES (?,?)",
                    (
                        "identity-graph:v1:a-connected-profile",
                        '{"shows":{"263102":{"canonicalImdb":"tt44051354",'
                        '"aliases":["tt44094505"]}}}',
                    )
                )
            result = audit.sqlite_status({
                "Mounts": [{"Destination": "/app/data", "Source": str(root)}]
            })
            self.assertTrue(result["verified_graph_present"])
            self.assertTrue(result["verified_graph_known_show"])
            self.assertTrue(result["verified_graph_alias_matches"])
            self.assertNotIn("a-connected-profile", str(result))

    def test_network_plan_does_not_mutate_docker(self):
        br = {
            "State": {"Running": True},
            "NetworkSettings": {"Networks": {
                "homedocker-trakt-bridge_default": {}
            }}
        }
        aio = {
            "State": {"Running": True},
            "Config": {"Image": "ghcr.io/viren070/aiostreams:latest"},
            "NetworkSettings": {
                "Networks": {
                    "media_core_net": {"Aliases": ["aiostreams", "abc"]}
                },
                "Ports": {
                    "3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3240"}]
                },
            }
        }
        mock = [{"Driver": "bridge", "Internal": False,
                 "Containers": {"xyz": {"Name": "aiostreams"}}}]
        with patch.object(audit, "docker_json", return_value=mock) as tool:
            status = audit.valid_networks(br, aio)
        tool.assert_called_once_with("network", "inspect", "media_core_net")
        self.assertTrue(status["aio_has_network_dns_alias"])
        self.assertTrue(status["aio_loopback_host_3240"])
        self.assertFalse(status["bridge_already_on_media_core_net"])
        self.assertEqual(status["aio_internal_port"], 3000)

    def test_never_edit_sqlite_during_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with sqlite3.connect(root / "bridge.db") as db:
                db.execute("CREATE TABLE media_cache (cache_key TEXT, payload_json TEXT)")
            info = {
                "Mounts": [{"Destination": "/app/data", "Source": str(root)}]
            }
            with patch.object(audit.sqlite3, "connect", wraps=sqlite3.connect) as connect:
                audit.sqlite_status(info)
                self.assertIn("mode=ro", connect.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
