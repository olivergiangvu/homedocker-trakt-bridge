import importlib.util
import io
import ipaddress
import json
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"scripts"/"plan-v140-r23d2b-gateway-offline.py"
spec=importlib.util.spec_from_file_location("r23d2b_gateway_no_write",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA="d72b4109944c0b552be1b9847134ad93fa8a6d78"
NET=ipaddress.ip_network("10.231.240.16/28")

class OfflineGatewayPlanTests(unittest.TestCase):
    def test_source_image_contract_matches_actual_gateway_only_dockerfile(self):
        self.assertTrue(m.check_gateway_source())
        self.assertEqual(m.GUEST_PROOF_PATH,"/run/identity-proof.json")
        self.assertIn("GATEWAY_PROOF_FILE=/run/identity-proof.json",
            m.GATEWAY_IMAGE_FILE.read_text())
        self.assertNotIn("VOLUME ",m.GATEWAY_IMAGE_FILE.read_text())

    def test_command_contract_is_pinned_sealed_single_network_no_credentials(self):
        plan=m.planned_commands(NET,SHA)
        cmd=plan["commands"]
        tag="homedocker-trakt-bridge:v140-r23d-"+SHA[:12]
        self.assertEqual(plan["image_tag"],tag)
        self.assertIn(tag,cmd["docker_build"])
        self.assertIn("Dockerfile.gateway-canary",str(cmd["docker_build"]))
        self.assertIn("--network=none",cmd["docker_build"])
        self.assertIn("--pull=false",cmd["docker_build"])
        self.assertEqual(cmd["network_create"][0:3],
                         ["docker","network","create"])
        self.assertIn("com.docker.network.bridge.name=br-r23dgate",
                      cmd["network_create"])
        self.assertIn("com.docker.network.bridge.enable_ip_masquerade=false",
                      cmd["network_create"])
        self.assertIn("homedocker.phase=r23d-persistent-canary",
                      cmd["network_create"])
        self.assertEqual(cmd["guard_check"],["nft","--check","--file","-"])
        self.assertEqual(cmd["guard_install"],["nft","--file","-"])
        self.assertIn('iifname "br-r23dgate" drop',plan["policy"])
        run=cmd["gateway_run"]
        for expected in (
            "--read-only","--cap-drop","ALL","--security-opt",
            "no-new-privileges","--restart","no","--pull","never",
            "--network",m.FINAL_NETWORK,"--ip",plan["canary"],
            "--publish","127.0.0.1:17145:7101",
            "type=bind,src="+m.HOST_PROOF_PATH+
                ",dst=/run/identity-proof.json,readonly",
            "JELLYFIN_GATEWAY_AIO_USER_ID=R23D_FAKE_USER_000",
            "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH=false",
        ):
            self.assertIn(expected,run)
        self.assertIn("JELLYFIN_GATEWAY_UPSTREAM_URL=http://"+
                      plan["gateway"]+":17420/jellyfin",run)
        self.assertNotIn("media_core_net",json.dumps(run))
        for danger in ("TRAKT_CLIENT_SECRET","X-Emby-Token","/app/bridge.db",
                       "docker.sock","--privileged","--network=host",
                       "JELLYFIN_GATEWAY_RESUME_PASSTHROUGH=true",
                       "ghcr.io/viren070/aiostreams"):
            self.assertNotIn(danger,json.dumps(cmd))

    def test_invalid_image_commit_is_rejected_before_argv_generation(self):
        for bad in ("xyz",SHA[:11],"z"*40,SHA+"0"):
            with self.subTest(bad=bad),self.assertRaises(m.Stop):
                m.planned_commands(NET,bad)

    def test_actual_git_source_identity_is_readonly_and_per_checkout_safe_directory(self):
        seen=[]
        def run(args,**kw):
            seen.append(args)
            output=SHA+"\n" if args[-2:]==["rev-parse","HEAD"] else ""
            return mock.Mock(returncode=0,stdout=output,stderr="")
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m.subprocess,"run",side_effect=run):
            self.assertEqual(m.source_identity(),SHA)
        self.assertEqual(len(seen),2)
        for call in seen:
            self.assertEqual(call[0:3],["git","-c","safe.directory="+str(ROOT)])
            self.assertIn("-C",call)
            self.assertNotIn("config --global",str(call))
            self.assertNotIn("reset",call)

    def test_dirty_git_source_rejected_and_nonroot_refused(self):
        with mock.patch.object(m.os,"geteuid",return_value=1000), \
             self.assertRaisesRegex(m.Stop,"SUDO_REQUIRED"):
            m.source_identity()
        calls=[
          mock.Mock(returncode=0,stdout=SHA+"\n",stderr=""),
          mock.Mock(returncode=0,stdout=" M unsafe.py\n",stderr=""),
        ]
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m.subprocess,"run",side_effect=calls), \
             self.assertRaisesRegex(m.Stop,"GIT_SOURCE_NOT_IMMUTABLE_CLEAN"):
            m.source_identity()

    def test_readonly_main_prints_only_fixed_safe_flags(self):
        report=io.StringIO()
        with mock.patch.object(m,"preflight",return_value=m.planned_commands(NET,SHA)), \
             redirect_stdout(report):
            m.main()
        s=report.getvalue()
        self.assertIn("R23D2B0_READONLY_DEPLOYMENT_PLAN_PASS",s)
        for sensitive in (str(NET), "10.231.240.17","br-r23dgate",
                          SHA, "identity-proof.json", "table inet"):
            self.assertNotIn(sensitive,s)
        self.assertIn('"actual_final_network_or_firewall_created": false',s)
        self.assertIn('"real_jellyfin_session_allowed": false',s)

    def test_host_preflight_reads_only_and_enforces_final_resource_collisions(self):
        planner=mock.Mock()
        planner.TEST_BRIDGE="br-r23dtrial"
        planner.inspect_candidates.return_value=(NET,None)
        planner.parsed.return_value=[{"ifname":"eth0"},{"ifname":"lo"}]
        renderer=m.load(m.RENDER,"r23d2b_test_renderer")
        verified=m.load(m.NFT,"r23d2b_test_guard")
        real_load=m.load
        def fake_load(path,name):
            if path==m.PLANNER: return planner
            if path==m.RENDER: return renderer
            if path==m.NFT: return verified
            return real_load(path,name)
        with mock.patch.object(m,"source_identity",return_value=SHA), \
             mock.patch.object(m,"check_gateway_source",return_value=True), \
             mock.patch.object(m,"load",side_effect=fake_load):
            planner.run.side_effect=[b"old_net\n",b"old_container\n",b""]
            plan=m.preflight()
            self.assertEqual(plan["net"],str(NET))
            self.assertEqual(planner.run.call_count,3)
            args,kwargs=planner.run.call_args
            self.assertEqual(args[0],["nft","--check","--file","-"])
            self.assertIn(b"table inet",kwargs["stdin"])
            planner.parsed.return_value=[{"ifname":"br-r23dgate"}]
            planner.run.reset_mock()
            planner.run.side_effect=[b"",b""]
            with self.assertRaisesRegex(m.Stop,"FINAL_R23D_DOCKER_RESOURCES_ALREADY_EXIST"):
                m.preflight()
            planner.parsed.return_value=[{"ifname":"eth0"}]
            planner.run.reset_mock()
            planner.run.side_effect=[b"homedocker_v140_r23d_local\n",b""]
            with self.assertRaisesRegex(m.Stop,"FINAL_R23D_DOCKER_RESOURCES_ALREADY_EXIST"):
                m.preflight()

    def test_no_host_mutation_commands_or_session_unlock_paths(self):
        source=SRC.read_text()
        for bad in (
           'parser.add_argument("--apply"', "docker network create",
           "nft add table", "nft flush ruleset", "subprocess.Popen(",
           "shell=True", "requests.post(", "X-Emby-Token",
           "AuthenticateByName", "docker rm -f", "iptables -F"
        ):
            self.assertNotIn(bad,source)
        self.assertIn('"nft","--check","--file","-"',source)
        self.assertIn('"actual_final_network_or_firewall_created":False',source)
        self.assertFalse(m.CONFIRMATION_ALLOWED)

    def test_redacted_error_does_not_reveal_inner_network_or_tokens(self):
        out=io.StringIO()
        with mock.patch.object(m,"preflight",
            side_effect=RuntimeError("PRIVATE_IP 10.0.0.1 X-Emby-Token")), \
            redirect_stderr(out),self.assertRaises(SystemExit) as ctx:
            m.main()
        self.assertEqual(ctx.exception.code,1)
        self.assertIn("R23D2B0_SAFE_DIAGNOSTIC",out.getvalue())
        self.assertNotIn("10.0.0.1",out.getvalue())
        self.assertNotIn("X-Emby-Token",out.getvalue())

if __name__=="__main__":
    unittest.main()
