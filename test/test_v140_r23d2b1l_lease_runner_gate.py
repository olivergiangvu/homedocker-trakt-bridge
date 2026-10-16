"""R23D-2B1L runner-boundary regression; never executes privileged lab."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"probe-v140-r23d2b1l-guardian-crash-ci.sh"
PROBE=ROOT/"scripts"/"probe-v140-r23d2b1k-ci.py"

def blocked(args,override=None):
    env=dict(os.environ)
    env.update({
        "GITHUB_ACTIONS":"false",
        "RUNNER_OS":"Linux",
        "RUNNER_ENVIRONMENT":"not-github-hosted",
        "CI":"false",
    })
    env.update(override or {})
    return subprocess.run(["bash",str(SCRIPT),*args],capture_output=True,
                          timeout=4,env=env)

class FenceLabGuardrails(unittest.TestCase):
    def test_script_bash_syntax(self):
        self.assertEqual(subprocess.run(["bash","-n",str(SCRIPT)],
                       capture_output=True,timeout=3).returncode,0)
    def test_without_explicit_flag_rejected(self):
        r=blocked([])
        self.assertEqual(r.returncode,3)
        self.assertIn(b"STOP_NOT_PRIVILEGED_EPHEMERAL",r.stderr)
    def test_local_environment_rejected_before_any_command(self):
        r=blocked(["--ci-ephemeral-only"])
        self.assertEqual(r.returncode,3)
        self.assertIn(b"STOP_NOT_PRIVILEGED_EPHEMERAL",r.stderr)
    def test_selfhosted_label_rejected(self):
        r=blocked(["--ci-ephemeral-only"],{
            "GITHUB_ACTIONS":"true","RUNNER_OS":"Linux",
            "RUNNER_ENVIRONMENT":"self-hosted","CI":"true"})
        self.assertEqual(r.returncode,3)
    def test_source_scopes_guard_mutations_to_namespace(self):
        source=SCRIPT.read_text(encoding="utf8")
        self.assertIn('ip netns exec "$NS_H" nft --file',source)
        self.assertIn('ip netns exec "$NS_H" nft delete table',source)
        self.assertIn("R23D2B1L_SIGKILL_KERNEL_LEASE_EXPIRY_FAIL_CLOSED_PASS",source)
        self.assertNotIn("nft flush ruleset",source)
        self.assertNotIn("systemctl ",source)
        self.assertNotIn("docker run",source)
    def test_fake_tcp_helper_disallows_nonlab_destination(self):
        spec=importlib.util.spec_from_file_location("fakeprobe",PROBE)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for host,port in (("127.0.0.1",17420),
                          ("8.8.8.8",443),
                          ("172.31.254.1",22)):
            with self.subTest(host=host,port=port):
                with self.assertRaises(module.Stop):
                    module.validate(host,port)

if __name__=="__main__":
    unittest.main()
