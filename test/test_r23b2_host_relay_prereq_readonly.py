"""Static/mocked safety tests for read-only HomeDocker relay readiness audit."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

PATH=Path(__file__).resolve().parents[1]/"scripts/audit-r23b2-host-relay-prereqs.py"
spec=importlib.util.spec_from_file_location("b2audit",PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
C={
 "State":{"Running":True,"Pid":12345},
 "Config":{"Labels":{"homedocker.phase":"r23b1"},
           "Env":["JELLYFIN_GATEWAY_UPSTREAM_URL=http://127.0.0.1:9999/jellyfin",
                  "TRAKT_CLIENT_SECRET=TOP_PRIVATE"]},
 "HostConfig":{"ReadonlyRootfs":True,"Privileged":False,
               "RestartPolicy":{"Name":"no"},"UsernsMode":"",
               "PortBindings":{"7000/tcp":[{"HostIp":"127.0.0.1","HostPort":"17041"}],
                               "7101/tcp":[{"HostIp":"127.0.0.1","HostPort":"17141"}]}},
 "NetworkSettings":{"Networks":{"homedocker_v140_r23b_local":{}}},
}
N={"Internal":False,"Options":{"com.docker.network.bridge.enable_ip_masquerade":"false"}}
class Preflight(unittest.TestCase):
    def run_mock(self,c=C,n=N,image=True,dir_exists=False,port="",root=0):
        def docker(*args):
            if args[:2]==("container","inspect"):return json.dumps(c)
            if args[:2]==("network","inspect"):return json.dumps(n)
            if args[:2]==("image","inspect"):return "{}" if image else None
            raise AssertionError("unexpected Docker mutation")
        output=io.StringIO()
        with (mock.patch.object(mod,"docker",side_effect=docker),
              mock.patch.object(mod,"see",return_value=port),
              mock.patch.object(mod.os,"geteuid",return_value=root),
              mock.patch.object(mod.os.path,"lexists",return_value=dir_exists),
              mock.patch.object(mod.pwd,"getpwnam",return_value=SimpleNamespace(pw_uid=65534)),
              contextlib.redirect_stdout(output)):
            rc=mod.main()
        return rc,json.loads(output.getvalue())
    def test_green_only_if_precisely_owned_resources(self):
        rc,r=self.run_mock()
        self.assertEqual(rc,0)
        self.assertTrue(r["trial_environment_ready"])
        self.assertEqual(r["host_resources_modified"],0)
        self.assertFalse(r["host_secrets_or_docker_env_displayed"])
        self.assertNotIn("TOP_PRIVATE",json.dumps(r))
        self.assertEqual(r["python_312_alpine_already_local"],True)
    def test_missing_image_stops_without_pulling(self):
        rc,r=self.run_mock(image=False)
        self.assertEqual(rc,4)
        self.assertFalse(r["trial_environment_ready"])
        self.assertFalse(r["python_312_alpine_already_local"])
    def test_occupied_port_stops(self):
        rc,r=self.run_mock(port="LISTEN 0 100 127.0.0.1:9999")
        self.assertEqual(rc,4)
        self.assertFalse(r["netns_port9999_free"])
    def test_no_root_access_cannot_guess_port_is_free(self):
        rc,r=self.run_mock(root=1000)
        self.assertEqual(rc,4)
        self.assertFalse(r["netns_port9999_free"])
    def test_socket_collision_stops(self):
        rc,r=self.run_mock(dir_exists=True)
        self.assertEqual(rc,4)
        self.assertFalse(r["private_socket_path_available"])
    def test_network_drift_stops(self):
        c={**C,"NetworkSettings":{"Networks":{"media_core_net":{}}}}
        rc,r=self.run_mock(c=c)
        self.assertEqual(rc,4)
        self.assertFalse(r["canary_only_private_network"])
    def test_wrong_upstream_never_prints_env_or_passes(self):
        c={**C,"Config":{**C["Config"],"Env":["TRAKT_CLIENT_SECRET=TOP_PRIVATE"]}}
        rc,r=self.run_mock(c=c)
        self.assertEqual(rc,4)
        self.assertFalse(r["canary_image_and_config_unchanged"])
        self.assertNotIn("TOP_PRIVATE",json.dumps(r))
    def test_no_broad_host_mutators(self):
        s=PATH.read_text()
        for dangerous in ("docker run","docker rm","docker pull","docker network connect",
                          "systemctl restart","nft flush","subprocess.Popen(",
                          "nginx -s","sqlite3.connect("):
            self.assertNotIn(dangerous,s)
if __name__=="__main__":unittest.main()
