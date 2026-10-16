"""No host/network mutation: R23B2 authenticated trial operator tests."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/run-r23b2-authenticated-readonly-trial.sh"
B1V = ROOT / "scripts/audit-v140-r23d2b1v-real-client-readonly.py"

class AuthenticatedObserver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WRAPPER.read_text()
        cls.b1v = B1V.read_text()

    def test_bash_syntax_valid(self):
        proc = subprocess.run(["bash","-n",str(WRAPPER)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)

    def test_no_args_refuses_without_creating_resources(self):
        p=subprocess.run(["bash",str(WRAPPER)],capture_output=True,text=True)
        self.assertEqual(p.returncode,3)
        self.assertIn("EXPLICIT_MODE_AND_CONFIRM_REQUIRED",p.stderr)

    def test_incorrect_confirm_refused_for_both_modes(self):
        for mode in ("existing-token","login"):
            with self.subTest(mode=mode):
                p=subprocess.run(["bash",str(WRAPPER),"--mode",mode,
                  "--confirm","WRONG"],capture_output=True,text=True)
                self.assertEqual(p.returncode,3)
                self.assertIn("EXPLICIT_MODE_AND_CONFIRM_REQUIRED",p.stderr)

    def test_only_pinned_launcher_and_b1v(self):
        for marker in ('host-trial-r23b2.py','audit-v140-r23d2b1v-real-client-readonly.py',
           'R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY','--ttl-seconds 360',
           'R23B2_PHASE_READY','seq 1 200','timeout --foreground',
           '240s'):
            self.assertIn(marker,self.source)

    def test_credentials_hidden_and_demoted(self):
        for marker in ('setpriv --reuid "$OP_UID" --regid "$OP_GID" --clear-groups',
          'env -i PATH=','HOME="$OP_HOME"','! -t 0',
          'PYTHONDONTWRITEBYTECODE=1',
          'args+=(--existing-token --confirm LIVE_READONLY_GET_ONLY)',
          'args+=(--confirm LIVE_AUTH_LOGIN_GET_ONLY)',
          '__B1V_ARGS__'):
            self.assertIn(marker.replace('__B1V_ARGS__', 'python3 -B "$B1V" ' + chr(34) + chr(36) + '{args[@]}' + chr(34)),self.source)
        self.assertIn('getpass.getpass',self.b1v)
        for forbidden in ('--password','--token=','set -x','bash -x','X-Emby-Token',
          'TRAKT_CLIENT_SECRET','AIO_PASSWORD'):
            self.assertNotIn(forbidden,self.source)

    def test_exact_rollback_and_state_confirmation(self):
        for marker in ('kill -TERM "$JOB_PID"','R23B2_SIGNAL_CLEANUP_REQUESTED',
          'R23B2_OWNED_RESOURCES_CLEANUP_PASS','R23B2_AUTH_POST_PREFLIGHT_PASS',
          'R23B2_AUTH_STOP_ORIGINAL_CANARY_DRIFT',
          'R23B2_AUTH_STOP_SIDECAR_LEFTOVER',
          'R23B2_AUTH_STOP_SOCKET_OR_RECEIPT_LEFTOVER',
          'R23B2_AUTHENTICATED_NEXTUP_COMPARE_AND_ROLLBACK_PASS'):
            self.assertIn(marker,self.source)

    def test_never_mutates_production_services(self):
        for prohibited in ('docker rm -f','docker network connect','docker system prune',
             'docker volume rm','nft flush','iptables -F','systemctl restart',
             'nginx -s','/var/run/docker.sock','--include-resume'):
            self.assertNotIn(prohibited,self.source)

if __name__=="__main__":
    unittest.main()
