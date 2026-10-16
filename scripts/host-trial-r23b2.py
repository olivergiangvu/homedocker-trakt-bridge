#!/usr/bin/env python3
"""R23B2: guarded, temporary GET-only AIO Unix relay for existing R23B.

No permanent service, production route, Trakt, AIO DB or nginx changes.
Dry-run by default. The opt-in trial is deliberately time bounded.
Only use from an exact reviewed Git checkout with the two sibling R23B2 scripts.
"""
import argparse
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

class TrialStop(Exception):
    pass

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

def remove_owned_sidecar(nonce):
    """Return error code, not an exception, so other cleanup always runs."""
    try:
        c = inspect_container(SIDECAR, required=False)
        if c is None:
            return None
        if not sidecar_owned(c, nonce):
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
        err = remove_owned_sidecar(nonce)
        if err: errors.append(err)
        err = stop_host(host)
        if err: errors.append(err)
        err = remove_owned_dir(directory_created)
        if err: errors.append(err)
        try:
            if fingerprint(inspect_container(CANARY)) != original:
                errors.append('R23B_CANARY_CHANGED_MANUAL_REVIEW')
        except (TrialStop, KeyError, ValueError):
            errors.append('R23B_CANARY_UNVERIFIABLE_MANUAL_REVIEW')
        if errors:
            # These codes reveal no config, IDs, credentials or private networking.
            raise TrialStop('CLEANUP_INCOMPLETE_' + '_'.join(errors))
        print('R23B2_OWNED_RESOURCES_CLEANUP_PASS')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--ttl-seconds', type=int, default=600)
    a = parser.parse_args()
    if not 60 <= a.ttl_seconds <= 900:
        raise TrialStop('TTL_OUTSIDE_60_900_SECONDS')
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
