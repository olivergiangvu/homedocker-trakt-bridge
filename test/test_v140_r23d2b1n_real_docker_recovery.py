"""R23D-2B1N dedicated GitHub runner, real Docker daemon failure lab guardrails.

Unprivileged tests NEVER restart or alter a Docker daemon.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SHELL=ROOT/"scripts"/"probe-v140-r23d2b1n-real-docker-daemon-ci.sh"
FAKE=ROOT/"scripts"/"probe-v140-r23d2b1n-docker-fake-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class R23D2B1NGuard(unittest.TestCase):
    def test_shell_syntax(self):
        r=subprocess.run(["bash","-n",str(SHELL)],capture_output=True,timeout=4)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_no_flag_rejected_before_docker(self):
        r=subprocess.run(["bash",str(SHELL)],capture_output=True,timeout=4,
            env={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"})
        self.assertEqual(r.returncode,3,r.stderr)
        self.assertIn(b"STOP_NOT_PRIVILEGED_GITHUB_HOSTED_EPHEMERAL",r.stderr)
    def test_self_hosted_rejected_before_docker(self):
        r=subprocess.run(["bash",str(SHELL),"--ci-ephemeral-only"],
           capture_output=True,timeout=4,
           env={**os.environ,"GITHUB_ACTIONS":"true","RUNNER_OS":"Linux",
                "RUNNER_ENVIRONMENT":"self-hosted","CI":"true"})
        self.assertEqual(r.returncode,3,r.stderr)
        self.assertIn(b"STOP_NOT_PRIVILEGED_GITHUB_HOSTED_EPHEMERAL",r.stderr)
    def test_daemon_restart_only_in_dedicated_job(self):
        wf=CI.read_text(encoding="utf8")
        self.assertIn("r23d-real-docker-daemon-recovery-lab:",wf)
        self.assertIn("runs-on: ubuntu-24.04",wf)
        self.assertIn("scripts/probe-v140-r23d2b1n-real-docker-daemon-ci.sh --ci-ephemeral-only",wf)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",wf)
        code=SHELL.read_text(encoding="utf8")
        self.assertIn('timeout 100 systemctl restart docker',code)
        self.assertIn('[[ -z "$(docker ps -aq --no-trunc)" ]]',code)
        self.assertIn("R23D2B1N_STOP_LIVE_RESTORE_ENABLED",code)
    def test_docker_resources_are_private_and_nonrestarting(self):
        code=SHELL.read_text(encoding="utf8")
        for evidence in ('--internal', '--restart no', '--cap-drop ALL',
                         '--read-only','--security-opt no-new-privileges',
                         'com.docker.network.bridge.name',
                         'R23D2B1N_ACTUAL_DOCKER_DAEMON_RESTART_PEERS_HELD_STOPPED_PASS',
                         'R23D2B1N_DOCKER_RECREATED_VETH_LOST_OLD_TC_PROVEN',
                         'R23D2B1N_TC_REARMED_BEFORE_FAKE_GATEWAY_START_PASS'):
            self.assertIn(evidence,code)
        self.assertNotIn("docker system prune",code)
        self.assertNotIn("docker volume rm",code)
        self.assertNotIn("nft flush ruleset",code)
    def test_order_gate_rearms_before_fake_gateway_restart(self):
        code=SHELL.read_text(encoding="utf8")
        index=code.index('docker start "$CLIENT" >/dev/null')
        self.assertLess(index,code.index('arm_tc "$NEW_PEER_IF"'))
        self.assertLess(code.index('arm_tc "$NEW_PEER_IF"'),
                        code.index('docker start "$SERVER" >/dev/null'))
        self.assertIn('ready 17421 || {',code)
        self.assertIn("R23D2B1N_REARMED_FILTER_FAIL_CLOSED_PASS",code)
    def test_fake_probe_disallows_any_nonlab_destination(self):
        spec=importlib.util.spec_from_file_location("fake_docker_ci",FAKE)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for ip,port in (("127.0.0.1",17420),
                        ("8.8.8.8",443),
                        ("172.31.252.3",443),
                        ("172.31.254.3",17420)):
            with self.subTest(ip=ip,port=port):
                with self.assertRaises(module.Stop):
                    module.client_probe(ip,port)

if __name__=="__main__":
    unittest.main()
