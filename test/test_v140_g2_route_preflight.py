"""G2-0 read-only route preflight and no-reconfiguration contract tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-v140-g2-route-preflight.py"
spec = importlib.util.spec_from_file_location("g2_route_preflight", SCRIPT)
g2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g2)


class StubG1A:
    pass


class StubG1B:
    def __init__(self): self.source_reads = []
    def inspect_production(self):
        self.source_reads.append("inspect_rc6")
        return "sha256:" + "a" * 64, Path("/nonexistent-bridge.db")
    def snapshot(self, db, g1a):
        self.source_reads.append("read_only_legacy")
        return {"shows": 17, "groups": 1}


class StubG1C:
    def __init__(self): self.topology_reads = 0
    def aio_topology(self):
        self.topology_reads += 1
        return True


class Tests(unittest.TestCase):
    def test_unbound_candidate_ports_do_not_claim_a_live_route(self):
        b, c = StubG1B(), StubG1C()
        data = g2.inspect_baseline(StubG1A(), b, c, ports={80, 443, 3240, 9000})
        self.assertEqual(data["status"], "G2_ROUTE_BASELINE_READY_NO_APPLY")
        self.assertEqual(data["verified_show_candidates"], 17)
        self.assertEqual(data["verified_alias_groups"], 1)
        self.assertEqual(data["preview_ingress_port_candidate"], 17431)
        self.assertEqual(data["preview_gateway_port_candidate"], 17432)
        self.assertTrue(data["candidate_ports_currently_unbound"])
        self.assertFalse(data["client_nextup_request_path_observed"])
        self.assertFalse(data["nginx_preview_configuration_installed"])
        self.assertFalse(data["gateway_candidate_created"])
        self.assertFalse(data["real_client_qualified"])
        self.assertFalse(data["app_client_server_address_modified"])
        self.assertEqual(data["docker_resources_changed"], 0)
        self.assertEqual(data["production_sqlite_writes"], 0)
        self.assertTrue(data["release_hold_active"])
        self.assertEqual(b.source_reads, ["inspect_rc6", "read_only_legacy"])
        self.assertEqual(c.topology_reads, 1)

    def test_both_reserved_candidates_refuse_collision(self):
        for occupied in ({17431}, {17432}, {17431, 17432}):
            with self.subTest(occupied=occupied), self.assertRaises(g2.Stop) as ctx:
                g2.inspect_baseline(StubG1A(), StubG1B(),
                                    StubG1C(), ports=occupied)
            self.assertEqual(str(ctx.exception),
                             "PROPOSED_PREVIEW_PORT_UNAVAILABLE")

    def test_read_only_proc_tcp_and_tcp6_distinguish_listen_from_connected(self):
        with tempfile.TemporaryDirectory() as directory:
            ipv4 = Path(directory) / "tcp"
            ipv6 = Path(directory) / "tcp6"
            head = "sl local_address rem_address st tx_queue rx_queue tr tm_when retrnsmt uid timeout inode"
            ipv4.write_text(head + "\n" +
                "0: 0100007F:4417 00000000:0000 0A 0 0 0 0 0 0 0\n" +
                "1: 0100007F:4418 0100007F:9999 01 0 0 0 0 0 0 0\n")
            ipv6.write_text(head + "\n" +
                "0: 00000000000000000000000000000000:4418 00000000000000000000000000000000:0000 0A 0 0 0 0 0 0 0\n")
            self.assertEqual(g2.listening_ports((ipv4, ipv6)), {0x4417, 0x4418})
            self.assertIn(17431, g2.listening_ports((ipv4, ipv6)))
            self.assertIn(17432, g2.listening_ports((ipv4, ipv6)))

    def test_unavailable_proc_table_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(g2.Stop) as ctx:
                g2.listening_ports((Path(directory) / "not-present",))
            self.assertEqual(str(ctx.exception), "KERNEL_SOCKET_TABLE_UNAVAILABLE")

    def test_no_other_mode_accepted_before_aio_or_docker(self):
        command = [sys.executable, "-B", str(SCRIPT), "--mode", "pilot"]
        p = subprocess.run(command, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr)

    def test_output_must_not_contain_legacy_profile_or_token(self):
        info = g2.inspect_baseline(StubG1A(), StubG1B(), StubG1C(), ports=set())
        result = json.dumps(info)
        for forbidden in ("authorization", "access_token_enc",
                          "tt44051354", "tt44094505", "a"*24):
            self.assertNotIn(forbidden, result)


if __name__ == "__main__":
    unittest.main()
