"""Non-host-mutating regression for opt-in R23B2 operator launcher."""
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/host-trial-r23b2.py'
spec = importlib.util.spec_from_file_location('r23b2_host_trial', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class LauncherSafety(unittest.TestCase):
    def test_dry_run_no_trial(self):
        with (mock.patch.object(sys, 'argv', ['launcher']),
              mock.patch.object(mod, 'preflight', return_value=({}, 'sha256:demo')) as check,
              mock.patch.object(mod, 'trial') as execute,
              redirect_stdout(io.StringIO()) as output):
            mod.main()
        check.assert_called_once()
        execute.assert_not_called()
        self.assertIn('DRY_RUN_READY_NO_CHANGES', output.getvalue())

    def test_reject_unconfirmed_and_invalid_ttl(self):
        for args in (['--apply'], ['--apply', '--confirm', 'wrong'],
                     ['--ttl-seconds', '901'], ['--ttl-seconds', '59'],
                     ['--confirm', mod.CONFIRM]):
            with (self.subTest(args=args), mock.patch.object(sys, 'argv', ['launcher'] + args),
                  mock.patch.object(mod, 'preflight') as check,
                  mock.patch.object(mod, 'trial') as execute):
                with self.assertRaises(mod.TrialStop):
                    mod.main()
                check.assert_not_called()
                execute.assert_not_called()

    def test_exact_confirm_calls_only_trial(self):
        with (mock.patch.object(sys, 'argv', ['launcher', '--apply', '--confirm',
                                            mod.CONFIRM, '--ttl-seconds', '120']),
              mock.patch.object(mod, 'trial') as execute):
            mod.main()
        execute.assert_called_once_with(120)

    def test_owned_container_refuses_other_nonce(self):
        obj = {'Name':'/' + mod.SIDECAR, 'Config':{'Labels':{'homedocker.phase':'r23b2-unix-get-trial',
             'homedocker.r23b2.nonce':'first'}}}
        self.assertTrue(mod.sidecar_owned(obj, 'first'))
        self.assertFalse(mod.sidecar_owned(obj, 'second'))

    def test_cleanup_refuses_different_owner(self):
        obj = {'Name':'/' + mod.SIDECAR, 'Config':{'Labels':{'homedocker.phase':'different',
             'homedocker.r23b2.nonce':'different'}}}
        with (mock.patch.object(mod, 'inspect_container', return_value=obj),
              mock.patch.object(mod, 'command') as cmd):
            self.assertEqual(mod.remove_owned_sidecar('new'), 'SIDECAR_OWNERSHIP_MISMATCH_MANUAL_REVIEW')
            cmd.assert_not_called()

    def test_cleanup_recovers_owned_failed_create(self):
        obj = {'Name':'/' + mod.SIDECAR, 'Id':'sha256:fake',
               'Config':{'Labels':{'homedocker.phase':'r23b2-unix-get-trial',
                      'homedocker.r23b2.nonce':'nonce'}}}
        calls = iter([obj, obj, None])
        with (mock.patch.object(mod, 'inspect_container', side_effect=lambda *a, **kw: next(calls)),
              mock.patch.object(mod, 'command', return_value=(0,'')) as cmd):
            self.assertIsNone(mod.remove_owned_sidecar('nonce'))
            argv = [c.args[:3] for c in cmd.call_args_list]
            self.assertIn(('docker','stop','--time'), argv)
            self.assertIn(('docker','rm','-f'), argv)

    def test_never_targets_other_host_resources(self):
        src = SCRIPT.read_text()
        for forbidden in ('docker prune', 'docker system prune', 'docker network connect',
                          'nginx -s', 'nft flush', 'trakt.tv', 'sqlite3.connect'):
            self.assertNotIn(forbidden, src)

if __name__ == '__main__':
    unittest.main()
