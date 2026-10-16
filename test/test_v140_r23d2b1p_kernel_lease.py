"""R23D-2B1P: real-kernel timed-lease fault injection safety boundaries.
These tests never invoke Docker, nftables, root or host systemd.
"""
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SHELL=ROOT/"scripts"/"probe-v140-r23d2b1p-real-docker-nft-lease-ci.sh"
GUARD=ROOT/"scripts"/"probe-v140-r23d2b1o-admission-guardian-ci.py"
PROBE=ROOT/"scripts"/"probe-v140-r23d2b1p-fake-tcp-session-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class ExpiringLeaseSafety(unittest.TestCase):
    def test_shell_parses(self):
        p=subprocess.run(["bash","-n",str(SHELL)],capture_output=True,timeout=4)
        self.assertEqual(p.returncode,0,p.stderr.decode(errors="replace"))
    def test_no_ephemeral_flag_stops_before_docker(self):
        env={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"}
        p=subprocess.run(["bash",str(SHELL)],capture_output=True,timeout=4,env=env)
        self.assertEqual(p.returncode,3)
        self.assertIn(b"STOP_NOT_PRIVILEGED_GITHUB_EPHEMERAL",p.stderr)
    def test_self_hosted_runner_rejected(self):
        env={**os.environ,"GITHUB_ACTIONS":"true","CI":"true","RUNNER_OS":"Linux",
             "RUNNER_ENVIRONMENT":"self-hosted"}
        p=subprocess.run(["bash",str(SHELL),"--ci-ephemeral-only"],
                         capture_output=True,timeout=4,env=env)
        self.assertEqual(p.returncode,3)
    def test_kernel_nft_lease_fails_closed_even_if_both_processes_killed(self):
        code=SHELL.read_text()
        for m in ("add table bridge", "flags timeout; timeout 3s",
                  "R23D2B1P_DUAL_SIGKILL_KERNEL_LEASE_FAIL_CLOSED_WITHOUT_USERS_PASSES",
                  "R23D2B1P_DUAL_SIGKILL_EXISTING_TCP_SESSION_CUTOFF_PASS",
                  "R23D2B1P_DELETING_LAST_KERNEL_LEASE_FENCE_REOPENS_TCP_CONTROL",
                  "R23D2B1P_EMPTY_KERNEL_LEASE_RECREATED_DENY_PASS",
                  "R23D2B1P_OWNED_DOCKER_AND_NFT_CLEANUP_PASS"):
            self.assertIn(m,code)
        self.assertIn('kill -KILL "$GUARDIAN_PID" "$WATCHDOG_PID"',code)
        self.assertLess(code.index('create_table\nnft list set'),
                        code.index('python3 -B "$GUARD" --role guardian'))
    def test_scopes_filter_to_the_exact_owned_veth(self):
        s=SHELL.read_text()
        self.assertIn('iifname "$PEER_DEV"',s)
        self.assertIn('oifname "$PEER_DEV"',s)
        self.assertIn('ip saddr @authorized accept',s)
        self.assertIn('ip daddr @authorized accept',s)
        self.assertIn('priority -250; policy accept;',s)
        self.assertIn("--internal",s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("docker volume rm",s)
        self.assertNotIn("systemctl restart docker",s)
    def test_guardian_refresh_only_after_two_live_checks(self):
        s=GUARD.read_text()
        self.assertIn('p.add_argument("--kernel-lease",action="store_true")',s)
        self.assertIn("def refresh_optional_kernel_lease(a):",s)
        self.assertIn('["nft","-f","-"]',s)
        self.assertIn("flush set bridge r23d2b1p_lease authorized",s)
        self.assertIn("{ 172.31.252.2 timeout 3s }",s)
        loop=s[s.index("def guard_loop(a):"):s.index("def watchdog_loop(a):")]
        self.assertLess(loop.index("if not alive(a.watchdog_pid) or not fresh(a.watchdog_heartbeat):"),
                        loop.index("refresh_optional_kernel_lease(a)"))
        self.assertLess(loop.index("refresh_optional_kernel_lease(a)"),
                        loop.index('run("docker","start",a.server'))
    def test_persistent_session_probes_established_tcp(self):
        s=PROBE.read_text()
        self.assertIn("R23D2B1P_EXISTING_TCP_SESSION_ESTABLISHED",s)
        self.assertIn("R23D2B1P_ESTABLISHED_TCP_NO_LONGER_REACHABLE",s)
        self.assertIn("R23D2B1P_UNSAFE_SESSION_STAYED_OPEN",s)
    def test_dedicated_runner(self):
        wf=CI.read_text()
        self.assertIn("r23d-kernel-expiring-admission-lease-lab:",wf)
        self.assertIn("runs-on: ubuntu-24.04",wf)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",wf)
        self.assertIn("scripts/probe-v140-r23d2b1p-real-docker-nft-lease-ci.sh --ci-ephemeral-only",wf)

if __name__=="__main__": unittest.main()
