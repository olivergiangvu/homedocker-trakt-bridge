"""R23D-2B1I joint NFT/Docker synthetic evidence regression; no host APIs."""
import ast
import copy
import importlib.util
from pathlib import Path
import unittest

SRC=Path(__file__).resolve().parents[1]/"scripts"/"verify-v140-r23d2b1i-nft-docker-evidence-offline.py"
spec=importlib.util.spec_from_file_location("r23d2b1i_joint",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class JointOfflineEvidence(unittest.TestCase):
    def setUp(self):
        self.nft,self.containers,self.networks=m.fixture()
    def run_attest(self):
        return m.attest_synthetic_evidence(self.nft,self.containers,self.networks)
    def test_exact_fixture_passes_but_never_authorizes_runtime(self):
        r=self.run_attest()
        self.assertEqual(r["status"],"SYNTHETIC_STRUCTURE_PASS_LIVE_GUARD_NOT_ATTESTED")
        self.assertFalse(r["real_canary_start_authorized"])
        self.assertFalse(r["packet_path_enforcement_proven"])
        self.assertFalse(r["evidence_read_from_real_kernel_or_docker"])
        self.assertFalse(r["authenticated_aio_relay_allowed"])
    def test_mutated_image_id_rejected(self):
        self.containers[0]["Image"]="sha256:"+"b"*64
        with self.assertRaises(m.Stop):self.run_attest()
    def test_mutated_original_source_tag_rejected(self):
        self.containers[0]["Config"]["Image"]="homedocker-trakt-bridge:v140-r23d-"+"a"*12
        with self.assertRaises(m.Stop):self.run_attest()
    def test_docker_restart_always_rejected(self):
        self.containers[0]["HostConfig"]["RestartPolicy"]["Name"]="always"
        with self.assertRaises(m.Stop):self.run_attest()
    def test_missing_docker_restart_policy_rejected(self):
        del self.containers[0]["HostConfig"]["RestartPolicy"]
        with self.assertRaises(m.Stop):self.run_attest()
    def test_privileged_container_rejected(self):
        self.containers[0]["HostConfig"]["Privileged"]=True
        with self.assertRaises(m.Stop):self.run_attest()
    def test_extra_network_peer_rejected(self):
        self.networks[0]["Containers"]["b"*64]={"Name":"intruder","IPv4Address":"172.31.254.3/28"}
        with self.assertRaises(m.Stop):self.run_attest()
    def test_extra_container_network_rejected(self):
        self.containers[0]["NetworkSettings"]["Networks"]["bridge"]={"IPAddress":"172.17.0.12"}
        with self.assertRaises(m.Stop):self.run_attest()
    def test_ipv6_enablement_rejected(self):
        self.networks[0]["EnableIPv6"]=True
        with self.assertRaises(m.Stop):self.run_attest()
    def test_public_gateway_binding_rejected(self):
        self.containers[0]["HostConfig"]["PortBindings"]["7101/tcp"][0]["HostIp"]="0.0.0.0"
        with self.assertRaises(m.Stop):self.run_attest()
    def test_proof_mount_writable_rejected(self):
        self.containers[0]["Mounts"][0]["RW"]=True
        with self.assertRaises(m.Stop):self.run_attest()
    def test_expected_guard_rule_missing_rejected(self):
        self.nft["nftables"].pop()
        with self.assertRaises(m.Stop):self.run_attest()
    def test_guard_allow_rule_modified_rejected(self):
        self.nft["nftables"][-3]["rule"]["expr"][-1]={"drop":None}
        with self.assertRaises(m.Stop):self.run_attest()
    def test_guard_chain_priority_modified_rejected(self):
        self.nft["nftables"][2]["chain"]["prio"]=0
        with self.assertRaises(m.Stop):self.run_attest()
    def test_additional_nft_rule_rejected(self):
        extra=copy.deepcopy(self.nft["nftables"][-1])
        self.nft["nftables"].append(extra)
        with self.assertRaises(m.Stop):self.run_attest()
    def test_unexpected_dns_or_extra_hosts_rejected(self):
        self.containers[0]["HostConfig"]["ExtraHosts"]=["host:127.0.0.1"]
        with self.assertRaises(m.Stop):self.run_attest()
    def test_no_host_mutating_or_reading_imports(self):
        tree=ast.parse(SRC.read_text(encoding="utf8"))
        mods=set()
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):
                mods.update(x.name.split(".")[0] for x in n.names)
            elif isinstance(n,ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertFalse(mods & {"subprocess","socket","os","urllib","requests","ctypes","shutil"})
        self.assertNotIn("subprocess.Popen",SRC.read_text())
    def test_docker_container_evidence_cardinality_rejected(self):
        self.containers.append(copy.deepcopy(self.containers[0]))
        with self.assertRaises(m.Stop):self.run_attest()

if __name__=="__main__":
    unittest.main()
