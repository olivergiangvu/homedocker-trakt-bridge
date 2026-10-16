"""B1S safety checks: no local privileged actions or VM guest reboot."""
import ast
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
ATTACK=ROOT/"scripts"/"probe-v140-r23d2b1s-privileged-docker-bypass-ci.sh"
VM=ROOT/"scripts"/"probe-v140-r23d2b1s-real-vm-reboot-ci.sh"
CI=ROOT/".github/workflows/ci.yml"

class R23D2B1S(unittest.TestCase):
    def test_scripts_parse_without_execution(self):
        for path in (ATTACK,VM):
            p=subprocess.run(["bash","-n",str(path)],capture_output=True,timeout=5)
            self.assertEqual(p.returncode,0,(path.name,p.stderr))
    def test_safe_default_rejects_home_server_and_selfhost(self):
        for path in (ATTACK,VM):
            env={**os.environ,"GITHUB_ACTIONS":"false","CI":"false"}
            p=subprocess.run(["bash",str(path)],capture_output=True,timeout=5,env=env)
            self.assertEqual(p.returncode,3,(path.name,p.stderr))
            env.update({"GITHUB_ACTIONS":"true","CI":"true",
                        "RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"self-hosted"})
            p=subprocess.run(["bash",str(path),"--ci-ephemeral-only"],
                capture_output=True,timeout=5,env=env)
            self.assertEqual(p.returncode,3,(path.name,p.stderr))
    def test_privileged_docker_bypass_is_negative_not_qualification(self):
        s=ATTACK.read_text()
        self.assertIn('kill -STOP "$PAUSED_PID"',s)
        self.assertIn('kill -CONT "$PAUSED_PID"',s)
        self.assertIn("R23D2B1S_PRIVILEGED_DOCKER_BYPASS_EXPOSED_FAKE_TCP_NO_GO",s)
        self.assertIn("R23D2B1S_MONITOR_EVENTUAL_RECOVERY_CUT_BYPASSED_PEER_PASS",s)
        self.assertIn("R23D2B1S_PRIVILEGED_API_BYPASS_LAB_PASS_STABLE_RELEASE_HOLD",s)
        self.assertIn("--network none",s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("docker volume rm",s)
        self.assertNotIn("nft flush ruleset",s)
    def test_real_qemu_guest_reboot_is_not_host_reboot(self):
        s=VM.read_text()
        for expected in (
          'qemu-system-x86_64 -enable-kvm',"/dev/kvm",
          'guest \'sudo systemctl reboot\'', "/proc/sys/kernel/random/boot_id",
          '[[ "$BOOT2" != "$BOOT1" ]]',
          "R23D2B1S_ACTUAL_VM_KERNEL_REBOOT_BOOT_ID_CHANGED_PASS",
          "R23D2B1S_GUEST_COLD_BOOT_SYSTEMD_ORDER_AND_DOCKER_HOLD_PASS",
          "R23D2B1S_TRUE_VM_REBOOT_SMOKE_PASS_NOT_A_PRODUCTION_GATEWAY_QUALIFICATION",
          "R23D2B1S_QEMU_VM_OWNED_STORAGE_AND_PROCESS_CLEANUP_PASS"):
            self.assertIn(expected,s)
        self.assertNotIn("sudo systemctl reboot\n",s)
        self.assertNotIn("docker system prune",s)
        self.assertNotIn("nft flush ruleset",s)
    def test_image_checksum_not_unverified_image(self):
        s=VM.read_text()
        self.assertIn("SHA256SUMS",s)
        self.assertIn("sha256sum -c",s)
        self.assertIn("R23D2B1S_OFFICIAL_GUEST_IMAGE_SHA256_VERIFIED_PASS",s)
    def test_ci_runs_only_on_disposable_hosted_runners(self):
        w=CI.read_text()
        for job in ("r23d-privileged-docker-bypass-negative-lab:",
                    "r23d-real-qemu-guest-kernel-reboot-smoke:"):
            self.assertIn(job,w)
        self.assertIn("runs-on: ubuntu-24.04",w)
        self.assertIn("RUNNER_ENVIRONMENT: github-hosted",w)
        self.assertIn("timeout-minutes: 35",w)

if __name__=="__main__":
    unittest.main()
