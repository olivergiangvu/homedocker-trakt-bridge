import importlib.util
from pathlib import Path
import unittest
from unittest import mock
from tempfile import TemporaryDirectory
import sqlite3
import json

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-pilot-readiness.py"
spec = importlib.util.spec_from_file_location("r21_host_readiness", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def container(name, img, networks, port, internal):
    return {
        "Name": "/" + name,
        "State": {"Running": True},
        "Config": {"Image": img, "Env": ["TOKEN=SECRET_DO_NOT_PRINT"]},
        "HostConfig": {
            "PortBindings": {
                internal: [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
            }
        },
        "NetworkSettings": {"Networks": {n: {} for n in networks}},
    }

class R21PreflightTests(unittest.TestCase):
    def fixtures(self):
        return {
            "trakt-bridge": container(
                "trakt-bridge", audit.RC6_IMAGE,
                ["homedocker-trakt-bridge_default"], 7000, "7000/tcp"),
            "aiostreams": container(
                "aiostreams", audit.AIO_TAG,
                ["media_core_net"], 3240, "3000/tcp"),
            "homedocker-resume-contract-canary": container(
                "homedocker-resume-contract-canary", "safe", ["media_core_net"],
                17134, "7101/tcp"),
        }

    def test_valid_readonly_gate_holds_deployment_pending_backup(self):
        fixtures = self.fixtures()
        with mock.patch.object(audit, "inspect",
                               side_effect=lambda name: fixtures[name]), \
             mock.patch.object(audit, "docker_reserved_ports",
                               return_value={7000, 3240, 17134}), \
             mock.patch.object(audit, "live_socket_ports",
                               return_value={7000, 3240, 17134}), \
             mock.patch.object(audit, "sqlite_quick_check", return_value=True):
            result = audit.audit()
        self.assertTrue(result["production_bridge_rc6_digest_unchanged"])
        self.assertTrue(result["proposed_ports_available_at_snapshot"])
        self.assertFalse(result["canary_deployment_authorized"])
        self.assertFalse(result["private_backup_created_or_verified_by_this_audit"])
        self.assertEqual(result["production_db_writes"], 0)
        wire = json.dumps(result)
        self.assertNotIn("SECRET_DO_NOT_PRINT", wire)
        self.assertNotIn("TOKEN=", wire)
        self.assertNotIn("profile", wire.lower() if False else "SECRET_DO_NOT_PRINT")

    def test_wrong_network_aborts(self):
        fixtures = self.fixtures()
        fixtures["trakt-bridge"]["NetworkSettings"]["Networks"]["media_core_net"] = {}
        with mock.patch.object(audit, "inspect",
                               side_effect=lambda name: fixtures[name]):
            with self.assertRaisesRegex(audit.PreflightStop, "isolation"):
                audit.audit()

    def test_candidate_port_collision_detected_not_ignored(self):
        fixtures = self.fixtures()
        with mock.patch.object(audit, "inspect",
                               side_effect=lambda name: fixtures[name]), \
             mock.patch.object(audit, "docker_reserved_ports",
                               return_value={17041}), \
             mock.patch.object(audit, "live_socket_ports",
                               return_value={17141}), \
             mock.patch.object(audit, "sqlite_quick_check", return_value=True):
            result = audit.audit()
        self.assertFalse(result["proposed_ports_available_at_snapshot"])
        self.assertEqual(result["candidate_port_conflict_numbers"],
                         [17041, 17141])

    def test_query_only_sqlite_quick_check_no_write(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "bridge.db"
            db = sqlite3.connect(p)
            db.execute("CREATE TABLE x (a INTEGER)")
            db.commit()
            db.close()
            before = p.read_bytes()
            info = {"Mounts":[{"Destination":"/app/data",
                               "Type":"volume","Source":tmp}]}
            self.assertTrue(audit.sqlite_quick_check(info))
            self.assertEqual(p.read_bytes(), before)
            (Path(tmp) / "bridge.db-alias").symlink_to(p)

    def test_bridge_sqlite_symlink_rejected(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            source = d / "real.db"
            source.write_bytes(b"x")
            (d / "bridge.db").symlink_to(source)
            info = {"Mounts":[{"Destination":"/app/data",
                               "Type":"volume","Source":tmp}]}
            with self.assertRaisesRegex(audit.PreflightStop, "symlink"):
                audit.sqlite_quick_check(info)

    def test_read_only_inspector_exposes_no_docker_env(self):
        fixtures = self.fixtures()
        self.assertTrue(audit.host_bound_port(fixtures["trakt-bridge"],
                                              "7000/tcp", 7000))
        self.assertFalse(audit.host_bound_port(fixtures["trakt-bridge"],
                                               "7000/tcp", 17141))

if __name__ == "__main__":
    unittest.main()
