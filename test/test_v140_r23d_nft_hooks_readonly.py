import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

SOURCE=Path(__file__).resolve().parents[1]/"scripts"/"audit-v140-r23d-nft-hooks-readonly.py"
spec=importlib.util.spec_from_file_location("r23d1d_hooks",SOURCE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NFT={
  "nftables":[
    {"metainfo":{"version":"1.1.0"}},
    {"table":{"family":"ip","name":"PRIVATE_BRIDGE_FROM_HOST","handle":1}},
    {"chain":{"family":"ip","table":"PRIVATE_BRIDGE_FROM_HOST",
              "name":"INPUT","type":"filter","hook":"input","prio":0,
              "policy":"accept","handle":2}},
    {"chain":{"family":"ip","table":"PRIVATE_BRIDGE_FROM_HOST",
              "name":"FORWARD","type":"filter","hook":"forward","prio":"filter",
              "policy":"drop","handle":3}},
    {"chain":{"family":"inet","table":"confidential_table",
              "name":"secret_input","type":"filter","hook":"input","prio":-50,
              "policy":"accept","handle":4}},
    {"chain":{"family":"inet","table":"confidential_table",
              "name":"unused","handle":5}},
    {"rule":{"family":"inet","table":"confidential_table","chain":"secret_input",
             "expr":[{"match":{"left":{"meta":{"key":"iifname"}},
                   "op":"==","right":"secret_interface"}},
                     {"drop":None}],"comment":"token=secret"}},
  ]
}

class HookAuditTests(unittest.TestCase):
    def test_only_aggregate_priorities_never_names_or_rule_bodies(self):
        out=mod.summarize(NFT)
        self.assertEqual(out["status"],"OBSERVED_NOT_EGRESS_QUALIFIED")
        self.assertEqual(out["nft_table_count"],1)
        self.assertEqual(out["base_chains_total"],3)
        self.assertEqual(out["regular_chains_total"],1)
        expected=[
            {"family":"inet","hook":"input","priority":-50,"policy":"accept","chains":1},
            {"family":"ip","hook":"forward","priority":0,"policy":"drop","chains":1},
            {"family":"ip","hook":"input","priority":0,"policy":"accept","chains":1},
        ]
        self.assertEqual(out["base_chain_aggregates"],expected)
        self.assertFalse(out["firewall_effective_egress_isolation_verified"])
        self.assertFalse(out["gateway_user_session_allowed"])
        self.assertEqual(out["docker_firewall_nginx_aio_watch_history_writes"],0)
        dump=json.dumps(out)
        for secret in ("PRIVATE_BRIDGE_FROM_HOST","confidential_table","secret_input",
                       "secret_interface","token=secret","handle"):
            self.assertNotIn(secret,dump)

    def test_unsupported_metadata_is_unknown_not_forged_priority(self):
        copy=json.loads(json.dumps(NFT))
        copy["nftables"][3]["chain"]["prio"]="filter + 150"
        out=mod.summarize(copy)
        self.assertEqual(out["base_chains_with_unrecognized_fields"],1)
        self.assertTrue(any(x["priority"]=="UNKNOWN" for x in out["base_chain_aggregates"]))
        self.assertEqual(mod.priority("mangle"),-150)
        self.assertEqual(mod.priority("security"),50)
        self.assertEqual(mod.priority(150),150)
        self.assertEqual(mod.priority("totally-custom"),"UNKNOWN")

    def test_invalid_json_and_huge_base_chain_count_fail_closed(self):
        for obj in (None,[],{},{"nftables":{}},
                    {"nftables":[{"chain":42}]}):
            with self.assertRaises(mod.Stop):
                mod.summarize(obj)
        big={"nftables":[NFT["nftables"][2]]*(mod.MAX_BASE_CHAINS+1)}
        with self.assertRaisesRegex(mod.Stop,"NFT_BASE_CHAIN_LIMIT_EXCEEDED"):
            mod.summarize(big)

    def test_cli_stops_safely_without_exposing_raw_command_details(self):
        secret="192.0.2.42 eth99 username secret session"
        buffer=io.StringIO()
        with mock.patch.object(mod,"baseline",return_value=True), \
             mock.patch.object(mod,"read_nft_ruleset",side_effect=RuntimeError(secret)), \
             redirect_stderr(buffer),self.assertRaises(SystemExit) as err:
            mod.main()
        self.assertEqual(err.exception.code,1)
        self.assertNotIn(secret,buffer.getvalue())
        self.assertIn("R23D1D_SAFE_DIAGNOSTIC",buffer.getvalue())

    def test_read_only_guard_mandatory_before_nft_query(self):
        with mock.patch.object(mod,"baseline",side_effect=mod.Stop("R23D1C_HOST_BASELINE_FAILED")), \
             mock.patch.object(mod,"read_nft_ruleset",side_effect=AssertionError("SHOULD NOT RUN")), \
             redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            mod.main()

    def test_read_nft_ruleset_command_no_write_or_rule_exposure(self):
        sample=json.dumps(NFT).encode()
        seen=[]
        def run(args,**kwargs):
            seen.append(args)
            return mock.Mock(returncode=0,stdout=sample,stderr=b"")
        with mock.patch.object(mod.shutil,"which",return_value="/usr/sbin/nft"), \
             mock.patch.object(mod.subprocess,"run",side_effect=run):
            self.assertEqual(mod.read_nft_ruleset(),NFT)
        self.assertEqual(seen,[["/usr/sbin/nft","-j","list","ruleset"]])

if __name__=="__main__":
    unittest.main()
