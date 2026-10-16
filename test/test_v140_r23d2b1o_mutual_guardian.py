"""R23D-2B1O: unprivileged boundary tests, no Docker/systemd invocation."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SHELL=ROOT/"scripts"/"probe-v140-r23d2b1o-admission-failure-ci.sh"
PY=ROOT/"scripts"/"probe-v140-r23d2b1o-admission-guardian-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class MutualWatchdogBoundaries(unittest.TestCase):
    def test_script_syntax(self):
        self.assertEqual(subprocess.run(["bash","-n",str(SHELL)],
            capture_output=True,timeout=4).returncode,0)
        self.assertEqual(subprocess.run(["python3","-B","-m","py_compile",str(PY)],
            capture_output=True,timeout=4).returncode,0)
    def test_default_shell_disallowed(self):
        e={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"}
        p=subprocess.run(["bash",str(SHELL)],capture_output=True,timeout=5,env=e)
        self.assertEqual(p.returncode,3)
        self.assertIn(b"STOP_NOT_PRIVILEGED_EPHEMERAL_GITHUB_RUNNER",p.stderr)
    def test_selfhosted_shell_disallowed(self):
        e={**os.environ,"GITHUB_ACTIONS":"true","CI":"true",
           "RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"self-hosted"}
        p=subprocess.run(["bash",str(SHELL),"--ci-ephemeral-only"],
             capture_output=True,timeout=5,env=e)
        self.assertEqual(p.returncode,3)
    def test_python_cannot_start_unless_ephemeral(self):
        code=PY.read_text()
        self.assertIn('os.getenv("R23D2B1O_EPHEMERAL_ONLY")=="true"',code)
        self.assertIn("B1O_STOP_NOT_EPHEMERAL_RUNNER",code)
        self.assertIn('r23d2b1o.lab',code)
    def test_independent_process_failure_cases_and_negative_control(self):
        shell=SHELL.read_text()
        for marker in (
            "R23D2B1O_SIGKILL_GUARDIAN_INDEPENDENT_WATCHDOG_KILL_PASS",
            "R23D2B1O_SIGKILL_WATCHDOG_GUARDIAN_KILL_PASS",
            "R23D2B1O_REAL_DOCKER_DAEMON_RESTART_ADMISSION_CLOSED_PASS",
            "R23D2B1O_POST_DAEMON_RECOVERY_REARMED_BEFORE_ADMISSION_PASS",
            "R23D2B1O_DUAL_GUARDIAN_WATCHDOG_LOSS_EXPOSURE_RELEASE_BLOCKER_PROVEN",
            "R23D2B1O_OWNED_DOCKER_RESOURCE_CLEANUP_PASS"):
            self.assertIn(marker,shell)
        self.assertIn("kill -KILL",shell)
        self.assertIn('timeout 100 systemctl restart docker',shell)
        self.assertIn("docker stop --time 1",shell)
    def test_enforced_runner_scoping_and_no_production_integrations(self):
        wf=CI.read_text()
        self.assertIn("r23d-mutual-admission-guardian-lab:",wf)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",wf)
        self.assertIn("runs-on: ubuntu-24.04",wf)
        self.assertIn("scripts/probe-v140-r23d2b1o-admission-failure-ci.sh --ci-ephemeral-only",wf)
        s=SHELL.read_text()
        self.assertIn("--internal",s)
        self.assertIn("--restart no",s)
        self.assertIn("--cap-drop ALL",s)
        self.assertIn("--read-only",s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("nft flush ruleset",s)
        self.assertNotIn("docker volume rm",s)
    def test_heartbeat_files_and_mutual_failure_detection(self):
        p=PY.read_text()
        self.assertIn("time.monotonic()",p)
        self.assertIn("def watchdog_loop",p)
        self.assertIn("def guard_loop",p)
        self.assertIn("R23D2B1O_INDEPENDENT_WATCHDOG_STOPPED_FAKE_GATEWAY",p)
        self.assertIn("R23D2B1O_GUARDIAN_DETECTED_WATCHDOG_LOSS_STOPPED_FAKE_GATEWAY",p)
        self.assertIn("arm(dev,a.client_ip,a.server_ip)",p)
        self.assertLess(p.index("arm(dev,a.client_ip,a.server_ip)"),
                        p.index('run("docker","start",a.server'))
if __name__=="__main__":
    unittest.main()
