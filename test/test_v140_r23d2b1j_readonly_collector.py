"""R23D-2B1J read-only real Docker/nft collector; all host calls mocked."""
import ast
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"scripts"/"collect-v140-r23d2b1j-docker-nft-readonly.py"
spec=importlib.util.spec_from_file_location("r23d2b1j_collector",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def absence_outputs():
    return {
        m.LIST_COMMANDS[0]:"trakt-bridge\naiostreams\n",
        m.LIST_COMMANDS[1]:"bridge\nhost\n",
        m.LIST_COMMANDS[2]:json.dumps([{"ifname":"lo"},{"ifname":"docker0"}]),
        m.LIST_COMMANDS[3]:json.dumps({"nftables":[
            {"metainfo":{"version":"1.0"}},{"table":{"family":"inet","name":"unrelated"}}]}),
    }

class CollectorTests(unittest.TestCase):
    def test_classify_all_absent(self):
        o=absence_outputs()
        self.assertEqual(m.presence_report(o[m.LIST_COMMANDS[0]],
            o[m.LIST_COMMANDS[1]],m.parse(o[m.LIST_COMMANDS[2]]),
            m.parse(o[m.LIST_COMMANDS[3]])),"ABSENT")
    def test_absent_path_inherits_protected_baseline(self):
        o=absence_outputs()
        real_mod=m.module
        prior=mock.Mock()
        prior.audit.return_value={"status":"INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD"}
        prior.names=real_mod(m.ABSENCE,"r23d2b1j_test_names").names
        def loader(path,name):
            return prior if path==m.ABSENCE else real_mod(path,name)
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"read",side_effect=lambda x:o[tuple(x)]) as reads, \
             mock.patch.object(m,"module",side_effect=loader):
            result=m.audit()
        self.assertEqual(result["status"],"ABSENT_CONFIRMED_BOOT_FENCING_HOLD")
        self.assertTrue(result["protected_absence_baseline_rechecked"])
        self.assertFalse(result["real_canary_start_authorized"])
        self.assertFalse(result["authenticated_aio_relay_allowed"])
        self.assertEqual(reads.call_count,4)
        prior.audit.assert_called_once()
    def test_absence_protected_baseline_failure_stops(self):
        o=absence_outputs()
        rmod=m.module(m.ABSENCE,"r23d2b1j_test_names_2")
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"read",side_effect=lambda x:o[tuple(x)]), \
             mock.patch.object(rmod,"audit",return_value={"status":"STOP"}), \
             mock.patch.object(m,"module",return_value=rmod):
            with self.assertRaisesRegex(m.Stop,"PROTECTED_ABSENCE_BASELINE_CHANGED"):
                m.audit()
    def test_partial_container_presence_stops(self):
        o=absence_outputs()
        with self.assertRaisesRegex(m.Stop,"PARTIAL_OR_COLLIDING_R23D_RESOURCE_SET"):
            m.presence_report(m.CONTAINER+"\n",o[m.LIST_COMMANDS[1]],
                m.parse(o[m.LIST_COMMANDS[2]]),m.parse(o[m.LIST_COMMANDS[3]]))
    def test_partial_guard_presence_stops(self):
        o=absence_outputs()
        doc={"nftables":[{"table":{"family":"inet","name":m.NFT_TABLE}}]}
        with self.assertRaisesRegex(m.Stop,"PARTIAL_OR_COLLIDING_R23D_RESOURCE_SET"):
            m.presence_report(o[m.LIST_COMMANDS[0]],o[m.LIST_COMMANDS[1]],
                m.parse(o[m.LIST_COMMANDS[2]]),doc)
    def test_reserved_nft_name_wrong_family_rejected(self):
        with self.assertRaisesRegex(m.Stop,"UNTRUSTED_NFT_TABLE_NAME_OR_FAMILY"):
            m.presence_report("","",[],{"nftables":[
                {"table":{"family":"ip","name":m.NFT_TABLE}}]})
    def test_guarded_joint_fixture_observation_not_authorization(self):
        j=m.module(m.JOINT,"r23d2b1j_test_fixture")
        nft,containers,networks=j.fixture()
        self.assertTrue(m.preview_structural(containers,networks,nft))
        o={
            m.LIST_COMMANDS[0]:m.CONTAINER+"\n",
            m.LIST_COMMANDS[1]:m.NETWORK+"\n",
            m.LIST_COMMANDS[2]:json.dumps([{"ifname":m.BRIDGE}]),
            m.LIST_COMMANDS[3]:json.dumps({"nftables":[
                {"table":{"family":"inet","name":m.NFT_TABLE}}]}),
            m.DETAIL_COMMANDS[0]:json.dumps(containers),
            m.DETAIL_COMMANDS[1]:json.dumps(networks),
            m.DETAIL_COMMANDS[2]:json.dumps(nft),
        }
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"read",side_effect=lambda x:o[tuple(x)]) as reads:
            result=m.audit()
        self.assertEqual(result["status"],"STRUCTURE_OBSERVED_UNTRUSTED_BOOT_FENCING_HOLD")
        self.assertFalse(result["reader_identity_or_ownership_authenticated"])
        self.assertFalse(result["kernel_packet_enforcement_qualified"])
        self.assertFalse(result["real_canary_start_authorized"])
        self.assertEqual(reads.call_count,7)
    def test_mutated_restart_policy_rejected_in_structural_preview(self):
        j=m.module(m.JOINT,"r23d2b1j_fixture_restart")
        nft,c,n=j.fixture()
        c[0]["HostConfig"]["RestartPolicy"]["Name"]="always"
        with self.assertRaisesRegex(m.Stop,"UNTRUSTED_SNAPSHOT_STRUCTURE_REJECTED"):
            m.preview_structural(c,n,nft)
    def test_wrong_image_identity_rejected(self):
        j=m.module(m.JOINT,"r23d2b1j_fixture_image")
        nft,c,n=j.fixture()
        c[0]["Image"]="sha256:"+"b"*64
        with self.assertRaises(m.Stop):m.preview_structural(c,n,nft)
    def test_extra_network_peer_rejected(self):
        j=m.module(m.JOINT,"r23d2b1j_fixture_peer")
        nft,c,n=j.fixture()
        n[0]["Containers"]["b"*64]={"Name":"intruder","IPv4Address":"172.31.254.3/28"}
        with self.assertRaises(m.Stop):m.preview_structural(c,n,nft)
    def test_policy_rule_drift_rejected(self):
        j=m.module(m.JOINT,"r23d2b1j_fixture_nft")
        nft,c,n=j.fixture()
        nft["nftables"].pop()
        with self.assertRaises(m.Stop):m.preview_structural(c,n,nft)
    def test_mutating_commands_rejected_before_subprocess(self):
        for argv in (("docker","run","bad"),("nft","flush","ruleset"),
                     ("systemctl","start","docker.service"),
                     ("docker","network","create","xyz")):
            with mock.patch.object(m.subprocess,"run",
                                   side_effect=AssertionError("UNSAFE_SUBPROCESS")):
                with self.assertRaisesRegex(m.Stop,"READ_COMMAND_NOT_ALLOWLISTED"):
                    m.read(argv)
    def test_malformed_evidence_rejected(self):
        for bad in ('{"nftables":', '{"nftables": [', "nonsense"):
            with self.assertRaises(m.Stop):m.parse(bad)
    def test_fail_closed_error_redacts_host_snapshot(self):
        with mock.patch.object(m,"audit",side_effect=m.Stop("NOT_A_PUBLIC_ERROR SECRET")), \
             mock.patch("sys.argv",["collector","--audit-only"]), \
             redirect_stderr(io.StringIO()) as err, \
             self.assertRaises(SystemExit):
            m.main()
        self.assertIn("PROTECTED_OR_UNCLASSIFIED_COLLECTOR_STOP",err.getvalue())
        self.assertNotIn("SECRET",err.getvalue())
    def test_rejects_nonroot_without_inspection(self):
        with mock.patch.object(m.os,"geteuid",return_value=1000), \
             mock.patch.object(m,"read",side_effect=AssertionError("EARLY_HOST_READ")):
            with self.assertRaisesRegex(m.Stop,"ROOT_REQUIRED_FOR_READONLY_COLLECTION"):
                m.audit()
    def test_source_limits_commands_and_has_no_apply_path(self):
        source=SRC.read_text(encoding="utf8")
        self.assertIsInstance(ast.parse(source),ast.Module)
        for forbidden in ("shell=True","subprocess.Popen(","docker network create","nft add table","--apply"):
            self.assertNotIn(forbidden,source)

if __name__=="__main__":
    unittest.main()
