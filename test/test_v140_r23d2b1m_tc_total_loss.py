"""R23D-2B1M private namespace + two independent kernel mechanisms.

Unit tests never execute privileged network commands.
"""
from pathlib import Path
import os
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"probe-v140-r23d2b1m-tc-nft-total-loss-ci.sh"
CI=ROOT/".github/workflows/ci.yml"

class R23D2B1MSafety(unittest.TestCase):
    def test_shell_syntax(self):
        result=subprocess.run(["bash","-n",str(SCRIPT)],
                              capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_default_invocation_does_not_run(self):
        env={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"}
        result=subprocess.run(["bash",str(SCRIPT)],
                              capture_output=True,timeout=5,env=env)
        self.assertEqual(result.returncode,3)
        self.assertIn(b"STOP_NOT_EPHEMERAL",result.stderr)
    def test_self_hosted_guard_rejected(self):
        env={**os.environ,"GITHUB_ACTIONS":"true","CI":"true",
             "RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"self-hosted"}
        result=subprocess.run(["bash",str(SCRIPT),"--ci-ephemeral-only"],
                              capture_output=True,timeout=5,env=env)
        self.assertEqual(result.returncode,3)
        self.assertIn(b"STOP_NOT_EPHEMERAL",result.stderr)
    def test_boot_barrier_built_before_link_enabled(self):
        code=SCRIPT.read_text()
        self.assertLess(code.index("install_tc\n# Confirm rules"),
                        code.index('ip -n "$NS_H" link set r23ms0 up'))
        self.assertIn('must_deny 172.31.254.1 17420',code)
        self.assertIn("R23D2B1M_LINK_DOWN_BEFORE_FENCE_PASS",code)
    def test_total_flush_is_in_private_netns_only(self):
        code=SCRIPT.read_text()
        self.assertIn('ip netns exec "$NS_H" nft flush ruleset',code)
        self.assertNotIn('\nnft flush ruleset',code)
        self.assertNotIn("systemctl ",code)
        self.assertNotIn("docker run",code)
        self.assertIn("R23D2B1M_TOTAL_NFT_FLUSH_TC_INDEPENDENT_DENY_PASS",code)
    def test_independent_tc_negative_and_positive_controls(self):
        code=SCRIPT.read_text()
        for chunk in ('tc filter add dev r23ms0 ingress protocol ip pref 10 u32',
                      'tc filter add dev r23ms0 ingress protocol all pref 20',
                      'matchall action drop',
                      'tc qdisc del dev r23ms0 clsact',
                      'R23D2B1M_REMOVING_LAST_FENCE_EXPOSES_FORBIDDEN_TCP_POSITIVE_CONTROL',
                      'R23D2B1M_TC_FENCE_RESTORED_PASS'):
            self.assertIn(chunk,code)
    def test_ci_restricts_privileged_lab_to_github_hosted(self):
        ci=CI.read_text()
        self.assertIn("runs-on: ubuntu-24.04",ci)
        self.assertIn("R23D-2B1M independent tc boot fence",ci)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",ci)
        self.assertIn("scripts/probe-v140-r23d2b1m-tc-nft-total-loss-ci.sh --ci-ephemeral-only",ci)

if __name__=="__main__":
    unittest.main()
