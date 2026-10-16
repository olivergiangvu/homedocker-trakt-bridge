"""R23D-2B1Q regression guardrails: no privileged commands in unit tests."""
import os
from pathlib import Path
import subprocess
import unittest
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
SH=ROOT/"scripts"/"probe-v140-r23d2b1q-systemd-docker-admission-ci.sh"
PY=ROOT/"scripts"/"probe-v140-r23d2b1q-systemd-admission-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class AdmissionInterlockSafety(unittest.TestCase):
    def test_code_compiles_safely(self):
        self.assertEqual(subprocess.run(["bash","-n",str(SH)],
                                         capture_output=True,timeout=3).returncode,0)
        import ast
        ast.parse(PY.read_text(encoding="utf8"))
    def test_normal_host_cannot_run(self):
        result=subprocess.run(["bash",str(SH)],capture_output=True,timeout=3,
                              env={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"})
        self.assertEqual(result.returncode,3)
        self.assertIn(b"STOP_NOT_DISPOSABLE_GITHUB_RUNNER",result.stderr)
    def test_self_hosted_runner_rejected(self):
        env={**os.environ,"GITHUB_ACTIONS":"true","CI":"true",
             "RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"self-hosted"}
        result=subprocess.run(["bash",str(SH),"--ci-ephemeral-only"],
                              capture_output=True,timeout=3,env=env)
        self.assertEqual(result.returncode,3)
    def test_boot_qualification_not_claimed(self):
        s=SH.read_text(encoding="utf8")
        self.assertIn("Not a real reboot",s)
        self.assertIn("R23D2B1Q_EPHEMERAL_INTERLOCK_LAB_PASS_BOOT_REBOOT_HOLD",s)
        self.assertIn("systemd-run --quiet --pipe --wait --collect",s)
        self.assertIn("timeout 100 systemctl restart docker",s)
        self.assertNotIn("reboot -f",s)
        self.assertNotIn("nft flush ruleset",s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("docker volume rm",s)
    def test_private_peer_disconnected_before_missing_nft(self):
        s=SH.read_text(encoding="utf8")
        self.assertIn('--network none',s)
        self.assertIn('docker network disconnect -f "$NET" "$SERVER"',s)
        self.assertLess(s.index('docker network disconnect -f "$NET" "$SERVER"'),
                        s.index('timeout 100 systemctl restart docker'))
        self.assertIn("R23D2B1Q_NFT_LOSS_WHILE_ATTACHED_EXPOSES_ALLOWED_TCP_RELEASE_BLOCKER",s)
        self.assertIn("R23D2B1Q_SYSTEMD_REJECTS_DAEMON_RESTART_WITHOUT_BOTH_FENCES_PASS",s)
    def test_exact_labels_and_nets_verified_before_admission(self):
        code=PY.read_text(encoding="utf8")
        self.assertIn('labels.get("r23d2b1q.lab")!="disposable"',code)
        self.assertIn('iface.get("master")!=BRIDGE',code)
        self.assertIn("NFT_ADMISSION_POLICY_MISSING",code)
        self.assertIn("INDEPENDENT_TC_FENCE_NOT_PROVEN",code)
        self.assertIn('call("docker","network","connect","--ip",IP_SERVER,network,server)',code)
        self.assertLess(code.index("server,network=verify(a)"),
                        code.index('call("docker","network","connect"'))
        self.assertIn('R23D2B1Q_SYSTEMD_INTERLOCK_ADMITTED_AFTER_TC_AND_NFT',code)
    def test_runner_job_isolated(self):
        code=CI.read_text(encoding="utf8")
        self.assertIn("r23d-systemd-network-interlock-lab:",code)
        self.assertIn("runs-on: ubuntu-24.04",code)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",code)
        self.assertIn("probe-v140-r23d2b1q-systemd-docker-admission-ci.sh --ci-ephemeral-only",code)

if __name__=="__main__":
    unittest.main()
