"""B1T security and isolation regressions. Unit tests never touch Docker."""
from pathlib import Path
import ast
import os
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SH=ROOT/"scripts"/"probe-v140-r23d2b1t-real-nat-and-socket-ci.sh"
PY=ROOT/"scripts"/"probe-v140-r23d2b1t-external-echo-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class NatSocketBoundary(unittest.TestCase):
    def test_syntax(self):
        p=subprocess.run(["bash","-n",str(SH)],capture_output=True,timeout=4)
        self.assertEqual(p.returncode,0,p.stderr.decode(errors="replace"))
        ast.parse(PY.read_text())
    def test_normal_or_self_hosted_refused(self):
        for args,env in (
            ([],{**os.environ,"CI":"false","GITHUB_ACTIONS":"false"}),
            (["--ci-ephemeral-only"],{**os.environ,"CI":"true",
                "GITHUB_ACTIONS":"true","RUNNER_OS":"Linux",
                "RUNNER_ENVIRONMENT":"self-hosted"})):
            p=subprocess.run(["bash",str(SH),*args],capture_output=True,timeout=4,env=env)
            self.assertEqual(p.returncode,3,p.stderr)
            self.assertIn(b"STOP_NOT_DISPOSABLE_GITHUB_RUNNER",p.stderr)
    def test_private_scoped_network_and_loopback_host_only(self):
        s=SH.read_text()
        self.assertIn('com.docker.network.bridge.enable_ip_masquerade=true',s)
        self.assertIn('--publish 127.0.0.1::17420',s)
        self.assertIn('pub[0]["HostIp"]=="127.0.0.1"',s)
        self.assertIn('172.31.249.2',PY.read_text())
        self.assertIn('172.31.249.1',PY.read_text())
        self.assertIn('--network none',s)
        self.assertIn('--restart no',s)
        self.assertIn('--cap-drop ALL',s)
    def test_positive_and_negative_controls(self):
        s=SH.read_text()
        for marker in (
            "R23D2B1T_UNADMITTED_SERVER_NO_NETWORK_PASS",
            "R23D2B1T_UNPRIVILEGED_DOCKER_SOCKET_ACCESS_DENIED_PASS",
            "R23D2B1T_REAL_LOOPBACK_HOST_PORT_PUBLISH_PASS",
            "R23D2B1T_NONINTERNAL_REAL_SNAT_PATH_PASS",
            "R23D2B1T_NETWORK_DISCONNECT_CUTS_PUBLISHED_PORT_PASS",
            "R23D2B1T_EXACT_DOCKER_NAMESPACE_CLEANUP_PASS"):
            self.assertIn(marker,s)
        self.assertIn('setpriv --reuid 65534 --regid 65534 --clear-groups',s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("nft flush ruleset",s)
        self.assertNotIn("systemctl restart docker",s)
        self.assertNotIn("docker volume rm",s)
    def test_namespaced_echo_fails_if_natted_ip_is_not_host(self):
        s=PY.read_text()
        self.assertIn('if seen!=TARGET:',s)
        self.assertIn("R23D2B1T_NAT_SOURCE_NOT_HOST_VETH",s)
    def test_dedicated_runner(self):
        w=CI.read_text()
        self.assertIn("r23d-production-shaped-network-and-socket-lab:",w)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",w)
        self.assertIn("probe-v140-r23d2b1t-real-nat-and-socket-ci.sh --ci-ephemeral-only",w)

if __name__=="__main__":
    unittest.main()
