#!/usr/bin/env python3
"""G2-0: read-only host baseline for one-client preview ingress planning.

No nginx edit/reload, new Docker container/network/image, secret, AIO account
login, client reconfiguration, AIO/Trakt API read/write, production SQLite
write or new listening socket. Reads existing pinned RC6/AIO Docker metadata,
Bridge SQLite using existing G1B read-only verifier, and Linux /proc/net/tcp*.

Ports below are ONLY proposed and their current status is observed, never
reserved. An available port can become occupied after preflight.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PILOT_INGRESS_PORT = 17431
PILOT_GATEWAY_PORT = 17432


class Stop(Exception):
    pass


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def listening_ports(files=None):
    """Linux kernel socket read only, no sudo networking/probing/binds."""
    files = files or (Path("/proc/net/tcp"), Path("/proc/net/tcp6"))
    ports = set()
    read_at_least_one = False
    for path in files:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="ascii").splitlines()
        except OSError:
            raise Stop("KERNEL_SOCKET_READ_UNAVAILABLE") from None
        read_at_least_one = True
        for line in lines[1:]:
            columns = line.split()
            if len(columns) < 4:
                raise Stop("KERNEL_SOCKET_TABLE_INVALID")
            if columns[3] != "0A":  # TCP_LISTEN, not established connections
                continue
            addr = columns[1]
            if ":" not in addr:
                raise Stop("KERNEL_SOCKET_ADDRESS_INVALID")
            try:
                port = int(addr.rsplit(":", 1)[1], 16)
            except ValueError:
                raise Stop("KERNEL_SOCKET_PORT_INVALID") from None
            if not 0 <= port <= 65535:
                raise Stop("KERNEL_SOCKET_PORT_OUT_OF_RANGE")
            ports.add(port)
    if not read_at_least_one:
        raise Stop("KERNEL_SOCKET_TABLE_UNAVAILABLE")
    return ports


def inspect_baseline(g1a, g1b, g1c, ports=None):
    g1c.aio_topology()
    _, database = g1b.inspect_production()
    legacy = g1b.snapshot(database, g1a)
    # No HTTP in G2-0: AIO public and auth path independently PASS G1C.
    seen = listening_ports() if ports is None else set(ports)
    in_use = [port for port in (PILOT_INGRESS_PORT, PILOT_GATEWAY_PORT)
              if port in seen]
    if in_use:
        # Do not output unrelated processes, ports, user configs or /proc data.
        raise Stop("PROPOSED_PREVIEW_PORT_UNAVAILABLE")
    return {
        "audit": "G2_REAL_CLIENT_ROUTE_DISCOVERY_PREFLIGHT",
        "status": "G2_ROUTE_BASELINE_READY_NO_APPLY",
        "running_bridge_rc6_digest_checked": True,
        "existing_aiostreams_loopback_topology_checked": True,
        "verified_show_candidates": legacy["shows"],
        "verified_alias_groups": legacy["groups"],
        "preview_ingress_port_candidate": PILOT_INGRESS_PORT,
        "preview_gateway_port_candidate": PILOT_GATEWAY_PORT,
        "candidate_ports_currently_unbound": True,
        "ports_reserved_or_published": False,
        "nginx_preview_configuration_installed": False,
        "gateway_candidate_created": False,
        "app_client_server_address_modified": False,
        "client_nextup_request_path_observed": False,
        "real_client_qualified": False,
        "aio_auth_requests": 0,
        "production_sqlite_writes": 0,
        "docker_resources_changed": 0,
        "release_hold_active": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preflight",), required=True)
    args = parser.parse_args()
    if args.mode != "preflight":
        raise Stop("UNAUTHORIZED_MODE")
    g1a = load("g1a", "audit-v140-g1-live-inputs.py")
    g1b = load("g1b", "audit-v140-g1b-isolated-replay.py")
    g1c = load("g1c", "audit-v140-g1c-direct-aio.py")
    try:
        return inspect_baseline(g1a, g1b, g1c)
    except (g1a.Stop, g1b.Stop, g1c.Stop):
        # Prior audit imports use their own Stop classes; do not surface
        # arbitrary Docker / SQLite paths or identities in this operator UI.
        raise Stop("UPSTREAM_G1_BASELINE_CHANGED") from None


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2, sort_keys=True))
    except (Stop, OSError, ValueError, TypeError, KeyError,
            AssertionError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        detail = str(exc) if isinstance(exc, Stop) else ""
        safe = (detail if re.fullmatch(r"[A-Z0-9_]{3,100}", detail)
                else "G2_PREFLIGHT_UNAVAILABLE")
        print("G2_STOP_" + safe, file=sys.stderr)
        raise SystemExit(5) from None
