"""R23B2 one-shot orchestration safety tests; no root, Docker, network or secrets."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from unittest import mock
import subprocess
import sys
import unittest

HERE=Path(__file__).resolve().parents[1]
PATH=HERE/"scripts/trial-r23b2-ephemeral-relay-acceptance.py"
sp=importlib.util.spec_from_file_location("trial_r23b2",PATH)
mod=importlib.util.module_from_spec(sp)
sp.loader.exec_module(mod)

def canary():
    return {"Id":"c"*64,"State":{"Running":True,"Pid":98765}}
def sidecar():
    return {
      "Name":"/"+mod.SIDECAR,"Id":"f"*64,
      "HostConfig":{
        "Privileged":False,"ReadonlyRootfs":True,
        "RestartPolicy":{"Name":"no"},
        "NetworkMode":"container:"+("c"*64),
        "PortBindings":{},
      },
      "Config":{"Labels":{mod.LABEL:"ephemeral-readonly"}},
      "Mounts":[{"Source":str(mod.SOCKDIR),"RW":False}],
      "State":{"Running":True},
    }

class TrialSafety(unittest.TestCase):
    def test_pinned_image_must_be_exact_pull_digest_and_image_id(self):
        self.assertEqual(mod.EXPECTED_IMAGE_ID,
          "sha256:979e5a8a56846771598d47ebf1f8a75b46155104e168a0b237c0783c1dedff8a")
        self.assertEqual(mod.DIGEST,
          "python@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4")
        self.assertEqual(mod.IMAGE,mod.DIGEST)
        self.assertEqual(mod.TTL,600)
    def test_preflight_pin_rejects_missing_or_different_image_without_create(self):
        data={"audit":"R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT",
              "trial_environment_ready":True}
        def fake_cmd(*args,**kwargs):
            self.assertEqual(args[0],"python3")
            return mock.Mock(stdout=json.dumps(data))
        valid={"Id":mod.EXPECTED_IMAGE_ID,"RepoDigests":[mod.DIGEST]}
        with mock.patch.object(mod,"cmd",side_effect=fake_cmd),mock.patch.object(
            mod,"inspect_one",side_effect=[valid,None]):
            self.assertTrue(mod.prereq()["trial_environment_ready"])
        for image in (None,{"Id":"sha256:"+("b"*64),"RepoDigests":[mod.DIGEST]},
                      {"Id":mod.EXPECTED_IMAGE_ID,"RepoDigests":[]}):
            with mock.patch.object(mod,"cmd",side_effect=fake_cmd), mock.patch.object(
                mod,"inspect_one",return_value=image):
                with self.assertRaisesRegex(mod.Stop,
                         "PINNED_LOCAL_PYTHON_IMAGE_DIGEST_MISMATCH"):
                    mod.prereq()
    def test_existing_name_refused_even_with_good_image(self):
        raw={"audit":"R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT",
             "trial_environment_ready":True}
        with mock.patch.object(mod,"cmd",return_value=mock.Mock(stdout=json.dumps(raw))),             mock.patch.object(mod,"inspect_one",side_effect=[
               {"Id":mod.EXPECTED_IMAGE_ID,"RepoDigests":[mod.DIGEST]},
               {"Name":"/foreign"}
             ]):
            with self.assertRaisesRegex(mod.Stop,"EXACT_SIDECAR_NAME_OCCUPIED"):
                mod.prereq()
    def test_network_namespace_owner_must_be_exact_and_no_privileges(self):
        self.assertTrue(mod.owner_ok(sidecar()))
        for key,value in (("NetworkMode","bridge"),("Privileged",True),
                          ("ReadonlyRootfs",False),
                          ("PortBindings",{"9999/tcp":[{"HostIp":"0.0.0.0"}]})):
            c=sidecar()
            c["HostConfig"][key]=value
            self.assertFalse(mod.owner_ok(c))
        c=sidecar()
        c["Config"]["Labels"][mod.LABEL]="some-other-purpose"
        self.assertFalse(mod.owner_ok(c))
    def test_sidecar_creation_never_pulls_or_publishes_or_mutates_canary(self):
        recorded=[]
        def fake_cmd(*args,**kwargs):
            recorded.append(args)
            return mock.Mock(returncode=0)
        with mock.patch.object(mod,"cmd",side_effect=fake_cmd),mock.patch.object(
            mod,"inspect_one",side_effect=[sidecar(),sidecar()]):
            cid=mod.start_sidecar("c"*64)
        self.assertEqual(cid,"f"*64)
        create=recorded[0]
        self.assertEqual(create[:2],("docker","create"))
        for fragment in ("--pull","never","--read-only","--cap-drop",
                         "ALL","--restart","no","--user","65534:65534",
                         "--network","container:"+("c"*64),mod.IMAGE,
                         "--mount"):
            self.assertIn(fragment,create)
        for unsafe in ("-p","--publish","--privileged","--network=host","--volume"):
            self.assertNotIn(unsafe,create)
        self.assertEqual(recorded[1],("docker","start",mod.SIDECAR))
    def test_synthetic_readiness_uses_no_token_and_only_canary_netns(self):
        commands=[]
        def fake_cmd(*args,**kwargs):
            commands.append(args)
            return mock.Mock(returncode=0)
        with mock.patch.object(mod,"cmd",side_effect=fake_cmd):
            mod.probe_netns(1234)
        args=commands[0]
        self.assertEqual(args[:5],("nsenter","-t","1234","-n","python3"))
        self.assertIn("127.0.0.1',9999",args[-1])
        self.assertNotIn("X-Emby-Token",args[-1])
    def test_strict_no_confirmation_and_no_sudo_does_not_mutate(self):
        with mock.patch.object(mod.os,"geteuid",return_value=0),             mock.patch.object(mod,"prereq",return_value={}):
            with mock.patch.object(mod.sys,"argv",["trial","--apply"]),                 contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(mod.main(),5)
                self.assertIn("EXPLICIT_TRIAL_CONFIRMATION_REQUIRED",output.getvalue())
        with mock.patch.object(mod.os,"geteuid",return_value=1000),             mock.patch.object(mod.sys,"argv",["trial","--apply",
                            "--confirm","R23B2_SINGLE_600S_EPHEMERAL_ACCEPTANCE"]),             contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(mod.main(),5)
            self.assertIn("SUDO_ROOT_REQUIRED",output.getvalue())
    def test_rollback_does_not_remove_foreign_container(self):
        other=sidecar()
        other["Config"]["Labels"][mod.LABEL]="not-ours"
        with mock.patch.object(mod,"inspect_one",return_value=other),             mock.patch.object(mod.os.path,"lexists",return_value=False),             mock.patch.object(mod,"cmd") as command:
            self.assertFalse(mod.do_cleanup())
            command.assert_not_called()
    def test_timeout_and_operator_uid_demotion_visible_in_code(self):
        s=PATH.read_text()
        for needed in ("preexec_fn=demote","os.setgroups([])",
                       "os.setgid(gid)","os.setuid(uid)","timeout=480",
                       "trial_environment_ready","--ttl-seconds",
                       "R23B2_EPHEMERAL_EXACT_ROLLBACK_"):
            self.assertIn(needed,s)
        for prohibited in ("docker system prune","docker volume rm",
                           "docker network connect","nft flush",
                           "systemctl restart","iptables -F","nginx -s"):
            self.assertNotIn(prohibited,s)

if __name__=="__main__":
    unittest.main()
