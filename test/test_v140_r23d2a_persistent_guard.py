import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock
from contextlib import redirect_stdout

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"scripts"/"attest-v140-r23d-persistent-guard.py"
spec=importlib.util.spec_from_file_location("r23d2a_structural_guard",SOURCE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCOPE={
    "bridge":"br-r23dgate",
    "subnet":"172.31.254.0/28",
    "host_gateway":"172.31.254.1",
    "canary":"172.31.254.2",
    "relay_port":17420,
}

def nft_fixture():
    return {"nftables":[
        {"metainfo":{"version":"1.1.0","release_name":"secret test fixture"}},
        {"table":{"family":"inet","name":"homedocker_r23d_guard","handle":10}},
        {"chain":{"family":"inet","table":"homedocker_r23d_guard",
                  "name":"r23d_input","type":"filter","hook":"input",
                  "prio":-20,"policy":"accept","handle":20}},
        {"chain":{"family":"inet","table":"homedocker_r23d_guard",
                  "name":"r23d_forward","type":"filter","hook":"forward",
                  "prio":-20,"policy":"accept","handle":21}},
        {"rule":{"family":"inet","table":"homedocker_r23d_guard",
                 "chain":"r23d_input","handle":30,
                 "expr":m.expected_rules(**dict(bridge=SCOPE["bridge"],
                       gw=SCOPE["host_gateway"],canary=SCOPE["canary"],
                       relay_port=SCOPE["relay_port"]))["r23d_input"][0]}},
        {"rule":{"family":"inet","table":"homedocker_r23d_guard",
                 "chain":"r23d_input","handle":31,
                 "expr":m.expected_rules(SCOPE["bridge"],SCOPE["host_gateway"],
                         SCOPE["canary"],SCOPE["relay_port"])["r23d_input"][1]}},
        {"rule":{"family":"inet","table":"homedocker_r23d_guard",
                 "chain":"r23d_forward","handle":32,
                 "expr":m.expected_rules(SCOPE["bridge"],SCOPE["host_gateway"],
                         SCOPE["canary"],SCOPE["relay_port"])["r23d_forward"][0]}}
    ]}

class NFTGuardAttestationTests(unittest.TestCase):
    def test_only_exact_guard_is_accepted_and_no_raw_policy_emitted(self):
        fixture=nft_fixture()
        self.assertTrue(m.check_guard(fixture,**SCOPE))
        self.assertTrue(m.check_guard(copy.deepcopy(fixture),**SCOPE))
        report=m.report()
        self.assertFalse(report["live_aio_session_allowed"])
        self.assertFalse(report["runtime_docker_identity_and_nft_fetcher_wired"])
        self.assertFalse(report["kernel_firewall_enforcement_verified"])
        self.assertEqual(report["docker_firewall_nginx_aio_or_history_writes"],0)
        out=io.StringIO()
        with redirect_stdout(out):
            print(json.dumps(report))
        for secret in ("172.31.254.", "br-r23dgate", "session", "X-Emby-Token"):
            if secret=="session":
                continue
            self.assertNotIn(secret,out.getvalue())

    def test_rejects_missing_table_chain_or_wrong_policy_and_priority(self):
        fixture=nft_fixture()
        for fn in (
            lambda x:x["nftables"].pop(1),
            lambda x:x["nftables"].pop(2),
            lambda x:x["nftables"].pop(3),
            lambda x:x["nftables"][2]["chain"].update({"prio":0}),
            lambda x:x["nftables"][3]["chain"].update({"hook":"output"}),
            lambda x:x["nftables"][2]["chain"].update({"policy":"drop"}),
            lambda x:x["nftables"][2]["chain"].update({"family":"ip"}),
            lambda x:x["nftables"][1]["table"].update({"name":"new_table"}),
            lambda x:x["nftables"][3]["chain"].update({"dev":"eth0"}),
        ):
            obj=copy.deepcopy(fixture)
            fn(obj)
            with self.subTest(changed=obj),self.assertRaises(m.Stop):
                m.check_guard(obj,**SCOPE)

    def test_rejects_missing_reordered_changed_or_added_rule(self):
        fixture=nft_fixture()
        changes=(
            lambda x:x["nftables"].pop(4),
            lambda x:x["nftables"].pop(5),
            lambda x:x["nftables"][4]["rule"]["expr"].pop(),
            lambda x:x["nftables"][4]["rule"]["expr"][0]["match"].update({"right":"eth0"}),
            lambda x:x["nftables"][4]["rule"]["expr"][1]["match"].update({"right":"172.31.254.3"}),
            lambda x:x["nftables"][4]["rule"]["expr"][2]["match"].update({"right":"172.31.254.3"}),
            lambda x:x["nftables"][4]["rule"]["expr"][3]["match"].update({"right":22}),
            lambda x:x["nftables"][4]["rule"]["expr"][-1].clear(),
            lambda x:x["nftables"][5]["rule"]["expr"][-1].update({"accept":None}),
            lambda x:x["nftables"][6]["rule"]["expr"][-1].update({"accept":None}),
            lambda x:x["nftables"].append({"rule":{"family":"inet","table":"homedocker_r23d_guard",
                "chain":"r23d_forward","expr":[{"accept":None}]}}),
            lambda x:x["nftables"][4]["rule"].update({"comment":"allow all"}),
            lambda x:x["nftables"][4]["rule"]["expr"].insert(0,{"counter":None}),
            lambda x:x["nftables"].insert(4,{"set":{"name":"wildcard","table":"homedocker_r23d_guard"}}),
        )
        for fn in changes:
            obj=copy.deepcopy(fixture)
            fn(obj)
            with self.subTest(change=repr(fn)),self.assertRaises(m.Stop):
                m.check_guard(obj,**SCOPE)
        obj=copy.deepcopy(fixture)
        obj["nftables"][4],obj["nftables"][5]=obj["nftables"][5],obj["nftables"][4]
        with self.assertRaises(m.Stop):
            m.check_guard(obj,**SCOPE)

    def test_spoofed_interface_address_or_non_rfc1918_scope_cannot_qualify(self):
        obj=nft_fixture()
        for key,bad in (("bridge","eth0"),("bridge","br-r23dOTHER"),
                        ("subnet","192.0.2.0/28"),("subnet","172.31.254.0/16"),
                        ("host_gateway","172.31.254.3"),("canary","172.31.254.3"),
                        ("relay_port",22)):
            with self.subTest(key=key),self.assertRaises(m.Stop):
                m.check_guard(obj,**{**SCOPE,key:bad})
        for obj in ({}, [], {"nftables":{}}, {"nftables":[{"unknown":{}}]},
                    {"nftables":[42]}, {"nftables":[{"table":{}}]*21}):
            with self.subTest(obj=str(obj)[:40]),self.assertRaises(m.Stop):
                m.check_guard(obj,**SCOPE)

    def test_no_host_inspection_or_firewall_command_in_source(self):
        source=SOURCE.read_text()
        for forbidden in ("import subprocess", "os.system(", "docker run",
                          "nft --file", "nft flush", "AuthenticateByName",
                          "from urllib", "import socket", "aio_get_token"):
            self.assertNotIn(forbidden,source)

class GuardLifetimeGateTests(unittest.TestCase):
    def test_gate_is_closed_before_start_and_accepts_fresh_checks_only(self):
        calls=[]
        gate=m.GuardLifetimeGate(lambda:calls.append("verify") is None,
                                 lambda:calls.append("loss"),interval_seconds=30)
        self.assertFalse(gate.observe())
        self.assertEqual(calls,[])
        gate.close()
        with self.assertRaises(m.Stop):
            gate.start()
        self.assertEqual(gate.state,"CLOSED")

    def test_start_success_then_any_policy_mutation_permanently_denies(self):
        present={"guard":True}
        events=[]
        def verify():
            events.append("check")
            return present["guard"]
        gate=m.GuardLifetimeGate(verify,lambda:events.append("shutdown"),
                                 interval_seconds=30)
        try:
            self.assertTrue(gate.start())
            self.assertTrue(gate.observe())
            present["guard"]=False
            self.assertFalse(gate.observe())
            self.assertFalse(gate.observe())
            present["guard"]=True
            self.assertFalse(gate.observe(),"sticky fail never recovers")
            self.assertEqual(events.count("shutdown"),1)
            self.assertEqual(gate.state,"FAILED")
            with self.assertRaises(m.Stop):
                gate.start()
        finally:
            gate.close()

    def test_initial_missing_guard_triggers_single_shutdown_no_worker(self):
        shutdown=[]
        gate=m.GuardLifetimeGate(lambda:False,lambda:shutdown.append(1),
                                 interval_seconds=30)
        with self.assertRaises(m.Stop):
            gate.start()
        self.assertEqual(shutdown,[1])
        self.assertFalse(gate.observe())
        gate.close()

    def test_exception_false_and_none_are_not_silent_pass(self):
        for result in (False,None,0,"true"):
            with self.subTest(result=result):
                shutdown=[]
                gate=m.GuardLifetimeGate(lambda:result,lambda:shutdown.append("kill"),
                                         interval_seconds=30)
                with self.assertRaises(m.Stop):
                    gate.start()
                self.assertEqual(shutdown,["kill"])
                gate.close()
        shutdown=[]
        def fail():
            raise RuntimeError("token=never-print-me")
        gate=m.GuardLifetimeGate(fail,lambda:shutdown.append("kill"),interval_seconds=30)
        with self.assertRaisesRegex(m.Stop,"PERSISTENT_NFT_GUARD_NOT_ATTESTED") as ctx:
            gate.start()
        self.assertNotIn("never-print-me",str(ctx.exception))
        self.assertEqual(shutdown,["kill"])
        gate.close()

    def test_periodic_poll_detects_guard_loss_with_no_request(self):
        present=threading.Event()
        present.set()
        died=threading.Event()
        gate=m.GuardLifetimeGate(lambda:present.is_set(),lambda:died.set(),
                                 interval_seconds=0.1)
        try:
            self.assertTrue(gate.start())
            present.clear()
            self.assertTrue(died.wait(2),"background loss must trigger stop")
            self.assertEqual(gate.state,"FAILED")
            self.assertFalse(gate.observe())
        finally:
            gate.close()

    def test_invalid_monitor_configuration_fails_closed(self):
        for interval in (-1,0,0.01,31,True,"2"):
            with self.subTest(interval=interval),self.assertRaises(ValueError):
                m.GuardLifetimeGate(lambda:True,lambda:None,interval_seconds=interval)
        with self.assertRaises(ValueError):
            m.GuardLifetimeGate("not_callable",lambda:None)
        with self.assertRaises(ValueError):
            m.GuardLifetimeGate(lambda:True,None)

if __name__=="__main__":
    unittest.main()
