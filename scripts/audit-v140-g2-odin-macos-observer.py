#!/usr/bin/env python3
"""G2-1A Odin/macos: ephemeral localhost-only, observe-only AIO reverse proxy.

No production nginx config/reload, RC6/AIO restart, DB or graph writes, Docker
changes or Gateway interception. This creates a SEPARATE nginx process and
listener on 127.0.0.1:17431 ONLY in explicitly opted-in 'observe' mode.

HTTP method safety: GET/HEAD/OPTIONS forwarded unchanged; ONLY exact
POST /jellyfin/Users/AuthenticateByName forwarded (may create an AIO session).
Every other non-read method is denied locally with 405. Do not play media.
Only category + HTTP status is logged into an automatically removed temp
directory, NEVER URI, query, IP, headers, credentials, IDs, body or user-agent.
Use a separate test server entry in Odin. Existing Odin AIO entry stays intact.

Mac connects using an SSH local tunnel to homedocker:17431; no LAN/WAN port.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PORT = 17431
AIO = "127.0.0.1:3240"
CONFIRM = "V140_ODIN_MACOS_EPHEMERAL_OBSERVE_ONLY"
VALID_GROUPS = ("nextup", "resume", "userdata", "auth", "other")
VALID_STATUS = re.compile(r"[1-5][0-9]{2}\Z")


class Stop(Exception):
    pass


def reject(reason):
    raise Stop(reason)


def g2_preflight():
    import importlib.util
    target = ROOT / "audit-v140-g2-route-preflight.py"
    spec = importlib.util.spec_from_file_location("g2_readonly", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Invoke the read-only G2 API directly; do not reparse observer flags.
    g1a = mod.load("g1a", "audit-v140-g1-live-inputs.py")
    g1b = mod.load("g1b", "audit-v140-g1b-isolated-replay.py")
    g1c = mod.load("g1c", "audit-v140-g1c-direct-aio.py")
    try:
        return mod.inspect_baseline(g1a, g1b, g1c)
    except Exception:
        # Upstream imported audit exceptions may contain sensitive file paths
        # or Docker details. Never expose them through a Python traceback.
        reject("G2_ROUTE_BASELINE_FAILED")


def nginx_path():
    result = shutil.which("nginx")
    if result:
        return result
    for candidate in ("/usr/sbin/nginx", "/usr/local/sbin/nginx"):
        if Path(candidate).is_file():
            return candidate
    reject("NGINX_EXECUTABLE_NOT_FOUND")


def configuration(folder):
    """Fixed, reviewable config: no external input interpolated into routing."""
    root = str(folder)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", root):
        reject("NGINX_TEMP_PATH_INVALID")
    return r'''worker_processes  1;
pid %s/observer.pid;
error_log /dev/null crit;
events { worker_connections 128; }
http {
    # Allow no raw URL or request metadata in the ONLY access log.
    log_format odin_groups '$odin_category $status';
    map "$request_method:$uri" $odin_category {
        default other;
        "~^GET:/jellyfin/Shows/NextUp$" nextup;
        "~^GET:/jellyfin/UserItems/Resume$" resume;
        "~^GET:/jellyfin/Users/[A-Za-z0-9_-]{8,128}/Items/Resume$" resume;
        "~^GET:/jellyfin/Users/[A-Za-z0-9_-]{8,128}/Items$" userdata;
        "~^GET:/jellyfin/Users/Me$" userdata;
        "~^POST:/jellyfin/Users/AuthenticateByName$" auth;
    }
    # Local observer cannot issue playback, watched or scrobble writes.
    # One allowed POST is AIO login, necessary for a *separate* Odin entry.
    map "$request_method:$uri" $odin_allowed {
        default 0;
        "~^(GET|HEAD|OPTIONS):" 1;
        "~^POST:/jellyfin/Users/AuthenticateByName$" 1;
    }
    map $http_upgrade $odin_connection {
        default upgrade;
        '' close;
    }
    server {
        listen 127.0.0.1:17431;
        server_name _;
        access_log %s/counts.log odin_groups;
        client_max_body_size 256k;
        client_body_buffer_size 256k;
        client_body_temp_path %s/body_temp;
        proxy_temp_path %s/proxy_temp;
        proxy_intercept_errors off;
        location / {
            if ($odin_allowed = 0) { return 405; }
            proxy_pass http://127.0.0.1:3240;
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header Connection $odin_connection;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header X-Forwarded-Host $http_host;
            proxy_set_header X-Forwarded-Proto http;
            proxy_connect_timeout 4s;
            proxy_send_timeout 15s;
            proxy_read_timeout 15s;
            proxy_buffering off;
            proxy_request_buffering on;
            proxy_redirect off;
        }
    }
}
''' % (root, root, root, root)


def counter_report(lines):
    counts = Counter()
    statuses = Counter()
    for line in lines:
        items = line.strip().split()
        if len(items) != 2 or items[0] not in VALID_GROUPS \
           or not VALID_STATUS.fullmatch(items[1]):
            reject("OBSERVER_COUNTER_LOG_FORMAT_CHANGED")
        counts[items[0]] += 1
        statuses[items[1][0] + "xx"] += 1
    return {
        "audit": "G2_ODIN_MACOS_REAL_ROUTE_OBSERVE_ONLY",
        "status": "ODIN_OBSERVER_FINISHED_NO_GATEWAY_INTERCEPT",
        "observed_requests": sum(counts.values()),
        "observed_nextup": counts["nextup"],
        "observed_resume": counts["resume"],
        "observed_userdata": counts["userdata"],
        "observed_authentication": counts["auth"],
        "observed_other": counts["other"],
        "response_classes": {
            "2xx": statuses["2xx"], "3xx": statuses["3xx"],
            "4xx": statuses["4xx"], "5xx": statuses["5xx"],
        },
        "client_nextup_path_observed": counts["nextup"] > 0,
        "gateway_forwarded_requests": 0,
        "production_nginx_reloaded": False,
        "production_sqlite_writes_requested": 0,
        "playback_writes_forwarded_to_aio": 0,
        "temporary_observer_removed": True,
        "real_gateway_client_qualified": False,
    }


def run_observer(nginx):
    # Fixed scope sanity: no preexisting temporary preview before binding.
    baseline = g2_preflight()
    if baseline.get("status") != "G2_ROUTE_BASELINE_READY_NO_APPLY":
        reject("G2_ROUTE_PREFLIGHT_NOT_READY")

    with tempfile.TemporaryDirectory(prefix="v140_odin_observer_") as directory:
        folder = Path(directory)
        for subdir in ("body_temp", "proxy_temp"):
            (folder / subdir).mkdir(mode=0o700)
        conf = folder / "nginx.conf"
        conf.write_text(configuration(folder), encoding="ascii")
        os.chmod(conf, 0o600)
        check = subprocess.run(
            [nginx, "-t", "-c", str(conf), "-p", str(folder) + "/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if check.returncode != 0:
            reject("NGINX_TEMP_CONFIG_REJECTED")

        process = subprocess.Popen(
            [nginx, "-c", str(conf), "-p", str(folder) + "/",
             "-g", "daemon off; master_process off;"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.7)
            if process.poll() is not None:
                reject("ODIN_OBSERVER_DID_NOT_START")
            print("ODIN_OBSERVER_READY_LOOPBACK_ONLY", flush=True)
            print("MAC_SSH_TUNNEL_TARGET=127.0.0.1:17431", flush=True)
            print("INITIAL_DISCOVERY=Browse Odin Home/Continue Watching ONLY; do not Play/mark watched.", flush=True)
            print("STOP=Press Ctrl+C in this terminal to close observer and print safe counters.", flush=True)
            while process.poll() is None:
                time.sleep(0.5)
            reject("ODIN_OBSERVER_EXITED_UNEXPECTEDLY")
        except KeyboardInterrupt:
            pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        logfile = folder / "counts.log"
        result = counter_report(logfile.read_text(encoding="ascii").splitlines()
                                if logfile.is_file() else [])
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("preflight", "observe"))
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.mode == "observe" and args.confirm != CONFIRM:
        reject("EXPLICIT_TEMPORARY_OBSERVER_APPROVAL_REQUIRED")
    if args.mode == "preflight" and args.confirm:
        reject("NO_AUTH_OR_APPLY_CONFIRMATION_ALLOWED_IN_PREFLIGHT")
    baseline = g2_preflight()
    nginx = nginx_path()
    if args.mode == "preflight":
        return {
            "audit": "G2_ODIN_MACOS_OBSERVER_PREFLIGHT",
            "status": "ODIN_OBSERVER_PREPARED_NOT_RUNNING",
            "g2_rc6_aio_port_baseline_pass": baseline.get("status")
                == "G2_ROUTE_BASELINE_READY_NO_APPLY",
            "nginx_executable_present": bool(nginx),
            "separate_observer_port": PORT,
            "observer_running": False,
            "production_nginx_touched": False,
            "client_modified": False,
            "real_gateway_client_qualified": False,
        }
    if not sys.stdin.isatty():
        reject("INTERACTIVE_SSH_REQUIRED")
    return run_observer(nginx)


if __name__ == "__main__":
    try:
        report = main()
        print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    except (Stop, OSError, ValueError, TypeError, KeyError,
            subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        value = str(exc) if isinstance(exc, Stop) else ""
        reason = value if re.fullmatch(r"[A-Z0-9_]{3,100}", value) else "OBSERVER_UNAVAILABLE"
        print("G2_ODIN_STOP_" + reason, file=sys.stderr)
        raise SystemExit(5) from None
