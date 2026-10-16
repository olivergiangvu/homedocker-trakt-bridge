"""R23D-2B1R disposable *installed* systemd unit safety regressions."""
import ast
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SH=ROOT/"scripts"/"probe-v140-r23d2b1r-persistent-systemd-docker-ci.sh"
PY=ROOT/"scripts"/"probe-v140-r23d2b1r-persistent-systemd-monitor-ci.py"
CI=ROOT/".github/workflows/ci.yml"

class PersistentControllerSafety(unittest.TestCase):
    def test_source_parses(self):
        p=subprocess.run(["bash","-n",str(SH)],capture_output=True,timeout=4)
        self.assertEqual(p.returncode,0,p.stderr)
        ast.parse(PY.read_text(encoding="utf8"))
    def test_normal_hosts_rejected_before_privileged_commands(self):
        for args,env in (
          ([],{**os.environ,"CI":"false","GITHUB_ACTIONS":"false"}),
          (["--ci-ephemeral-only"],{**os.environ,"CI":"true",
           "GITHUB_ACTIONS":"true","RUNNER_OS":"Linux",
           "RUNNER_ENVIRONMENT":"self-hosted"})):
            p=subprocess.run(["bash",str(SH),*args],capture_output=True,
                             timeout=4,env=env)
            self.assertEqual(p.returncode,3)
            self.assertIn(b"R23D2B1R_STOP_NOT_DISPOSABLE_GITHUB_RUNNER",p.stderr)
    def test_real_persistent_unit_is_scoped_and_removed(self):
        s=SH.read_text()
        for x in ('UNIT_FILE="/etc/systemd/system/$UNIT"',
                  "systemctl enable", "systemctl daemon-reload",
                  "WantedBy=docker.service", "BindsTo=docker.service",
                  "PartOf=docker.service", "WatchdogSec=8",
                  "Type=notify", "Restart=always", 'rm -f -- "$UNIT_FILE"',
                  "R23D2B1R_OWNED_SYSTEMD_DOCKER_NFT_CLEANUP_PASS"):
            self.assertIn(x,s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("nft flush ruleset",s)
        self.assertNotIn("reboot -f",s)
        self.assertNotIn("docker volume rm",s)
    def test_livedocker_network_loss_detected_without_shell_detach(self):
        s=SH.read_text()
        p=PY.read_text()
        self.assertIn("R23D2B1R_PERSISTENT_MONITOR_AUTO_DETACHED_AFTER_NFT_LOSS_PASS",s)
        self.assertIn('"network","disconnect","-f",network,server',p)
        self.assertIn('notify("READY=1',p)
        self.assertIn("R23D2B1R_DOCKER_RESTART_REBUILT_FENCES_AUTO_READMISSION_PASS",s)
        self.assertIn('timeout 100 systemctl restart docker',s)
    def test_ownership_checked_before_reattach(self):
        p=PY.read_text()
        self.assertIn('labels.get("r23d2b1q.lab")=="disposable"',p)
        self.assertIn('row.get("master")!=b1q.BRIDGE',p)
        self.assertIn('b1q.verify(args)',p)
        self.assertIn('b1q.call("docker","network","connect","--ip"',p)
        self.assertLess(p.index('b1q.verify(args)'),
                        p.index('b1q.call("docker","network","connect","--ip"'))
    def test_dedicated_job_and_explicit_reboot_hold(self):
        w=CI.read_text()
        self.assertIn("r23d-persistent-systemd-boot-interlock-lab:",w)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",w)
        s=SH.read_text()
        self.assertIn("NOT a true VM reboot",s)
        self.assertIn("R23D2B1R_PERSISTENT_SYSTEMD_REAL_DOCKER_CI_PASS_TRUE_REBOOT_HOLD",s)

if __name__=="__main__":unittest.main()
