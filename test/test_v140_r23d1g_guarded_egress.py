import importlib.util
import io
import json
import ipaddress
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest import mock

SRC=Path(__file__).resolve().parents[1]/"scripts"/"probe-v140-r23d1g-guarded-egress.py"
spec=importlib.util.spec_from_file_location("r23d1g_guard",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Plan:
    TEST_NET="homedocker_v140_r23d1f_trial"
    TEST_CONTAINER="homedocker-v140-r23d1f-trial"
    TEST_BRIDGE="br-r23dtrial"
    net=ipaddress.ip_network("10.231.240.16/28")
    gateway="10.231.240.17"
    canary="10.231.240.18"
    image_id="sha256:"+"a"*64
    renderer=type("Renderer",(),{"TABLE":"homedocker_r23d_guard"})

RUN_ID="abcdef0123456789abcdef01"

def valid_network():
    return [{
       "Name":Plan.TEST_NET,
       "Driver":"bridge","Internal":False,"EnableIPv6":False,
       "Options":{
          "com.docker.network.bridge.name":Plan.TEST_BRIDGE,
          "com.docker.network.bridge.enable_ip_masquerade":"false",
       },
       "Labels":m.owner_tag(RUN_ID),
       "IPAM":{"Config":[{"Subnet":str(Plan.net),"Gateway":Plan.gateway}]},
       "Containers":{},
    }]

def valid_container():
    return [{
       "Name":"/"+Plan.TEST_CONTAINER,
       "Image":Plan.image_id,
       "Config":{"Labels":m.owner_tag(RUN_ID)},
       "NetworkSettings":{"Networks":{
          Plan.TEST_NET:{"IPAddress":Plan.canary}}},
       "HostConfig":{"NetworkMode":Plan.TEST_NET,
                     "ReadonlyRootfs":True,"Privileged":False,
                     "CapAdd":None},
       "Mounts":[],
    }]

GOOD_TCP={
  "contract":"R23D1G_TCP_ONLY",
  "allowed_relay":True,
  "denied_open_control":False,
  "host_ports":[False,False,False,False,False],
  "forward_testnet":[False,False,False],
  "auth_or_payload_sent":False,
}

class GuardedEgressTests(unittest.TestCase):
    def test_network_exact_private_bridge_ownership_and_ipv6_disabled(self):
        self.assertTrue(m.assert_network(valid_network(),Plan(),RUN_ID))
        for mutation in (
            ("EnableIPv6",True),
            ("Internal",True),
            ("Name","production-bridge"),
            ("Driver","macvlan"),
        ):
            case=valid_network()
            case[0][mutation[0]]=mutation[1]
            with self.subTest(mutation=mutation),self.assertRaises(m.Stop):
                m.assert_network(case,Plan(),RUN_ID)
        bad=valid_network()
        bad[0]["Labels"]["homedocker.probe_run"]="someone_else"
        with self.assertRaisesRegex(m.Stop,"NETWORK_OWNERSHIP"):
            m.assert_network(bad,Plan(),RUN_ID)
        bad=valid_network()
        bad[0]["Options"]["com.docker.network.bridge.name"]="docker0"
        with self.assertRaises(m.Stop):
            m.assert_network(bad,Plan(),RUN_ID)

    def test_canary_cleanup_refuses_foreign_or_privileged_container(self):
        self.assertTrue(m.assert_container(valid_container(),Plan(),RUN_ID,Plan.image_id))
        for attr,value in (
            ("ReadonlyRootfs",False),
            ("Privileged",True),
            ("CapAdd",["NET_ADMIN"]),
        ):
            bad=valid_container()
            bad[0]["HostConfig"][attr]=value
            with self.subTest(attr=attr),self.assertRaises(m.Stop):
                m.assert_container(bad,Plan(),RUN_ID,Plan.image_id)
        bad=valid_container()
        bad[0]["Config"]["Labels"]={"homedocker.phase":"foreign"}
        with self.assertRaises(m.Stop):
            m.assert_container(bad,Plan(),RUN_ID,Plan.image_id)
        bad=valid_container()
        bad[0]["NetworkSettings"]["Networks"]["media_core_net"]={}
        with self.assertRaises(m.Stop):
            m.assert_container(bad,Plan(),RUN_ID,Plan.image_id)
        bad=valid_container()
        bad[0]["Mounts"]=[{"Type":"bind"}]
        with self.assertRaises(m.Stop):
            m.assert_container(bad,Plan(),RUN_ID,Plan.image_id)

    def test_tcp_fixture_requires_allowed_and_denied_open_controls(self):
        self.assertEqual(m.parse_tcp(json.dumps(GOOD_TCP)), GOOD_TCP)
        for bad in (
            {**GOOD_TCP,"contract":"crosswatch-write"},
            {**GOOD_TCP,"allowed_relay":"true"},
            {**GOOD_TCP,"denied_open_control":None},
            {**GOOD_TCP,"host_ports":[False]*4},
            {**GOOD_TCP,"host_ports":["False"]*5},
            {**GOOD_TCP,"forward_testnet":[False]*2},
            {**GOOD_TCP,"auth_or_payload_sent":True},
            [],
        ):
            with self.subTest(bad=bad),self.assertRaises(m.Stop):
                m.parse_tcp(json.dumps(bad))

    def test_cleanup_exact_order_container_network_then_only_unchanged_table(self):
        names={"container":True,"network":True}
        actions=[]
        def absent(kind,name):
            return not names[kind]
        def fake_command(argv,**kw):
            actions.append(argv)
            if argv[:2]==["docker","rm"]:
                names["container"]=False
            if argv[:3]==["docker","network","rm"]:
                names["network"]=False
            return b""
        snapshot=b'{"nftables":[{"table":{"name":"homedocker_r23d_guard"}}]}'
        nft=[snapshot,snapshot,None]
        with mock.patch.object(m,"absence",side_effect=absent), \
             mock.patch.object(m,"owned_container",return_value=valid_container()[0]), \
             mock.patch.object(m,"owned_network",return_value=valid_network()[0]), \
             mock.patch.object(m,"table_snapshot",side_effect=lambda renderer:nft.pop(0)), \
             mock.patch.object(m,"command",side_effect=fake_command):
            self.assertTrue(m.cleanup(Plan(),RUN_ID,snapshot))
        self.assertEqual(actions,[
          ["docker","rm","-f",Plan.TEST_CONTAINER],
          ["docker","network","rm",Plan.TEST_NET],
          ["nft","delete","table","inet",Plan.renderer.TABLE],
        ])

    def test_cleanup_keeps_firewall_if_canary_or_network_untrusted(self):
        calls=[]
        with mock.patch.object(m,"absence",return_value=False), \
             mock.patch.object(m,"owned_container",side_effect=m.Stop("foreign")), \
             mock.patch.object(m,"command",side_effect=lambda *args,**kw:calls.append(args)):
            with self.assertRaises(m.Stop):
                m.cleanup(Plan(),RUN_ID,b"snapshot")
        self.assertFalse(calls)
        with mock.patch.object(m,"absence",side_effect=lambda kind,name:kind=="container"), \
             mock.patch.object(m,"owned_network",return_value={
                 **valid_network()[0], "Containers":{"other":{}}}), \
             mock.patch.object(m,"command",side_effect=lambda *args,**kw:calls.append(args)):
            with self.assertRaisesRegex(m.Stop,"FOREIGN_NETWORK_PEERS"):
                m.cleanup(Plan(),RUN_ID,b"snapshot")
        self.assertFalse(calls)

    def test_cleanup_detects_changed_nft_table_and_does_not_delete_foreign_rules(self):
        seen=[]
        with mock.patch.object(m,"absence",return_value=True), \
             mock.patch.object(m,"table_snapshot",return_value=b"modified"), \
             mock.patch.object(m,"command",side_effect=lambda *args,**kw:seen.append(args)):
            with self.assertRaisesRegex(m.Stop,"FIREWALL_TABLE_OWNERSHIP_CHANGED"):
                m.cleanup(Plan(),RUN_ID,b"original")
        self.assertEqual(seen,[])

    def test_dry_run_can_never_mutate_docker_or_firewall(self):
        output=io.StringIO()
        with mock.patch("sys.argv",["prog"]), \
             mock.patch.object(m,"preflight",return_value=Plan()), \
             mock.patch.object(m,"apply",side_effect=AssertionError("MUTATION")), \
             redirect_stdout(output):
            m.main()
        self.assertIn("R23D1G_DRY_RUN_PASS",output.getvalue())
        self.assertIn('"docker_and_firewall_writes": 0',output.getvalue())
        self.assertIn('"jellyfin_session_allowed": false',output.getvalue())

    def test_apply_requires_exact_confirmation(self):
        err=io.StringIO()
        with mock.patch("sys.argv",["prog","--apply"]), \
             mock.patch.object(m,"preflight",return_value=Plan()), \
             mock.patch.object(m,"apply",side_effect=AssertionError("MUTATION")), \
             redirect_stderr(err),self.assertRaises(SystemExit) as aborted:
            m.main()
        self.assertEqual(aborted.exception.code,1)
        self.assertIn("EXPLICIT_CONFIRMATION_REQUIRED",err.getvalue())

    def test_source_requires_install_guard_before_any_container_and_strips_secrets(self):
        code=SRC.read_text()
        self.assertLess(code.index('command(["nft","--file","-"]'),
                        code.index('raw=command(["docker","run"'))
        for forbidden in ("X-Emby-Token","AuthenticateByName","trakt.tv",
                          "docker prune","nft flush ruleset","iptables -F",
                          "--network=host","media_core_net"):
            self.assertNotIn(forbidden,m.NODE)
        self.assertNotIn("POST",m.NODE)
        self.assertEqual(m.RELAY_PORT,17420)
        self.assertEqual(m.DENIED_CONTROL_PORT,17421)

if __name__=="__main__":
    unittest.main()
