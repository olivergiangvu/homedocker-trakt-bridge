#!/usr/bin/env python3
"""G2-1B HomeDocker Odin shadow: safe preflight or operator-confirmed 3m pilot.

- preflight: inspect pinned RC6 image/AIO topology; SQLite mode=ro/query_only;
  public AIO health; exact localhost ports; Node >=24; no new processes.
- login: ONE explicit AIO login POST; validate /Users/Me; start ephemeral,
  unprivileged Node shadow candidate from read-only legacy graph.
- existing-token: prompt for existing AIO token (hidden) instead of login.
  Token never crosses into the child or into any file/argument/environment.
- During pilot Odin's token can flow IN RAM through trusted localhost ingress
  to AIO and same-process-owned Gateway on bounded shadow GETs only.
- Ctrl+C (or 3m timer) stops both localhost listeners. Sanitized counters only.
No change to RC6, production nginx, AIO files, Portainer, Trakt or DB.
"""
import argparse
import getpass
import importlib.util
import json
import os
import pwd
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "launch-v140-g2-odin-shadow-candidate.mjs"
LIMIT = 4 * 1024 * 1024
CONFIRM_LOGIN = "V140_G2_ODIN_SHADOW_ONE_CLIENT_ONE_LOGIN"
CONFIRM_TOKEN = "V140_G2_ODIN_SHADOW_ONE_CLIENT_EXISTING_TOKEN"
READY = "G2_SHADOW_RUNTIME_READY_LOOPBACK_ONLY"
RESULT = "G2_ODIN_SHADOW_ACTUAL_GATEWAY_SESSION"
FIELDS = (
    "ingress_nextup", "ingress_resume", "ingress_client_aio_2xx",
    "ingress_client_aio_4xx", "ingress_client_aio_5xx",
    "shadow_attempted", "shadow_completed", "shadow_errors",
    "shadow_timeouts", "shadow_2xx", "shadow_4xx", "shadow_5xx",
    "gateway_nextup_gets", "gateway_alias_removed_items",
    "gateway_fail_open_normalization", "gateway_upstream_transport_failures",
    "gateway_nextup_responses_completed", "gateway_nextup_upstream_samples",
    "gateway_nextup_upstream_max_ms", "gateway_nextup_response_max_ms",
    "gateway_fail_open_normalization_errors",
    "aio_nextup_attempted", "aio_nextup_http_2xx",
    "aio_nextup_http_4xx", "aio_nextup_http_5xx",
    "aio_nextup_transport_error", "aio_nextup_aborted",
    "aio_users_me_attempted", "aio_users_me_http_2xx",
    "aio_users_me_transport_error", "aio_metadata_attempted",
    "aio_metadata_http_2xx", "aio_metadata_transport_error",
    "aio_get_only", "disallowed_gateway_aio_requests",
    "verified_legacy_show_count", "verified_legacy_alias_groups",
    "forwarded_watch_state_writes", "production_sqlite_writes",
)


class Stop(Exception):
    pass


def reject(reason):
    raise Stop(reason)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drop_privileges():
    """Shared early drop for version check and real pilot Node child."""
    uid, gid = drop_to_operator()
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def _operator_home():
    uid, _ = drop_to_operator()
    try:
        record = pwd.getpwuid(uid)
    except KeyError:
        reject("SUDO_OPERATOR_HOME_UNAVAILABLE")
    home = Path(record.pw_dir)
    if not home.is_absolute() or not home.is_dir():
        reject("SUDO_OPERATOR_HOME_UNAVAILABLE")
    return home


def find_node():
    """Find operator's private nvm Node 24; NEVER execute a user binary as root.

    sudo uses /usr/bin/node (often old) while interactive SSH resolves nvm's
    Node 14. Candidate --version checks and the final Node process both
    ALWAYS drop privileges to SUDO_UID/SUDO_GID first.
    """
    home = _operator_home()
    versions = home / ".nvm" / "versions" / "node"
    candidates = []
    if versions.is_dir():
        for version in versions.iterdir():
            match = re.fullmatch(r"v24\.(\d+)\.(\d+)", version.name)
            if match and version.is_dir():
                candidates.append((int(match.group(1)), int(match.group(2)),
                                   version / "bin" / "node"))
    candidates.sort(reverse=True)
    paths = [node for _, _, node in candidates]
    system_node = shutil.which("node")
    if system_node:
        paths.append(Path(system_node))
    for candidate in paths:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        # No group/world-writable binaries, even though they will execute
        # ONLY as the invoking normal user.
        if candidate.stat().st_mode & 0o022:
            continue
        try:
            checked = subprocess.run(
                [str(candidate), "--version"], text=True,
                capture_output=True, timeout=5, check=True,
                preexec_fn=_drop_privileges,
                cwd=str(home),
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
        except (OSError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired, subprocess.SubprocessError):
            continue
        version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*",
                               checked.stdout)
        if version and int(version.group(1)) >= 24:
            return str(candidate)
    reject("HOST_NODE_24_REQUIRED_INSTALL_IN_OPERATOR_NVM")


def baseline():
    if RUNNER.is_symlink() or not RUNNER.is_file():
        reject("SHADOW_NODE_RUNNER_MISSING")
    if os.geteuid() != 0:
        reject("SUDO_READONLY_SQLITE_PREFLIGHT_REQUIRED")
    g1a = load("g2_g1a", "audit-v140-g1-live-inputs.py")
    g1b = load("g2_g1b", "audit-v140-g1b-isolated-replay.py")
    g1c = load("g2_g1c", "audit-v140-g1c-direct-aio.py")
    g2 = load("g2_ports", "audit-v140-g2-route-preflight.py")
    try:
        g1c.aio_topology()
        image, database = g1b.inspect_production()
        snapshot = g1b.snapshot(database, g1a)
        if snapshot["groups"] != 1 or snapshot["shows"] != 17:
            reject("LEGACY_SCOPED_SHOW_COUNTS_CHANGED")
        if 17431 in g2.listening_ports() or 17432 in g2.listening_ports():
            reject("SHADOW_PREVIEW_PORT_BUSY")
        g1a.aio_request("/System/Info/Public")
    except (g1a.Stop, g1b.Stop, g1c.Stop, g2.Stop):
        reject("SHADOW_BASELINE_CHANGED")
    # No secrets, hosts, database paths, source IDs or image digests returned.
    return snapshot, g1a, find_node()


def drop_to_operator():
    if os.geteuid() != 0 or "SUDO_UID" not in os.environ \
       or "SUDO_GID" not in os.environ:
        reject("SUDO_ORIGINAL_OPERATOR_REQUIRED")
    try:
        uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    except ValueError:
        reject("SUDO_OPERATOR_ID_INVALID")
    if uid < 1000 or gid < 100 or uid == 0:
        reject("REFUSE_TO_RUN_SHADOW_AS_ROOT")
    return uid, gid


def authenticate(g1a, mode):
    if not sys.stdin.isatty():
        reject("INTERACTIVE_SSH_TTY_REQUIRED")
    if mode == "existing-token":
        token = getpass.getpass("Existing AIO token (hidden, not saved): ").strip()
        if not g1a.TOKEN.fullmatch(token):
            reject("INVALID_AIO_TOKEN")
        expected = None
        posts = 0
    else:
        token, expected = g1a.login_session()
        posts = 1
    try:
        user = g1a.aio_request("/Users/Me", token).get("Id")
        if not isinstance(user, str) or not g1a.USER.fullmatch(user):
            reject("SHADOW_AIO_USER_UNVERIFIED")
        if expected and expected != user:
            reject("SHADOW_LOGIN_USER_CHANGED")
        return user, posts
    finally:
        del token


def checked_report(raw):
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        reject("NODE_SANITIZED_REPORT_MISSING")
    if record.get("audit") != RESULT or \
       record.get("status") != "SHADOW_SESSION_FINISHED_NOT_RELEASED":
        reject("NODE_SANITIZED_REPORT_NOT_QUALIFIED")
    if any(type(record.get(k)) is not int or record[k] < 0 for k in FIELDS):
        reject("NODE_COUNTERS_INVALID")
    if any(record.get(k) != v for k, v in {
        "forwarded_watch_state_writes": 0,
        "production_sqlite_writes": 0,
        "real_odin_gateway_client_qualified": False,
        "shadow_canary_complete": False,
        "filter_response_sent_to_odin": False,
        "release_hold_active": True,
    }.items()):
        reject("NODE_HOLD_ASSERTION_INVALID")
    return {k: record[k] for k in ("audit", "status", *FIELDS,
            "real_odin_gateway_client_qualified", "shadow_canary_complete",
            "filter_response_sent_to_odin", "release_hold_active")}


def run_operator(node, source, uid, gid):
    # Never send an AIO token into child stdin: Odin's own token is read only
    # from the later live request and stays in the two local network processes.
    input_value = json.dumps(source, separators=(",", ":"))
    if len(input_value.encode("utf8")) > LIMIT:
        reject("SHADOW_IDENTITY_SNAPSHOT_TOO_LARGE")

    # The same audited unprivileged drop used by node --version preflight.
    if (uid, gid) != drop_to_operator():
        reject("SHADOW_OPERATOR_ID_CHANGED")

    child = subprocess.Popen(
        [node, str(RUNNER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
        cwd=str(ROOT.parent), preexec_fn=_drop_privileges,
        env={"PATH": "/usr/bin:/bin", "NODE_ENV": "production",
             "LANG": "C.UTF-8"},
    )
    try:
        child.stdin.write(input_value)
        child.stdin.close()
        # Note: no Node payload or identifiers are ever printed.
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                reject("SHADOW_NODE_READY_TIMEOUT")
        hello = child.stdout.readline().strip()
        if hello != READY:
            reject("SHADOW_NODE_DID_NOT_START")
        print("G2_ODIN_SHADOW_READY_LOOPBACK_ONLY", flush=True)
        print("MAC_SSH_TUNNEL_TARGET=127.0.0.1:17431", flush=True)
        print("SHADOW_3_MINUTE_MAX_USE_ODIN_PILOT_BROWSE_ONLY", flush=True)
        print("STOP=Ctrl+C, no Play, watched or Trakt sync", flush=True)
        try:
            child.wait(timeout=195)
        except KeyboardInterrupt:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                reject("SHADOW_FORCE_KILLED_REVIEW_LISTENERS")
        except subprocess.TimeoutExpired:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=12)
        raw = child.stdout.read()
        if child.returncode:
            reject("SHADOW_NODE_EXITED_WITH_ERROR")
        # Only one final safe JSON report is expected (no log stream).
        lines = raw.strip().splitlines()
        if len(lines) != 1:
            reject("SHADOW_OUTPUT_FORMAT_INVALID")
        return checked_report(lines[0])
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=7)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        child.stdin.close() if not child.stdin.closed else None
        child.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preflight", "existing-token", "login"),
                        required=True)
    parser.add_argument("--confirm", default="")
    opts = parser.parse_args()
    if opts.mode == "preflight" and opts.confirm:
        reject("PREFLIGHT_MUST_NOT_HAVE_CONFIRM")
    if opts.mode == "login" and opts.confirm != CONFIRM_LOGIN:
        reject("EXPLICIT_ONE_AIO_LOGIN_REQUIRED")
    if opts.mode == "existing-token" and opts.confirm != CONFIRM_TOKEN:
        reject("EXPLICIT_EXISTING_TOKEN_REQUIRED")
    source, g1a, node = baseline()
    if opts.mode == "preflight":
        return {
            "audit": "G2_ODIN_SHADOW_HOST_PREFLIGHT",
            "status": "SHADOW_PREFLIGHT_PASS_NO_LISTENER",
            "host_node_v24_ready": True,
            "production_rc6_digest_checked": True,
            "aio_loopback_checked": True,
            "legacy_shows": source["shows"],
            "alias_groups": source["groups"],
            "candidate_ingress_port": 17431,
            "candidate_gateway_port": 17432,
            "both_ports_currently_unbound": True,
            "aio_login_posts": 0,
            "new_listener_started": False,
            "production_writes": 0,
            "stable_release_hold": True,
        }
    uid, gid = drop_to_operator()
    user, posts = authenticate(g1a, opts.mode)
    report = run_operator(node, {
        "profile": source["profile"],
        "user": user,
        "legacy": source["legacy"],
    }, uid, gid)
    report["operator_login_posts"] = posts
    report["child_dropped_root_privileges"] = True
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, sort_keys=True))
    except (Stop, OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.TimeoutExpired, KeyboardInterrupt, EOFError) as exc:
        status = str(exc) if isinstance(exc, Stop) else ""
        label = status if re.fullmatch(r"[A-Z0-9_]{3,100}", status) else \
            "SHADOW_OPERATOR_LAUNCHER_UNAVAILABLE"
        print("G2_SHADOW_STOP_" + label, file=sys.stderr)
        raise SystemExit(5) from None
