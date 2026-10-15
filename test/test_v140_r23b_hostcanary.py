import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-v140-r23b-hostcanary.py"
spec=importlib.util.spec_from_file_location("r23b_test_module",SCRIPT)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHAID="sha256:"+"a"*64

def meta(name, image=None, network=None, running=True):
    return {
        "Name":"/"+name,
        "State":{"Running":running},
        "Config":{"Image":image or "test"},
        "Image":SHAID,
        "HostConfig":{
            "ReadonlyRootfs":True,
            "PortBindings":{
                "7000/tcp":[{"HostIp":"127.0.0.1","HostPort":"17041"}],
                "7101/tcp":[{"HostIp":"127.0.0.1","HostPort":"17141"}]
            },
        },
        "Mounts":[{"Type":"tmpfs","Destination":"/app/data"}],
        "NetworkSettings":{"Networks":{network or mod.ORIGINAL_NETWORK:{}}},
    }

class R23BTests(unittest.TestCase):
    def test_fake_credentials_and_no_live_upstream(self):
        values=mod.env()
        self.assertEqual(values["JELLYFIN_GATEWAY_ENABLED"],"true")
        self.assertEqual(values["JELLYFIN_GATEWAY_UPSTREAM_URL"],"http://127.0.0.1:9999/jellyfin")
        self.assertEqual(values["JELLYFIN_GATEWAY_LEGACY_IMPORT"],"off")
        self.assertEqual(values["AIO_RECONCILER_MODE"],"off")
        self.assertEqual(values["JELLYFIN_GATEWAY_RESUME_PASSTHROUGH"],"false")
        self.assertNotEqual(mod.env()["BRIDGE_SECRET_KEY"],values["BRIDGE_SECRET_KEY"])
        self.assertNotIn("TRAKT_ACCESS_TOKEN",values)

    def test_selected_original_ownership_and_no_real_volume(self):
        original=meta(mod.ORIGINAL,mod.EXPECTED_BASE_IMAGE)
        self.assertTrue(mod.check_ports(original))
        original["HostConfig"]["PortBindings"]["7000/tcp"][0]["HostIp"]="0.0.0.0"
        self.assertFalse(mod.check_ports(original))

    def test_apply_stops_only_offline_original_and_retains_as_rollback(self):
        commands=[]
        def execute(*args,**kw):
            commands.append(args)
            if args[:3]==("docker","network","inspect"):
                return json.dumps([{"Internal":False,"Options":{
                    "com.docker.network.bridge.enable_ip_masquerade":"false"
                }}])
            return "ok"
        def inspect(name):
            if name==mod.NEW: return meta(mod.NEW,network=mod.NETWORK)
            return meta(mod.ORIGINAL,mod.EXPECTED_BASE_IMAGE,
                        running=False)
        with mock.patch.object(mod,"execute",side_effect=execute), \
             mock.patch.object(mod,"inspect",side_effect=inspect), \
             mock.patch.object(mod,"health",return_value=200):
            result=mod.apply({"image_id":SHAID})
        self.assertEqual(result["status"],"LOCALHOST_TWO_LISTENERS_PASS")
        self.assertTrue(result["old_r23a_stopped_retained_for_rollback"])
        self.assertFalse(result["real_aio_or_trakt_access_qualified"])
        self.assertEqual(len(commands),5)
        self.assertEqual(commands[0][:3],("docker","network","create"))
        self.assertNotIn("--internal",commands[0])
        self.assertIn("com.docker.network.bridge.enable_ip_masquerade=false",commands[0])
        self.assertEqual(commands[2],("docker","stop",mod.ORIGINAL))
        running=commands[3]
        self.assertEqual(running[:3],("docker","run","-d"))
        self.assertEqual(running[-1],SHAID)
        self.assertEqual(running[running.index("--network")+1],mod.NETWORK)
        self.assertIn("--read-only",running)
        self.assertIn("--cap-drop",running)
        self.assertIn("--tmpfs",running)
        self.assertNotIn("--mount",running)
        self.assertIn("127.0.0.1:17041:7000",running)
        self.assertIn("127.0.0.1:17141:7101",running)
        self.assertNotIn("media_core_net",str(running))
        self.assertNotIn("/var/lib/docker/volumes",str(running))
        self.assertFalse(any(name in str(commands) for name in
                             ("docker compose","docker network connect","docker pull")))

    def test_failure_after_new_run_attempt_restores_original(self):
        commands=[]
        def execute(*args,**kw):
            commands.append(args)
            if args[:3]==("docker","network","inspect"):
                return json.dumps([{"Internal":False,"Options":{
                    "com.docker.network.bridge.enable_ip_masquerade":"false"}}])
            if args[:3]==("docker","run","-d"):
                raise mod.Stop("start failed")
            return "ok"
        def run(args,**kw):
            commands.append(tuple(args))
            return subprocess.CompletedProcess(args,0,"","")
        with mock.patch.object(mod,"execute",side_effect=execute), \
             mock.patch.object(mod.subprocess,"run",side_effect=run):
            with self.assertRaises(mod.Stop):
                mod.apply({"image_id":SHAID})
        self.assertIn(("docker","start",mod.ORIGINAL),commands)
        self.assertIn(("docker","network","rm",mod.NETWORK),commands)
        self.assertTrue(all("trakt-bridge" not in x[2:] or
                            x[2:] == (mod.ORIGINAL,) for x in commands
                            if x[:2] == ("docker","stop")))

    def test_preflight_refuses_unsafe_old_container(self):
        old=meta(mod.ORIGINAL,mod.EXPECTED_BASE_IMAGE)
        old["Mounts"].append({"Type":"volume","Source":"/sensitive","Destination":"/app/x"})
        def inspect(name):
            if name==mod.ORIGINAL: return old
            raise AssertionError("other inspect should not be called")
        with mock.patch.object(mod.os,"geteuid",return_value=0), \
             mock.patch.object(mod,"inspect",side_effect=inspect):
            with self.assertRaisesRegex(mod.Stop,"persistent source"):
                mod.state()

if __name__=="__main__":
    unittest.main()
