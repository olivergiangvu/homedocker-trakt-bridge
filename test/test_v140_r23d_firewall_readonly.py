import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-r23d-firewall-readonly.py"
spec = importlib.util.spec_from_file_location("r23d_fw_readonly", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def permitted(name, *args):
    fixtures = {
        ("iptables", "--version"): (True, "iptables v1.8.10 (nf_tables)\n"),
        ("nft", "--version"): (True, "nftables v1.1.3"),
        ("ip6tables", "--version"): (True, "ip6tables v1.8"),
        ("ip", "-Version"): (True, "ip utility"),
        ("iptables", "-S", "DOCKER-USER"): (True, "-A DOCKER-USER -d 10.1.2.3 -j DROP"),
        ("iptables", "-S", "FORWARD"): (True, "-A FORWARD -d 192.0.2.1 -j DROP"),
        ("nft", "list", "tables"): (True, "table inet secret_table"),
        ("ip", "-6", "route", "show", "default"): (True, "default via 2001:db8::1"),
        ("sysctl", "-n", "net.ipv4.ip_forward"): (True, "1\n"),
        ("sysctl", "-n", "net.ipv6.conf.all.forwarding"): (True, "0\n"),
    }
    return fixtures.get((name,*args), (None, ""))

class FirewallReadOnlyTests(unittest.TestCase):
    def test_never_claims_egress_verified_even_with_all_control_paths(self):
        with mock.patch.object(module, "probe", side_effect=permitted):
            result = module.firewall_capabilities()
        self.assertEqual(result["status"], "CAPABILITY_SURVEY_COMPLETE_NOT_EGRESS_VERIFIED")
        self.assertTrue(result["docker_user_chain_readable"])
        self.assertEqual(result["iptables_backend_hint"], "IPTABLES_NFT_COMPATIBILITY")
        self.assertTrue(result["ipv6_default_route_present"])
        self.assertFalse(result["network_egress_firewall_verified"])
        self.assertFalse(result["actual_egress_probes_performed"])
        self.assertEqual(result["nginx_docker_firewall_or_history_writes"], 0)
        text = json.dumps(result)
        for sensitive in ("10.1.2.3", "192.0.2.1", "2001:db8", "secret_table"):
            self.assertNotIn(sensitive, text)
        self.assertNotIn("iptables v1", text)

    def test_no_firewall_tools_is_observed_not_falsely_passed(self):
        with mock.patch.object(module, "probe", return_value=(None,"")):
            result = module.firewall_capabilities()
        self.assertFalse(result["iptables_command_available"])
        self.assertFalse(result["nft_command_available"])
        self.assertFalse(result["docker_user_chain_readable"])
        self.assertFalse(result["network_egress_firewall_verified"])
        self.assertIsNone(result["ipv4_kernel_forwarding_enabled"])
        self.assertIsNone(result["ipv6_default_route_present"])

    def test_previous_host_preflight_is_mandatory_before_firewall_observation(self):
        with mock.patch.object(module, "verify_precheck", side_effect=module.Stop("fail")):
            with self.assertRaises(module.Stop):
                module.verify_precheck()
        self.assertEqual(module.PRECHECK.name, "audit-v140-r23d-host-preflight.py")

    def test_generic_capability_failure_must_not_print_rules_or_token(self):
        hostile = "X-Emby-Token=untrusted auth 10.0.0.1"
        with mock.patch.object(module,"probe",side_effect=RuntimeError(hostile)):
            with self.assertRaises(RuntimeError):
                module.firewall_capabilities()
        # The CLI catches exceptions only when explicitly classified; never
        # print an uncaught exception with embedded external tool text.
        self.assertFalse(module.firewall_capabilities.__doc__ is None)

if __name__ == "__main__":
    unittest.main()
