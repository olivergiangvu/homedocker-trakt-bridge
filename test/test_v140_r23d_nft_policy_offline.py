import importlib.util
import json
from pathlib import Path
import re
import unittest

SOURCE=Path(__file__).resolve().parents[1]/"scripts"/"render-v140-r23d-nft-policy-offline.py"
spec=importlib.util.spec_from_file_location("r23d1e_policy_offline",SOURCE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class OfflineR23DPolicyTests(unittest.TestCase):
    def test_exactly_two_interface_scoped_chains_first_input_allow_then_drop(self):
        policy=mod.render_policy(**mod.SYNTHETIC)
        interface=mod.SYNTHETIC["bridge"]
        self.assertIn("table inet homedocker_r23d_guard {",policy)
        self.assertEqual(policy.count("type filter hook"),2)
        self.assertEqual(policy.count('iifname "'+interface+'"'),3)
        self.assertEqual(policy.count('iifname "'+interface+'" drop'),2)
        self.assertNotIn("flush ruleset",policy)
        self.assertNotIn("delete table",policy)
        self.assertNotIn("add rule ip filter",policy)
        self.assertNotIn("iptables",policy)
        self.assertIn("hook input priority -20; policy accept",policy)
        self.assertIn("hook forward priority -20; policy accept",policy)
        # Forward is DROP only for this newly owned Docker bridge; host/LAN
        # and all existing Docker network interfaces remain untouched.
        fwd=policy.split("chain r23d_forward {")[1].split("}",1)[0]
        self.assertEqual(len(re.findall(r"iifname ",fwd)),1)
        self.assertEqual(fwd.count(" drop"),1)
        self.assertNotIn(" accept\n",fwd)
        inp=policy.split("chain r23d_input {")[1].split("}",1)[0]
        allow=inp.index(" ip saddr ")
        stop=inp.index(' drop')
        self.assertLess(allow,stop)
        self.assertIn("ip daddr "+mod.SYNTHETIC["host_gateway"],inp)
        self.assertIn("ip saddr "+mod.SYNTHETIC["canary"],inp)
        self.assertIn("tcp dport 17420 accept",inp)
        self.assertEqual(inp.count(" accept"),1)
        self.assertEqual(inp.count(" drop"),1)

    def test_rejects_outside_bridge_name_or_relay_destination(self):
        ok=dict(mod.SYNTHETIC)
        for name in ("docker0","eth0","lo","br-other123",
                     "br-r23dthis_name_is_far_too_long",
                     'br-r23dAA', 'br-r23dfoo"; flush ruleset'):
            with self.subTest(name=name),self.assertRaises(mod.Stop):
                mod.render_policy(**{**ok,"bridge":name})
        for port in (22,80,3240,0,17421,65535,"17420",None,True):
            with self.subTest(port=port),self.assertRaises(mod.Stop):
                mod.render_policy(**{**ok,"relay_port":port})

    def test_rejects_non_private_network_and_aliased_gateway(self):
        ok=dict(mod.SYNTHETIC)
        for field,bad in [
            ("subnet","192.0.2.0/28"),
            ("subnet","0.0.0.0/0"),
            ("subnet","172.31.254.0/16"),
            ("subnet","172.31.254.0/31"),
            ("host_gateway","127.0.0.1"),
            ("host_gateway","172.31.254.99"),
            ("host_gateway","172.31.254.2"),
            ("canary","172.31.254.0"),
            ("canary","172.31.254.15"),
            ("canary","10.0.0.1"),
            ("canary","172.31.254.1"),
            ("canary","172.31.254.02"),
        ]:
            with self.subTest(field=field,bad=bad),self.assertRaises(mod.Stop):
                mod.render_policy(**{**ok,field:bad})

    def test_blueprint_reports_synthetic_not_host_verified(self):
        info=mod.offline_fixture_report()
        self.assertEqual(info["status"],"SYNTHETIC_FIXTURE_ONLY_NO_HOST_CHANGES")
        self.assertTrue(info["fixture_only"])
        self.assertFalse(info["nft_kernel_syntax_check_run"])
        self.assertFalse(info["host_bridge_interface_or_addresses_verified"])
        self.assertFalse(info["network_egress_firewall_verified"])
        self.assertFalse(info["jellyfin_session_or_relay_allowed"])
        self.assertEqual(info["docker_firewall_nginx_aio_history_writes"],0)
        self.assertEqual(info["required_rollback_sequence"][0],
                         "STOP_EXACT_NEW_R23D_GATEWAY_CANARY_FIRST")
        self.assertEqual(info["required_rollback_sequence"][-1],
                         "VERIFY_RC6_R23B_R23A_AND_CLIENT_ROUTES_UNCHANGED")
        self.assertIsInstance(json.loads(json.dumps(info)),dict)

    def test_generator_cannot_run_system_firewall_or_network_subprocess(self):
        src=SOURCE.read_text()
        for forbidden in ("import subprocess","import socket","import os\n",
                          "os.system(", "exec(", "eval(", "subprocess.run",
                          "nft add", "iptables -I", "docker network create",
                          "nft -f", "--apply", "--confirm"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden,src)

if __name__=="__main__":
    unittest.main()
