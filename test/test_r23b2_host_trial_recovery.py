"""R23B2 crash recovery: no Docker/network/host mutations in unit suite."""
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/host-trial-r23b2.py'
spec = importlib.util.spec_from_file_location('r23b2_crash_recovery', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NONCE = 'a' * 32
IMAGE = 'sha256:' + 'b' * 64
CANARY_ID = 'c' * 64
BOOT = '123e4567-e89b-12d3-a456-426614174000'


def receipt(**kwargs):
    r = {'version': 1, 'nonce': NONCE, 'image_id': IMAGE,
         'canary_id': CANARY_ID, 'boot_id': BOOT,
         'host_pid': None, 'host_start_ticks': None, 'ttl_seconds': 120}
    r.update(kwargs)
    return r


def sidecar(nonce=NONCE, image=IMAGE, mode=None):
    return {'Name': '/' + mod.SIDECAR, 'Image': image,
            'Config': {'Labels': {'homedocker.phase': 'r23b2-unix-get-trial',
                        'homedocker.r23b2.nonce': nonce}, 'User': '65534:65534'},
            'HostConfig': {'NetworkMode': mode or 'container:' + mod.CANARY,
                           'Privileged': False, 'ReadonlyRootfs': True,
                           'RestartPolicy': {'Name': 'no'}},
            'Mounts': [{'Source': str(mod.SOCKET_DIR),
                        'Destination': str(mod.SOCKET_DIR), 'RW': False}]}


class CrashRecovery(unittest.TestCase):
    def test_schema_rejects_bad_nonce_image_version_pid(self):
        for altered in (dict(nonce='../../etc'), dict(image_id='fake'),
                        dict(version=2), dict(host_pid=10),
                        dict(host_pid=10, host_start_ticks=0),
                        dict(ttl_seconds=901), dict(canary_id='wrong')):
            with self.subTest(altered=altered):
                with self.assertRaises(mod.TrialStop):
                    mod._receipt_fields(receipt(**altered))
        self.assertEqual(mod._receipt_fields(receipt())['nonce'], NONCE)

    def test_recovery_cli_is_read_only_by_default(self):
        with (mock.patch.object(sys, 'argv', ['launcher', '--recover']),
              mock.patch.object(mod, '_recover_trial') as recover,
              mock.patch.object(mod, 'preflight') as preflight,
              mock.patch.object(mod, 'trial') as apply):
            mod.main()
        recover.assert_called_once_with(apply=False)
        preflight.assert_not_called()
        apply.assert_not_called()

    def test_recovery_requires_separate_exact_confirm(self):
        for args in (['--recover', '--confirm', mod.CONFIRM],
                     ['--recover', '--confirm', 'incorrect'],
                     ['--apply', '--recover']):
            with self.subTest(args=args), mock.patch.object(sys, 'argv', ['launcher'] + args):
                with self.assertRaises((mod.TrialStop, SystemExit)):
                    mod.main()
        with (mock.patch.object(sys, 'argv', ['launcher', '--recover', '--confirm', mod.RECOVER_CONFIRM]),
              mock.patch.object(mod, '_recover_trial') as recover):
            mod.main()
        recover.assert_called_once_with(apply=True)

    def test_recovery_read_only_prints_only_safe_marker(self):
        with (mock.patch.object(mod.os, 'geteuid', return_value=0),
              mock.patch.object(mod, 'read_receipt', return_value=receipt()),
              mock.patch.object(mod, 'inspect_container', return_value=sidecar()),
              mock.patch.object(mod, 'remove_owned_sidecar') as rm,
              mock.patch.object(mod, '_stop_orphan_host') as stop,
              mock.patch.object(mod, '_safe_receipt_remove') as clear,
              redirect_stdout(io.StringIO()) as out):
            mod._recover_trial()
        self.assertIn('INSPECT_ONLY', out.getvalue())
        self.assertNotIn(NONCE, out.getvalue())
        rm.assert_not_called()
        stop.assert_not_called()
        clear.assert_not_called()

    def test_recovery_refuses_foreign_sidecar(self):
        with (mock.patch.object(mod.os, 'geteuid', return_value=0),
              mock.patch.object(mod, 'read_receipt', return_value=receipt()),
              mock.patch.object(mod, 'inspect_container', return_value=sidecar(nonce='d'*32)),
              mock.patch.object(mod, 'remove_owned_sidecar') as rm,
              mock.patch.object(mod, '_stop_orphan_host') as stop):
            with self.assertRaisesRegex(mod.TrialStop, 'SIDECAR_OWNER_MISMATCH'):
                mod._recover_trial(apply=True)
        rm.assert_not_called()
        stop.assert_not_called()

    def test_recovery_refuses_sidecar_different_image_or_mount(self):
        c = sidecar(image='sha256:'+'e'*64)
        self.assertFalse(mod._sidecar_owned_by_receipt(c, receipt()))
        c = sidecar()
        c['Mounts'][0]['RW'] = True
        self.assertFalse(mod._sidecar_owned_by_receipt(c, receipt()))

    def test_recovery_owner_cleanup_happy_path(self):
        with (mock.patch.object(mod.os, 'geteuid', return_value=0),
              mock.patch.object(mod, 'read_receipt', return_value=receipt()),
              mock.patch.object(mod, 'inspect_container', side_effect=[sidecar(), None]),
              mock.patch.object(mod, 'remove_owned_sidecar', return_value=None) as rm,
              mock.patch.object(mod, '_stop_orphan_host', return_value=None) as stop,
              mock.patch.object(mod.os.path, 'lexists', side_effect=[True, False]),
              mock.patch.object(mod, 'remove_owned_dir', return_value=None) as dir_rm,
              mock.patch.object(mod, '_safe_receipt_remove') as clear,
              redirect_stdout(io.StringIO()) as out):
            mod._recover_trial(apply=True)
        rm.assert_called_once_with(NONCE, receipt())
        stop.assert_called_once_with(receipt())
        dir_rm.assert_called_once_with(True)
        clear.assert_called_once_with(receipt())
        self.assertIn('RECOVERY_COMPLETE', out.getvalue())

    def test_recovery_cleanup_failure_does_not_delete_socket_or_receipt(self):
        with (mock.patch.object(mod.os, 'geteuid', return_value=0),
              mock.patch.object(mod, 'read_receipt', return_value=receipt()),
              mock.patch.object(mod, 'inspect_container', return_value=sidecar()),
              mock.patch.object(mod, 'remove_owned_sidecar', return_value='SIDECAR_CLEANUP_FAILED'),
              mock.patch.object(mod, '_stop_orphan_host') as stop,
              mock.patch.object(mod, 'remove_owned_dir') as dir_rm,
              mock.patch.object(mod, '_safe_receipt_remove') as clear):
            with self.assertRaises(mod.TrialStop):
                mod._recover_trial(apply=True)
        stop.assert_not_called()
        dir_rm.assert_not_called()
        clear.assert_not_called()

    def test_host_pid_reuse_blocks_pidfd_and_signal(self):
        with (mock.patch.object(mod, '_process_identity', return_value=('mismatch', None)),
              mock.patch.object(mod.os, 'pidfd_open') as pidfd):
            self.assertIn('MANUAL_REVIEW', mod._stop_orphan_host(receipt(host_pid=42, host_start_ticks=10)))
        pidfd.assert_not_called()

    def test_previous_receipt_blocks_fresh_trial_even_if_socket_absent(self):
        with (mock.patch.object(mod.os, 'geteuid', return_value=0),
              mock.patch.object(mod.os.path, 'lexists', return_value=True),
              mock.patch.object(mod, 'command') as cmd):
            with self.assertRaisesRegex(mod.TrialStop, 'PREVIOUS_TRIAL_RECEIPT'):
                mod.preflight()
        cmd.assert_not_called()

    def test_known_process_reboot_is_not_killed(self):
        with (mock.patch.object(mod, '_boot_id', return_value='other-boot'),
              mock.patch.object(mod, '_owned_relay_active', return_value=False),
              mock.patch.object(mod.os, 'pidfd_open') as pidfd):
            self.assertIsNone(mod._stop_orphan_host(receipt(host_pid=123, host_start_ticks=456)))
        pidfd.assert_not_called()

    def test_untracked_live_socket_refuses_cleanup(self):
        with (mock.patch.object(mod, '_process_identity', return_value=('absent', None)),
              mock.patch.object(mod, '_owned_relay_active', return_value=True)):
            self.assertIn('UNTRACKED_HOST_RELAY', mod._stop_orphan_host(receipt()))

if __name__ == '__main__':
    unittest.main()
