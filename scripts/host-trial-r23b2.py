#!/usr/bin/env python3
"""R23B2: guarded, temporary GET-only AIO Unix relay for existing R23B.

No permanent service, production route, Trakt, AIO DB or nginx changes.
Dry-run by default. The opt-in trial is deliberately time bounded.
Only use from an exact reviewed Git checkout with the two sibling R23B2 scripts.
"""
import argparse
import re
import socket
import select
import json
import os
import pwd
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PREFLIGHT = SCRIPTS / 'audit-r23b2-host-relay-prereqs.py'
RELAY = SCRIPTS / 'r23b2-experimental-aio-unix-get-relay.py'
CANARY = 'homedocker-v140-r23b-canary'
SIDECAR = 'homedocker-v140-r23b2-unix-sidecar'
SOCKET_DIR = Path('/run/homedocker-r23b2-aio')
SOCKET = SOCKET_DIR / 'readonly.sock'
STAGED_RELAY = SOCKET_DIR / 'relay.py'
IMAGE = 'python:3.12-alpine'
CONFIRM = 'R23B2_EPHEMERAL_READONLY_UNIX_RELAY_ONLY'
RECOVER_CONFIRM = 'R23B2_EXACT_OWNER_RECOVERY_ONLY'
STATE_DIR = Path('/var/lib/homedocker-r23b2-trial')
RECEIPT = STATE_DIR / 'receipt.json'
STATE_VERSION = 1

class TrialStop(Exception):
    pass

def _boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def _receipt_fields(receipt):
    # Persistent metadata never contains an AIO token, Docker environment or URL.
    if (not isinstance(receipt, dict) or set(receipt) != {
        'version', 'nonce', 'image_id', 'canary_id', 'boot_id',
        'host_pid', 'host_start_ticks', 'launcher_pid',
        'launcher_start_ticks', 'ttl_seconds'}
        or receipt['version'] != STATE_VERSION
        or not isinstance(receipt['nonce'], str)
        or re.fullmatch(r'[a-f0-9]{32}', receipt['nonce']) is None
        or not isinstance(receipt['image_id'], str)
        or re.fullmatch(r'sha256:[a-f0-9]{64}', receipt['image_id']) is None
        or not isinstance(receipt['canary_id'], str)
        or re.fullmatch(r'[a-f0-9]{64}', receipt['canary_id']) is None
        or not isinstance(receipt['boot_id'], str)
        or re.fullmatch(r'[a-f0-9-]{36}', receipt['boot_id']) is None
        or type(receipt['launcher_pid']) is not int
        or receipt['launcher_pid'] < 2
        or type(receipt['launcher_start_ticks']) is not int
        or receipt['launcher_start_ticks'] < 1
        or not isinstance(receipt['ttl_seconds'], int)
        or not 60 <= receipt['ttl_seconds'] <= 900
        or (receipt['host_pid'] is None) != (receipt['host_start_ticks'] is None)
        or (receipt['host_pid'] is not None and (
            type(receipt['host_pid']) is not int or receipt['host_pid'] < 2
            or type(receipt['host_start_ticks']) is not int
            or receipt['host_start_ticks'] < 1))):
        raise TrialStop('ROOT_RECEIPT_SCHEMA_INVALID_MANUAL_REVIEW')
    return receipt

def _require_root_receipt_dir():
    try:
        st = STATE_DIR.lstat()
        st2 = RECEIPT.lstat()
    except FileNotFoundError as exc:
        raise TrialStop('ROOT_RECEIPT_NOT_FOUND') from exc
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0
        or stat.S_IMODE(st.st_mode) != 0o700
        or not stat.S_ISREG(st2.st_mode) or st2.st_uid != 0
        or stat.S_IMODE(st2.st_mode) != 0o600):
        raise TrialStop('ROOT_RECEIPT_OWNERSHIP_OR_MODE_DRIFT')

def read_receipt():
    _require_root_receipt_dir()
    try:
        # O_NOFOLLOW guards against symlink substitution.
        fd = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as f:
            raw = f.read(4097)
        if len(raw) > 4096:
            raise ValueError('large receipt')
        return _receipt_fields(json.loads(raw))
    except (OSError, ValueError, UnicodeError) as exc:
        raise TrialStop('ROOT_RECEIPT_UNREADABLE_MANUAL_REVIEW') from exc

def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def _write_receipt_atomic(r, first=False):
    _receipt_fields(r)
    data = (json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if first:
        fd = os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        dst = RECEIPT
    else:
        dst = STATE_DIR / ('receipt-next-' + r['nonce'])
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if not first:
        os.replace(dst, RECEIPT)
    _fsync_dir(STATE_DIR)

def create_receipt(nonce, canary, image_id, ttl):
    if os.path.lexists(STATE_DIR):
        raise TrialStop('PREVIOUS_TRIAL_RECEIPT_PRESENT_USE_RECOVER')
    STATE_DIR.mkdir(mode=0o700)
    # All entries must be root-owned and permission-restricted.
    os.chmod(STATE_DIR, 0o700)
    receipt = {
        'version': STATE_VERSION, 'nonce': nonce, 'canary_id': canary['Id'],
        'image_id': image_id, 'boot_id': _boot_id(),
        'host_pid': None, 'host_start_ticks': None,
        'launcher_pid': os.getpid(), 'launcher_start_ticks': _start_ticks(os.getpid()),
        'ttl_seconds': ttl}
    _write_receipt_atomic(receipt, first=True)
    return receipt

def _start_ticks(pid):
    try:
        stat_data = (Path('/proc') / str(pid) / 'stat').read_text()
        return int(stat_data[stat_data.rfind(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None

def _set_host_pid(receipt, pid):
    ticks = _start_ticks(pid)
    if ticks is None:
        raise TrialStop('HOST_RELAY_PID_CANNOT_BE_ATTESTED')
    receipt = dict(receipt, host_pid=pid, host_start_ticks=ticks)
    _write_receipt_atomic(receipt)
    return receipt

def _safe_receipt_remove(receipt):
    if read_receipt() != receipt:
        raise TrialStop('ROOT_RECEIPT_CONTENT_CHANGED')
    expected = {'receipt.json', 'receipt-next-' + receipt['nonce']}
    if {p.name for p in STATE_DIR.iterdir()} - expected:
        raise TrialStop('UNEXPECTED_ROOT_JOURNAL_FILES_MANUAL_REVIEW')
    temporary = STATE_DIR / ('receipt-next-' + receipt['nonce'])
    if temporary.exists():
        st = temporary.lstat()
        if not (stat.S_ISREG(st.st_mode) and st.st_uid == 0
                and stat.S_IMODE(st.st_mode) == 0o600):
            raise TrialStop('ROOT_RECEIPT_TEMP_DRIFT')
        temporary.unlink()
    RECEIPT.unlink()
    STATE_DIR.rmdir()

def _process_identity(receipt):
    # No host PID may be killed without same-boot, same-PID-starttime,
    # UID and exact staged relay command evidence.
    pid = receipt['host_pid']
    if pid is None or receipt['boot_id'] != _boot_id():
        return 'absent', None
    root = Path('/proc') / str(pid)
    if not root.exists():
        return 'absent', None
    if (_start_ticks(pid) != receipt['host_start_ticks']
        or root.stat().st_uid != 65534):
        return 'mismatch', None
    try:
        cmdline = (root / 'cmdline').read_bytes().split(b'\0')
    except OSError:
        return 'mismatch', None
    if not (str(STAGED_RELAY).encode() in cmdline
            and b'--role' in cmdline and b'host' in cmdline
            and b'--ttl-seconds' in cmdline
            and str(receipt['ttl_seconds']).encode() in cmdline):
        return 'mismatch', None
    return 'running', pid

def _owned_relay_active():
    try:
        st = SOCKET.lstat()
    except FileNotFoundError:
        return False
    if not (stat.S_ISSOCK(st.st_mode) and st.st_uid == 65534):
        raise TrialStop('UNIX_SOCKET_DRIFT_MANUAL_REVIEW')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(.4)
        return sock.connect_ex(str(SOCKET)) == 0

def _stop_orphan_host(receipt):
    status, pid = _process_identity(receipt)
    if status == 'mismatch':
        return 'HOST_PID_REUSED_OR_COMMAND_DRIFT_MANUAL_REVIEW'
    if status == 'absent':
        if _owned_relay_active():
            return 'UNTRACKED_HOST_RELAY_ALIVE_WAIT_TTL_MANUAL_REVIEW'
        return None
    # pidfd binds the *same* process despite numerical PID reuse.
    if not (hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')):
        return 'PIDFD_NOT_AVAILABLE_MANUAL_REVIEW'
    try:
        pidfd = os.pidfd_open(pid)
        try:
            if _process_identity(receipt)[0] != 'running':
                return 'HOST_PID_REATTESTATION_FAILED_MANUAL_REVIEW'
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            if not poller.poll(2500):
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                if not poller.poll(2500):
                    return 'HOST_PROCESS_COULD_NOT_BE_STOPPED_MANUAL_REVIEW'
        finally:
            os.close(pidfd)
    except (OSError, ProcessLookupError):
        if _process_identity(receipt)[0] != 'absent':
            return 'HOST_PROCESS_SIGNAL_FAILED_MANUAL_REVIEW'
    if _owned_relay_active():
        return 'HOST_SOCKET_STILL_ACTIVE_MANUAL_REVIEW'
    return None

def _controller_still_alive(receipt):
    if receipt['boot_id'] != _boot_id():
        return False
    pid = receipt['launcher_pid']
    return _start_ticks(pid) == receipt['launcher_start_ticks']

def _sidecar_owned_by_receipt(c, r):
    if not sidecar_owned(c, r['nonce']):
        return False
    h = c.get('HostConfig') or {}
    mounts = c.get('Mounts') or []
    return (c.get('Image') == r['image_id']
            and h.get('NetworkMode') in ('container:' + CANARY,
                                         'container:' + r['canary_id'])
            and h.get('Privileged') is False
            and h.get('ReadonlyRootfs') is True
            and h.get('RestartPolicy', {}).get('Name') == 'no'
            and c.get('Config', {}).get('User') == '65534:65534'
            and len(mounts) == 1
            and mounts[0].get('Source') == str(SOCKET_DIR)
            and mounts[0].get('Destination') == str(SOCKET_DIR)
            and mounts[0].get('RW') is False)

def _recover_trial(apply=False):
    if os.geteuid() != 0:
        raise TrialStop('SUDO_ROOT_REQUIRED')
    r = read_receipt()
    if _controller_still_alive(r):
        raise TrialStop('ORIGINAL_TRIAL_CONTROLLER_STILL_ALIVE')
    c = inspect_container(SIDECAR, required=False)
    if c is not None and not _sidecar_owned_by_receipt(c, r):
        raise TrialStop('SIDECAR_OWNER_MISMATCH_MANUAL_REVIEW')
    if not apply:
        print('R23B2_RECOVERY_INSPECT_ONLY_RECEIPT_VALID_NO_CHANGES')
        return
    if c is not None:
        err = remove_owned_sidecar(r['nonce'], r)
        if err:
            raise TrialStop(err)
    err = _stop_orphan_host(r)
    if err:
        raise TrialStop(err)
    if os.path.lexists(SOCKET_DIR):
        err = remove_owned_dir(True)
        if err:
            raise TrialStop(err)
    if inspect_container(SIDECAR, required=False) is not None or os.path.lexists(SOCKET_DIR):
        raise TrialStop('RECOVERY_LEFTOVER_RESOURCE_MANUAL_REVIEW')
    _safe_receipt_remove(r)
    print('R23B2_EXACT_OWNER_RECOVERY_COMPLETE')

def command(*argv, timeout=18, allow_failure=False):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TrialStop('COMMAND_UNAVAILABLE_OR_TIMEOUT_' + str(argv[0])) from e
    if p.returncode and not allow_failure:
        # Never echo stderr, stdout, Docker env, URLs or credentials.
        raise TrialStop('COMMAND_FAILED_' + str(argv[0]))
    return p.returncode, p.stdout.strip()

def inspect_container(name, required=True):
    rc, out = command('docker', 'container', 'inspect', name, allow_failure=True)
    if rc:
        if required:
            raise TrialStop('CONTAINER_NOT_FOUND')
        return None
    try:
        objects = json.loads(out)
        if isinstance(objects, list) and len(objects) == 1 and isinstance(objects[0], dict):
            return objects[0]
    except ValueError:
        pass
    raise TrialStop('DOCKER_INSPECT_INVALID')

def fingerprint(c):
    # Deliberately never serialize or display the protected environment.
    return (c['Id'], c['Image'], c['State']['Pid'], c['Config'],
            c['HostConfig'], c['NetworkSettings']['Networks'])

def preflight():
    if os.geteuid() != 0:
        raise TrialStop('SUDO_ROOT_REQUIRED')
    if os.path.lexists(STATE_DIR):
        raise TrialStop('PREVIOUS_TRIAL_RECEIPT_PRESENT_USE_RECOVER')
    if not PREFLIGHT.is_file() or not RELAY.is_file():
        raise TrialStop('PINNED_SIBLING_SCRIPTS_MISSING')
    rc, raw = command(sys.executable, '-B', str(PREFLIGHT), timeout=45)
    try:
        report = json.loads(raw)
    except ValueError as e:
        raise TrialStop('PREFLIGHT_JSON_INVALID') from e
    if (report.get('audit') != 'R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT'
        or report.get('trial_environment_ready') is not True
        or report.get('host_resources_modified') != 0
        or report.get('production_route_modified') is not False):
        raise TrialStop('HOST_READONLY_PREFLIGHT_NOT_READY')
    if inspect_container(SIDECAR, required=False) is not None:
        raise TrialStop('EXACT_SIDECAR_NAME_COLLISION')
    if os.path.lexists(SOCKET_DIR):
        raise TrialStop('SOCKET_DIRECTORY_COLLISION')
    rc, raw = command('docker', 'image', 'inspect', IMAGE)
    try:
        images = json.loads(raw)
        image_id = images[0]['Id'] if len(images) == 1 else None
    except (ValueError, KeyError, TypeError):
        image_id = None
    if not isinstance(image_id, str) or not image_id.startswith('sha256:'):
        raise TrialStop('LOCAL_PYTHON_IMAGE_UNVERIFIED')
    canary = inspect_container(CANARY)
    if not canary.get('State', {}).get('Running') or canary['State'].get('Pid', 0) < 2:
        raise TrialStop('EXISTING_CANARY_NOT_RUNNING')
    if pwd.getpwnam('nobody').pw_uid != 65534:
        raise TrialStop('UID_65534_NOT_AVAILABLE')
    for tool in ('setpriv', 'nsenter', 'ss'):
        if shutil.which(tool) is None:
            raise TrialStop('REQUIRED_HOST_BINARY_MISSING_' + tool)
    return canary, image_id

def sidecar_owned(c, nonce):
    labels = c.get('Config', {}).get('Labels') or {}
    return (c.get('Name') == '/' + SIDECAR
            and labels.get('homedocker.phase') == 'r23b2-unix-get-trial'
            and labels.get('homedocker.r23b2.nonce') == nonce)

def stage_owned_file():
    os.chown(SOCKET_DIR, 65534, 65534)
    os.chmod(SOCKET_DIR, 0o700)
    fd = os.open(STAGED_RELAY, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600)
    with os.fdopen(fd, 'wb') as dst, RELAY.open('rb') as src:
        shutil.copyfileobj(src, dst)
    os.chown(STAGED_RELAY, 65534, 65534)
    os.chmod(STAGED_RELAY, 0o600)

def wait_for_socket(host, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if host.poll() is not None:
            raise TrialStop('HOST_RELAY_EARLY_EXIT')
        try:
            st = SOCKET.lstat()
        except FileNotFoundError:
            time.sleep(.1)
            continue
        if (stat.S_ISSOCK(st.st_mode) and st.st_uid == 65534
            and stat.S_IMODE(st.st_mode) == 0o600):
            return
        raise TrialStop('UNIX_SOCKET_OWNERSHIP_DRIFT')
    raise TrialStop('UNIX_SOCKET_STARTUP_TIMEOUT')

def wait_netns_listener(pid, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rc, output = command('nsenter', '-t', str(pid), '-n',
                             'ss', '-H', '-lnt', '( sport = :9999 )',
                             timeout=5, allow_failure=True)
        if rc == 0 and '127.0.0.1:9999' in output:
            return
        time.sleep(.15)
    raise TrialStop('SIDECAR_LOOPBACK_LISTENER_NOT_READY')

def check_sidecar(c, image_id, nonce):
    h = c['HostConfig']
    m = c.get('Mounts') or []
    if not (sidecar_owned(c, nonce)
            and c['Image'] == image_id
            and h.get('NetworkMode') in ('container:' + CANARY,
                                        'container:' + inspect_container(CANARY)['Id'])
            and h.get('Privileged') is False
            and h.get('ReadonlyRootfs') is True
            and h.get('RestartPolicy', {}).get('Name') == 'no'
            and h.get('UsernsMode') in ('', None)
            and c['Config']['User'] == '65534:65534'
            and len(m) == 1
            and m[0]['Source'] == str(SOCKET_DIR)
            and m[0]['Destination'] == str(SOCKET_DIR)
            and m[0]['RW'] is False):
        raise TrialStop('SIDECAR_SECURITY_CONTRACT_DRIFT')

def remove_owned_sidecar(nonce, receipt=None):
    """Return error code, not an exception, so other cleanup always runs."""
    try:
        c = inspect_container(SIDECAR, required=False)
        if c is None:
            return None
        if not (sidecar_owned(c, nonce) and (receipt is None or _sidecar_owned_by_receipt(c, receipt))):
            return 'SIDECAR_OWNERSHIP_MISMATCH_MANUAL_REVIEW'
        identifier = c['Id']
        # ID-specific calls; no broad prune and no other container targets.
        command('docker', 'stop', '--time', '2', identifier, timeout=16,
                allow_failure=True)
        if inspect_container(identifier, required=False) is not None:
            command('docker', 'rm', '-f', identifier, timeout=16)
        if inspect_container(SIDECAR, required=False) is not None:
            return 'SIDECAR_LEFTOVER_MANUAL_REVIEW'
        return None
    except (TrialStop, KeyError, ValueError):
        return 'SIDECAR_CLEANUP_FAILED_MANUAL_REVIEW'

def remove_owned_dir(created):
    if not created:
        return None
    try:
        st = SOCKET_DIR.lstat()
        # A failure during chown can leave the directory root-owned; it
        # was created exclusively by this process and is still ours to clean.
        if not (stat.S_ISDIR(st.st_mode) and st.st_uid in (0, 65534)
                and stat.S_IMODE(st.st_mode) == 0o700):
            return 'DIRECTORY_OWNER_OR_MODE_DRIFT_MANUAL_REVIEW'
        if {p.name for p in SOCKET_DIR.iterdir()} - {'relay.py', 'readonly.sock'}:
            return 'UNEXPECTED_FILES_IN_DIRECTORY_MANUAL_REVIEW'
        for path, expected in ((SOCKET, stat.S_ISSOCK), (STAGED_RELAY, stat.S_ISREG)):
            try:
                st = path.lstat()
            except FileNotFoundError:
                continue
            if not expected(st.st_mode) or st.st_uid != 65534:
                return 'OWNED_SOCKET_OR_FILE_DRIFT_MANUAL_REVIEW'
            path.unlink()
        SOCKET_DIR.rmdir()
        return None
    except OSError:
        return 'DIRECTORY_CLEANUP_FAILED_MANUAL_REVIEW'

def stop_host(host):
    if host is None or host.poll() is not None:
        return None
    try:
        host.terminate()
        try:
            host.wait(timeout=4)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=4)
        return None
    except OSError:
        return 'HOST_RELAY_TERMINATION_FAILED_MANUAL_REVIEW'

def trial(ttl):
    canary, image_id = preflight()
    original = fingerprint(canary)
    nonce = secrets.token_hex(16)
    directory_created = False
    host = None
    errors = []
    receipt = create_receipt(nonce, canary, image_id, ttl)
    try:
        SOCKET_DIR.mkdir(mode=0o700)
        directory_created = True
        stage_owned_file()
        host = subprocess.Popen(
            ['setpriv', '--reuid', '65534', '--regid', '65534', '--clear-groups',
             sys.executable, '-B', str(STAGED_RELAY), '--role', 'host',
             '--ttl-seconds', str(ttl)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        receipt = _set_host_pid(receipt, host.pid)
        wait_for_socket(host)
        # 'create' first, before 'start': enables exact recovery if a Docker start fails.
        # Even a failed 'create' is recoverable via name + unguessable owned nonce.
        command('docker', 'create', '--name', SIDECAR, '--restart', 'no',
                '--pull', 'never', '--network', 'container:' + CANARY,
                '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--pids-limit', '64',
                '--memory', '96m', '--label', 'homedocker.phase=r23b2-unix-get-trial',
                '--label', 'homedocker.r23b2.nonce=' + nonce,
                '--mount', 'type=bind,src=' + str(SOCKET_DIR) +
                           ',dst=' + str(SOCKET_DIR) + ',readonly',
                image_id, 'python3', '-B', str(STAGED_RELAY),
                '--role', 'sidecar', '--ttl-seconds', str(ttl), timeout=35)
        created = inspect_container(SIDECAR)
        check_sidecar(created, image_id, nonce)
        command('docker', 'start', created['Id'], timeout=30)
        current = inspect_container(created['Id'])
        check_sidecar(current, image_id, nonce)
        if not current.get('State', {}).get('Running'):
            raise TrialStop('SIDECAR_NOT_RUNNING')
        wait_netns_listener(canary['State']['Pid'])
        if fingerprint(inspect_container(CANARY)) != original:
            raise TrialStop('R23B_CANARY_DRIFT')
        print(json.dumps({'status': 'R23B2_TEMPORARY_RELAY_RUNNING',
             'ttl_seconds': ttl, 'resume_enabled': False,
             'trakt_or_aio_history_writes_requested': 0,
             'nginx_or_production_route_modified': False,
             'instruction': 'Run approved client GET comparison from a separate SSH session. Keep this SSH session open.'}))
        deadline = time.monotonic() + max(1, ttl - 8)
        while time.monotonic() < deadline:
            if host.poll() is not None:
                raise TrialStop('HOST_RELAY_EXITED_EARLY')
            if not inspect_container(SIDECAR).get('State', {}).get('Running'):
                raise TrialStop('SIDECAR_EXITED_EARLY')
            time.sleep(min(2, max(.1, deadline - time.monotonic())))
    finally:
        err = remove_owned_sidecar(nonce, receipt)
        if err: errors.append(err)
        err = stop_host(host)
        if err: errors.append(err)
        if not errors:
            err = remove_owned_dir(directory_created)
            if err: errors.append(err)
        try:
            if fingerprint(inspect_container(CANARY)) != original:
                errors.append('R23B_CANARY_CHANGED_MANUAL_REVIEW')
        except (TrialStop, KeyError, ValueError):
            errors.append('R23B_CANARY_UNVERIFIABLE_MANUAL_REVIEW')
        if not errors:
            try:
                _safe_receipt_remove(receipt)
            except TrialStop:
                errors.append('RECEIPT_CLEANUP_FAILED_MANUAL_REVIEW')
        if errors:
            # These codes reveal no config, IDs, credentials or private networking.
            raise TrialStop('CLEANUP_INCOMPLETE_' + '_'.join(errors))
        print('R23B2_OWNED_RESOURCES_CLEANUP_PASS')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--recover', action='store_true')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--ttl-seconds', type=int, default=600)
    a = parser.parse_args()
    if not 60 <= a.ttl_seconds <= 900:
        raise TrialStop('TTL_OUTSIDE_60_900_SECONDS')
    if a.recover:
        if a.confirm not in ('', RECOVER_CONFIRM):
            raise TrialStop('RECOVERY_CONFIRMATION_INVALID')
        _recover_trial(apply=a.confirm == RECOVER_CONFIRM)
        return
    if a.apply and a.confirm != CONFIRM:
        raise TrialStop('EXACT_APPLY_CONFIRMATION_REQUIRED')
    if not a.apply and a.confirm:
        raise TrialStop('CONFIRMATION_WITHOUT_APPLY_REFUSED')
    if not a.apply:
        preflight()
        print('R23B2_LAUNCHER_DRY_RUN_READY_NO_CHANGES')
        return
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    trial(a.ttl_seconds)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('R23B2_SIGNAL_CLEANUP_REQUESTED', file=sys.stderr)
        raise SystemExit(130)
    except (TrialStop, OSError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
        print('R23B2_STOP_' + str(e), file=sys.stderr)
        raise SystemExit(4)
