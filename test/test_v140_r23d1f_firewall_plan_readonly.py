import importlib.util
import io
import ipaddress
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"scripts"/"audit-v140-r23d1f-firewall-plan-readonly.py"
spec=importlib.util.spec_from_file_location("r23d1f_firewall_plan",SRC)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def result(argv,*,stdin=None,may_fail=False):
    arg=tuple(argv)
    fixtures={
        ("ip","-j","-4","route","show","table","all"): [
            {"dst":"default","gateway":"192.0.2.1"},
            {"dst":"10.231.240.0/28","dev":"vpn0"},
            {"dst":"192.168.50.0/24","dev":"eth0"}
        ],
        ("ip","-j","-4","address","show"): [
            {"ifname":"eth0","addr_info":[{"family":"inet","local":"192.168.50.2","prefixlen":24}]},
            {"ifname":"lo","addr_info":[{"family":"inet","local":"127.0.0.1","prefixlen":8}]}
        ],
        ("ip","-j","link","show"): [
            {"ifname":"eth0"},{"ifname":"lo"},{"ifname":"br-unrelated"}
        ],
        ("docker","network","inspect","a"*12,"b"*12): [
            {"IPAM":{"Config":[{"Subnet":"172.30.250.0/24"}]}},
            {"IPAM":{"Config":[{"Subnet":"172.17.0.0/16"}]}}
        ],
        ("nft","-j","list","tables"): {
            "nftables":[{"metainfo":{"version":"1.0"}},
                        {"table":{"family":"inet","name":"other_rules"}},
                        {"table":{"family":"ip","name":"nat"}}]
        },
    }
    strings={
        ("docker","network","ls","-q"): ("a"*12+"\n"+"b"*12+"\n").encode(),
        ("docker","network","ls","--format","{{.Name}}"): b"host\nbridge\nother_net\n",
        ("docker","ps","-a","--format","{{.Names}}"): b"aiostreams\ntrakt-bridge\n",
    }
    if arg in fixtures:
        return json.dumps(fixtures[arg]).encode()
    if arg in strings:
        return strings[arg]
    if arg==("nft","--check","--file","-"):
        assert isinstance(stdin,bytes)
        s=stdin.decode()
        assert "iifname" in s and "hook input priority -20" in s
        assert "hook forward priority -20" in s
        assert "drop" in s
        return b""
    raise AssertionError("unexpected/writing tool command: "+str(arg))

class R23D1FReadOnlyTests(unittest.TestCase):
    def test_inventory_reserves_unused_explicit_bridge_names_and_rfc1918(self):
        with mock.patch.object(mod,"run",side_effect=result):
            net,renderer=mod.inspect_candidates()
            self.assertEqual(str(net),"10.231.240.16/28")
            self.assertTrue(mod.syntactic_check(net,renderer))

    def test_route_docker_and_host_overlap_are_all_rejected(self):
        routes=[{"dst":"10.231.240.0/25"}]
        addresses=[{"ifname":"eth0","addr_info":[{"family":"inet","local":"10.231.240.34","prefixlen":28}]}]
        networks=[{"IPAM":{"Config":[{"Subnet":"192.168.247.0/24"}]}}]
        used=mod.used_subnets(routes,addresses,networks)
        self.assertTrue(any(net.overlaps(ipaddress.ip_network("10.231.240.0/28"))
                            for net in used))
        self.assertEqual(str(mod.candidate(used)),"172.30.250.0/28")
        used=list(map(ipaddress.ip_network,mod.CANDIDATES))
        with self.assertRaisesRegex(mod.Stop,"NO_SAFE_CANDIDATE"):
            mod.candidate(used)
        for inp in ([{"dst":"not-an-ip"}],["untrusted"]):
            with self.assertRaises(mod.Stop):
                mod.used_subnets(inp,[],[])

    def test_bridge_or_guard_name_conflict_refuses_before_nft_check(self):
        def hijacked(argv,**kw):
            out=result(argv,**kw)
            if tuple(argv)==("ip","-j","link","show"):
                return json.dumps([{"ifname":mod.TEST_BRIDGE}]).encode()
            return out
        with mock.patch.object(mod,"run",side_effect=hijacked), \
             self.assertRaisesRegex(mod.Stop,"PROPOSED_TEST_INTERFACE_OCCUPIED"):
            mod.inspect_candidates()
        def occupied(argv,**kw):
            if tuple(argv)==("nft","-j","list","tables"):
                return json.dumps({"nftables":[{"table":{
                    "family":"inet","name":"homedocker_r23d_guard"}}]}).encode()
            return result(argv,**kw)
        with mock.patch.object(mod,"run",side_effect=occupied), \
             self.assertRaisesRegex(mod.Stop,"PROPOSED_GUARD_TABLE_OCCUPIED"):
            mod.inspect_candidates()

    def test_nft_syntax_failure_never_install_or_emit_rules(self):
        actual=ipaddress.ip_network(mod.CANDIDATES[1])
        renderer=mod.load_module(mod.RENDER,"test_r23d1f_renderer")
        def command(argv,**kwargs):
            if argv[0]=="nft" and argv[1]=="--check":
                raise mod.Stop("HOST_INSPECTION_COMMAND_FAILED")
            self.fail("unexpected command")
        with mock.patch.object(mod,"run",side_effect=command), \
             self.assertRaisesRegex(mod.Stop,"NFT_SYNTAX_CHECK_FAILED"):
            mod.syntactic_check(actual,renderer)

    def test_readonly_cli_succeeds_without_disclosing_subnets_policies_or_tokens(self):
        output=io.StringIO()
        mock_renderer=mod.load_module(mod.RENDER,"test_r23d1f_renderer_report")
        with mock.patch.object(mod,"baseline",return_value=True), \
             mock.patch.object(mod,"inspect_candidates",return_value=(
                 ipaddress.ip_network("10.231.240.16/28"),mock_renderer)), \
             mock.patch.object(mod,"syntactic_check",return_value=True), \
             redirect_stdout(output):
            mod.main()
        result_out=output.getvalue()
        self.assertIn("R23D1F_READONLY_NFT_SYNTAX_PASS",result_out)
        self.assertNotIn("10.231.240.",result_out)
        self.assertNotIn("table inet",result_out)
        self.assertNotIn("X-Emby",result_out)
        self.assertIn('"firewall_effective_egress_isolation_verified": false',result_out)
        self.assertIn('"actual_firewall_table_or_rules_installed": false',result_out)

    def test_runtime_failures_redacted_no_raw_exception(self):
        secret="secret-x-emby-token 192.168.10.9"
        output=io.StringIO()
        with mock.patch.object(mod,"baseline",side_effect=RuntimeError(secret)), \
             redirect_stderr(output), self.assertRaises(SystemExit):
            mod.main()
        self.assertIn("R23D1F_SAFE_DIAGNOSTIC",output.getvalue())
        self.assertNotIn(secret,output.getvalue())

    def test_no_docker_or_nft_mutation_path_in_planner(self):
        source=SRC.read_text()
        forbidden=[
            '"--apply"', '"--confirm"', '"network","create"',
            '"container","run"', '"nft","--file"',
            '"nft","add"', '"nft","-f"', "os.system(", "shell=True"
        ]
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term,source)
        self.assertIn('"nft","--check","--file","-"',source)

if __name__=="__main__":
    unittest.main()
