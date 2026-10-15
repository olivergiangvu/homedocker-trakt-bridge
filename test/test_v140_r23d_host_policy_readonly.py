import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

SOURCE=Path(__file__).resolve().parents[1]/"scripts"/"audit-v140-r23d-host-policy-readonly.py"
spec=importlib.util.spec_from_file_location("r23d1c_host_policy",SOURCE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

IPTABLES={
    "INPUT":"""-P INPUT ACCEPT
-A INPUT -s 172.18.9.1/24 -j ACCEPT
-A INPUT -s 10.4.4.1/24 -j DROP
-A INPUT -j ANY_CUSTOM_CHAIN
""",
    "FORWARD":"""-P FORWARD DROP
-A FORWARD -j DOCKER-USER
-A FORWARD -j DOCKER-FORWARD
""",
    "DOCKER-USER":"""-N DOCKER-USER
-A DOCKER-USER -d 192.0.2.44/32 -j DROP
-A DOCKER-USER -j RETURN
"""
}
SS="""LISTEN 0 4096 127.0.0.1:3240 0.0.0.0:*
LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 128 192.0.2.11:80 0.0.0.0:*
LISTEN 0 128 [::]:9000 [::]:*
LISTEN 0 128 [::1]:7000 [::]:*
LISTEN 0 128 192.0.2.1:9240 0.0.0.0:*
"""

class HostPolicyTests(unittest.TestCase):
    def test_chain_summary_only_counts_fixed_jump_labels_not_rule_bodies(self):
        input_=mod.summarize_chain(IPTABLES["INPUT"],"INPUT")
        self.assertEqual(input_["default_policy"],"ACCEPT")
        self.assertEqual(input_["rule_count"],3)
        self.assertEqual(input_["terminal_accept_rules"],1)
        self.assertEqual(input_["terminal_drop_or_reject_rules"],1)
        forward=mod.summarize_chain(IPTABLES["FORWARD"],"FORWARD")
        self.assertTrue(forward["direct_docker_user_jump_present"])
        self.assertEqual(forward["default_policy"],"DROP")
        user=mod.summarize_chain(IPTABLES["DOCKER-USER"],"DOCKER-USER")
        self.assertEqual(user["default_policy"],"UNSET_OR_CUSTOM")
        self.assertEqual(user["terminal_drop_or_reject_rules"],1)

    def test_listener_binding_classes_are_redacted_and_not_reachability(self):
        data=mod.summarize_listeners(SS)
        self.assertEqual(data["3240"]["binding_classes"],["IPV4_LOOPBACK"])
        self.assertEqual(data["22"]["binding_classes"],["IPV4_WILDCARD"])
        self.assertEqual(data["80"]["binding_classes"],["IPV4_HOST_INTERFACE"])
        self.assertEqual(data["9000"]["binding_classes"],["IPV6_WILDCARD"])
        self.assertEqual(data["7000"]["binding_classes"],["IPV6_LOOPBACK"])
        raw=json.dumps(data)
        for sensitive in ("172.18.9.1","192.0.2.11","10.4.4.1"):
            self.assertNotIn(sensitive,raw)

    def test_all_capabilities_still_explicitly_unqualified(self):
        def fake(name,*args):
            if name=="iptables":
                return IPTABLES.get(args[-1])
            if name=="ss":
                return SS
            if name=="nft":
                return "table ip filter\ntable inet user_policy\n"
            return None
        with mock.patch.object(mod,"cmd",side_effect=fake):
            out=mod.inspect_policy()
        self.assertEqual(out["status"],"OBSERVED_NOT_EGRESS_QUALIFIED")
        self.assertEqual(out["native_nft_table_count"],2)
        self.assertFalse(out["network_egress_firewall_verified"])
        self.assertFalse(out["gateway_user_session_allowed"])
        self.assertFalse(out["input_and_forward_rule_order_or_conntrack_qualified"])
        self.assertEqual(out["docker_firewall_nginx_aio_or_watch_state_modifications"],0)
        result=json.dumps(out)
        for secret in ("user_policy","192.0.2.11","10.4.4.1","172.18.9.1"):
            self.assertNotIn(secret,result)

    def test_missing_chain_does_not_fabricate_accept_or_policy(self):
        no=mod.summarize_chain(None,"INPUT")
        self.assertEqual(no["default_policy"],"UNAVAILABLE")
        self.assertIsNone(no["rule_count"])
        self.assertFalse(no["readable"])
        self.assertIsNone(mod.summarize_listeners(None)["22"]["listening"])

    def test_ephemeral_resource_absence_is_mandatory_for_policy_audit(self):
        fake_guard=mock.Mock()
        fake_guard.audit.return_value={"status":"PREFLIGHT_PASS"}
        fake_spec=mock.Mock()
        fake_spec.loader=mock.Mock()
        with mock.patch.object(mod,"PREFLIGHT",SOURCE), \
             mock.patch.object(mod.importlib.util,"spec_from_file_location",return_value=fake_spec), \
             mock.patch.object(mod.importlib.util,"module_from_spec",return_value=fake_guard), \
             mock.patch.object(mod,"docker_object_exists",side_effect=[False,False]):
            self.assertTrue(mod.baseline())
        with mock.patch.object(mod,"PREFLIGHT",SOURCE), \
             mock.patch.object(mod.importlib.util,"spec_from_file_location",return_value=fake_spec), \
             mock.patch.object(mod.importlib.util,"module_from_spec",return_value=fake_guard), \
             mock.patch.object(mod,"docker_object_exists",side_effect=[True]):
            with self.assertRaisesRegex(mod.Stop,"EPHEMERAL_PROBE_CLEANUP_NOT_CONFIRMED"):
                mod.baseline()

    def test_unexpected_raw_firewall_tool_exception_never_escapes_to_cli(self):
        unsafe="private rule 172.19.15.9, session=secret"
        stream=io.StringIO()
        with mock.patch.object(mod,"baseline",return_value=True), \
             mock.patch.object(mod,"inspect_policy",side_effect=RuntimeError(unsafe)), \
             redirect_stderr(stream),self.assertRaises(SystemExit) as caught:
            mod.main()
        self.assertEqual(caught.exception.code,1)
        text=stream.getvalue()
        self.assertIn("R23D1C_SAFE_DIAGNOSTIC",text)
        self.assertNotIn(unsafe,text)

if __name__=="__main__":
    unittest.main()
