"""R23D-2B1H actual host inventory read-only gate: mock regression tests."""
import ast
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SRC=Path(__file__).resolve().parents[1]/"scripts"/"audit-v140-r23d2b1h-real-evidence-readonly.py"
spec=importlib.util.spec_from_file_location("r23d2b1h_inventory",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def nft():
    return {"nftables":[{"metainfo":{"version":"1.0.0"}},
            {"table":{"family":"inet","name":"unrelated_host_table"}},
            {"table":{"family":"ip","name":"filter"}}]}

def links():
    return [{"ifname":"eth0"},{"ifname":"docker0"},{"ifname":"br-r23b42"}]

def checks(**changed):
    data={"container_names":{"trakt-bridge","aiostreams"},
          "network_names":{"bridge","host","homedocker_v140_r23b_local"},
          "link_doc":links(),"nft_doc":nft()}
    data.update(changed)
    return m.evaluate_inventory(**data)

class ReadOnlyInventory(unittest.TestCase):
    def test_real_inventory_absent_means_hold_not_permission(self):
        r=checks()
        self.assertEqual(r["status"],"INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD")
        self.assertFalse(r["kernel_guard_enforcement_proven"])
        self.assertFalse(r["docker_peer_network_runtime_attested"])
        self.assertFalse(r["authenticated_aio_relay_allowed"])
    def test_exact_owned_container_collision_stops(self):
        with self.assertRaisesRegex(m.Stop,"OWNED_RESOURCE_NAME_COLLISION"):
            checks(container_names={m.CANARY})
    def test_exact_owned_network_collision_stops(self):
        with self.assertRaisesRegex(m.Stop,"OWNED_RESOURCE_NAME_COLLISION"):
            checks(network_names={m.NETWORK})
    def test_linux_bridge_collision_stops(self):
        with self.assertRaisesRegex(m.Stop,"EXPECTED_BRIDGE_ALREADY_EXISTS"):
            checks(link_doc=[{"ifname":m.IFACE}])
    def test_owned_nft_table_in_inet_family_stops(self):
        with self.assertRaisesRegex(m.Stop,"OWNED_GUARD_TABLE_PRESENT_NOT_VERIFIED"):
            checks(nft_doc={"nftables":[{"table":{"family":"inet","name":m.NFT_TABLE}}]})
    def test_owned_nft_table_in_other_family_stops(self):
        with self.assertRaisesRegex(m.Stop,"OWNED_GUARD_TABLE_PRESENT_NOT_VERIFIED"):
            checks(nft_doc={"nftables":[{"table":{"family":"ip","name":m.NFT_TABLE}}]})
    def test_malformed_or_non_table_nft_document_stops(self):
        for sample in ([],{},{"nftables":{}},
                       {"nftables":[{"rule":{"chain":"INPUT"}}]},
                       {"nftables":[{"table":{"family":"what","name":"test"}}]}):
            with self.subTest(sample=sample),self.assertRaises(m.Stop):
                checks(nft_doc=sample)
    def test_missing_interface_name_stops(self):
        with self.assertRaisesRegex(m.Stop,"LINUX_INTERFACE_INVENTORY_REJECTED"):
            checks(link_doc=[{"index":1}])
    def test_docker_resource_names_bound_and_validated(self):
        self.assertEqual(m.names("trakt-bridge\naiostreams\n"),{"trakt-bridge","aiostreams"})
        for bad in ("name with spaces\n","same\nsame\n","bad:name\n"):
            with self.subTest(bad=bad),self.assertRaises(m.Stop):
                m.names(bad)
    def test_exact_allowlist_rejects_all_mutation(self):
        for cmd in (["docker","run","anything"],
                    ["docker","network","create","test"],
                    ["nft","add","table","inet","x"],
                    ["systemctl","restart","docker.service"],
                    ["docker","image","pull","node:latest"]):
            with mock.patch.object(m.subprocess,"run",
                                   side_effect=AssertionError("MUTATION")):
                with self.assertRaisesRegex(m.Stop,"COMMAND_NOT_READ_ONLY_ALLOWLISTED"):
                    m.read(cmd)
    def test_mock_host_audit_then_real_inventory_calls(self):
        outputs={
            ("docker","ps","-a","--format","{{.Names}}"):"trakt-bridge\n",
            ("docker","network","ls","--format","{{.Name}}"):"bridge\nhost\n",
            ("ip","-j","link","show"):'[{"ifname":"lo"},{"ifname":"docker0"}]',
            ("nft","-j","list","tables"):'{"nftables":[{"metainfo":{}},{"table":{"family":"inet","name":"foreign"}}]}',
        }
        upstream=mock.Mock()
        upstream.audit.return_value={"status":"PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD"}
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"load_host_audit",return_value=upstream), \
             mock.patch.object(m,"read",side_effect=lambda argv:outputs[tuple(argv)]) as reads:
            result=m.audit()
        self.assertIn("HOLD",result["status"])
        self.assertEqual(reads.call_count,4)
        upstream.audit.assert_called_once()
    def test_prior_protected_host_failure_rejects_inventory(self):
        upstream=mock.Mock()
        upstream.audit.return_value={"status":"STOP"}
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"load_host_audit",return_value=upstream), \
             mock.patch.object(m,"read",side_effect=AssertionError("DO NOT INSPECT")):
            with self.assertRaisesRegex(m.Stop,"PROTECTED_HOST_BASELINE_NOT_VERIFIED"):
                m.audit()
    def test_source_does_not_run_any_mutating_command(self):
        source=SRC.read_text(encoding="utf8")
        self.assertIsInstance(ast.parse(source),ast.Module)
        self.assertNotIn("shell=True",source)
        self.assertNotIn("subprocess.Popen(",source)
        self.assertNotIn("nft add table",source)
        self.assertNotIn("docker rm -f",source)

if __name__=="__main__":
    unittest.main()
