"""Exact retired R23A cleanup safety and mock-only regressions."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"retire-v140-r23a-stopped-canary.py"
spec=importlib.util.spec_from_file_location("r23a_retirement",SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def fixture():
    def c(name,image,status="running",restart="no"):
        return {
            "Id":name+"-immutable-identity",
            "Name":"/"+name,
            "Image":image,
            "State":{"Status":status,"Running":status=="running",
                     "Health":{"Status":"healthy"} if status=="running" else {}},
            "Config":{"Image":m.OLD_TAG if name==m.OLD else "other","Labels":{}},
            "HostConfig":{"RestartPolicy":{"Name":restart},
                          "NetworkMode":m.OLD_NETWORK if name==m.OLD else "elsewhere",
                          "PortBindings":m.EXPECTED_BINDINGS if name==m.OLD else {},
                          "AutoRemove":False},
            "NetworkSettings":{"Networks":{}},
            "Mounts":[],
        }
    c0=c(m.OLD,m.CANARY_ID,"exited")
    rb=c(m.R23B,m.CANARY_ID)
    resume=c(m.RESUME,"sha256:"+"e"*64)
    rc6=c(m.BRIDGE,m.BRIDGE_ID,restart="unless-stopped")
    net={"Name":m.OLD_NETWORK,"Id":"f"*64,"Driver":"bridge","Containers":{}}
    values={m.OLD:c0,m.R23B:rb,m.RESUME:resume,m.BRIDGE:rc6}
    ids=["a"*64,"b"*64,"c"*64,"d"*64]
    return values,net,ids

class ExactRetirement(unittest.TestCase):
    def setUp(self):
        self.values,self.network,self.ids=fixture()
        self.calls=[]
    def fake_single(self,kind,name):
        if kind=="network": return self.network
        return self.values[name]
    def fake_cmd(self,*a):
        self.calls.append(a)
        if a==("docker","ps","-aq","--no-trunc"):
            return "\n".join(self.ids)
        if a[:3]==("docker","container","inspect"):
            return json.dumps(list(self.values.values()))
        return ""
    def check(self):
        with mock.patch.object(m,"single",side_effect=self.fake_single),\
             mock.patch.object(m,"cmd",side_effect=self.fake_cmd):
            return m.check()
    def test_expected_stopped_no_mount_canary_is_eligible(self):
        result=self.check()
        self.assertEqual(result["retired_network_id"],"f"*64)
        self.assertNotIn(("docker","container","rm",m.OLD),self.calls)
    def test_active_canary_rejected(self):
        self.values[m.OLD]["State"]={"Status":"running","Running":True}
        with self.assertRaisesRegex(m.Stop,"OWNER_IDENTITY_CHANGED"):
            self.check()
    def test_writable_or_readonly_mount_rejected(self):
        self.values[m.OLD]["Mounts"]=[{"Type":"bind","RW":False}]
        with self.assertRaises(m.Stop):self.check()
    def test_any_network_peer_rejected(self):
        self.network["Containers"]={"other":{"Name":"other"}}
        with self.assertRaises(m.Stop):self.check()
    def test_other_stopped_container_network_reference_rejected(self):
        extra=fixture()[0][m.OLD].copy()
        extra["Name"]="/another-container"
        extra["HostConfig"]=dict(extra["HostConfig"])
        extra["HostConfig"]["NetworkMode"]=m.OLD_NETWORK
        def listing(*a):
            if a==("docker","ps","-aq","--no-trunc"):
                return "\n".join(self.ids+["e"*64])
            if a[:3]==("docker","container","inspect"):
                return json.dumps(list(self.values.values())+[extra])
            return ""
        with mock.patch.object(m,"single",side_effect=self.fake_single),\
             mock.patch.object(m,"cmd",side_effect=listing):
            with self.assertRaisesRegex(m.Stop,"NETWORK_REFERENCED_BY_OTHER_CONTAINER"):
                m.check()
    def test_production_rc6_image_change_rejected(self):
        self.values[m.BRIDGE]["Image"]="sha256:"+"b"*64
        with self.assertRaisesRegex(m.Stop,"PRODUCTION_RC6_BASELINE_CHANGED"):
            self.check()
    def test_r23b_image_sharing_required(self):
        self.values[m.R23B]["Image"]="sha256:"+"b"*64
        with self.assertRaisesRegex(m.Stop,"R23B_CANARY_BASELINE_CHANGED"):
            self.check()
    def test_no_delete_on_default_audit(self):
        with mock.patch.object(m,"check",return_value={
             "retired_container_id":"c", "retired_network_id":"n",
             "r23b_id":"r","resume_id":"q","production_id":"p"}),\
             mock.patch.object(m,"cmd",side_effect=AssertionError("SHOULD_NOT_MUTATE")),\
             mock.patch("sys.argv",["cleanup"]):
            m.main()
    def test_apply_without_exact_confirmation_stops(self):
        with mock.patch.object(m,"check",side_effect=AssertionError("PRECHECK_NOT_EXPECTED")),\
             mock.patch("sys.argv",["cleanup","--apply"]):
            with self.assertRaisesRegex(m.Stop,"EXPLICIT_APPLY_AND_CONFIRM"):
                m.main()
    def test_command_allowlist_rejects_all_generic_prune(self):
        for args in (("docker","system","prune","-af"),
                     ("docker","volume","rm","production"),
                     ("docker","network","rm","media_core_net"),
                     ("docker","container","rm",m.R23B),
                     ("docker","stop",m.OLD)):
            with self.assertRaises(m.Stop):m.cmd(*args)

if __name__=="__main__":
    unittest.main()
