#!/usr/bin/env python3
"""R23D opt-in foreground host-only, single-canary AIO GET relay (no apply).

Only useful AFTER a separate, source-pinned R23D Gateway-only canary passes its
deployment gate. No Docker changes, no AIO login and NO credentials saved.
This process is intentionally foreground, stopped with Ctrl-C; no systemd
service or permanent API is installed.

Bound to the verified dedicated Docker bridge gateway address, NOT 0.0.0.0,
127.0.0.1 or a public interface. Accept only the precisely inspected source
IPv4 of the one R23D canary. No POST, no login endpoints, no Resume, no stream,
no arbitrary URL/proxy, and no Trakt APIs. GET /jellyfin/Users/Me,
GET /jellyfin/Shows/NextUp[?Limit=1..50], GET /jellyfin/Items/{SeriesId}.
The upstream is FIXED 127.0.0.1:3240/jellyfin. Auth tokens remain in request
RAM, never logged, written or exposed in errors. AIO GET handlers may refresh
their own caches.

CAVEAT: Docker bridge ingress/source allowlist IS NOT a verified egress
firewall. This tool cannot certify total container network isolation.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib import error, parse, request

HERE = Path(__file__).resolve().parent
GUARD_PATH = HERE / "audit-v140-r23d-host-preflight.py"
GATEWAY_CONTAINER = "homedocker-v140-r23d-gateway-canary"
NETWORK = "homedocker_v140_r23d_local"
UPSTREAM = "http://127.0.0.1:3240/jellyfin"
RELAY_PORT = 17420
TOKEN_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{16,1024}$")
ITEM_PATTERN = re.compile(r"^/jellyfin/Items/[A-Za-z0-9_-]{1,256}$")
IMAGE_PATTERN = re.compile(r"^homedocker-trakt-bridge:v140-r23d-[a-f0-9]{12}$")
AGENT = ('MediaBrowser Client="HomeDockerR23DRelay",Device="Canary",'
         'DeviceId="homedocker-r23d-canary-relay",Version="1.0.0"')

class Stop(Exception):
    pass

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = request.build_opener(request.ProxyHandler({}), NoRedirect())

def canary_topology():
    spec = importlib.util.spec_from_file_location("r23d_host_inventory", GUARD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    original = mod.container(GATEWAY_CONTAINER)
    host = original.get("HostConfig") or {}
    if not original.get("State", {}).get("Running"):
        raise Stop("r23d_canary_not_running")
    if not host.get("ReadonlyRootfs") or host.get("NetworkMode") != NETWORK:
        raise Stop("r23d_readonly_or_dedicated_network_missing")
    image = (original.get("Config") or {}).get("Image")
    if not IMAGE_PATTERN.fullmatch(str(image)):
        raise Stop("r23d_pinned_source_tag_invalid")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(original.get("Image"))):
        raise Stop("r23d_immutable_image_id_absent")
    if host.get("Privileged") or (host.get("CapAdd") or []):
        raise Stop("r23d_excessive_container_privileges")
    if (original.get("Config") or {}).get("User") not in ("node", "1000"):
        raise Stop("r23d_nonroot_user_not_confirmed")
    nets = (original.get("NetworkSettings") or {}).get("Networks") or {}
    if set(nets) != {NETWORK}:
        raise Stop("r23d_has_unexpected_network_attachment")
    if not mod.loopback_port_binding(original, 17145, 7101):
        raise Stop("r23d_gateway_not_loopback_bound")
    mounts = original.get("Mounts") or []
    # Only one nonsecret identity proof file can be mounted read-only; reject
    # production DB, AIO volumes and arbitrary bind/volume mounts.
    if len(mounts) != 1 or mounts[0].get("Destination") != "/app/identity-proof.json":
        raise Stop("r23d_proof_mount_unexpected")
    if mounts[0].get("RW") or mounts[0].get("Type") != "bind":
        raise Stop("r23d_proof_mount_not_readonly")
    info = mod.network(NETWORK)
    if info.get("Internal") is not False or (info.get("Options") or {}).get(
            "com.docker.network.bridge.enable_ip_masquerade") != "false":
        raise Stop("r23d_dedicated_bridge_configuration_changed")
    try:
        gateway = ipaddress.IPv4Address((info["IPAM"]["Config"][0])["Gateway"])
        subnet = ipaddress.IPv4Network(info["IPAM"]["Config"][0]["Subnet"],
                                       strict=False)
        canary = ipaddress.IPv4Address(nets[NETWORK]["IPAddress"])
    except (ValueError, IndexError, KeyError, TypeError):
        raise Stop("r23d_bridge_ipv4_address_unavailable") from None
    if (not gateway.is_private or not canary.is_private
            or gateway.is_loopback or gateway not in subnet
            or canary not in subnet or canary == gateway):
        raise Stop("r23d_bridge_ip_scope_invalid")
    return str(gateway), str(canary)

def path_to_upstream(target):
    """Return fixed allowlisted path, max body size, timeout. No decoding."""
    if not isinstance(target, str) or len(target) > 512 or "://" in target:
        return None
    try:
        u = parse.urlsplit(target)
    except ValueError:
        return None
    if u.scheme or u.netloc or u.fragment or not u.path.startswith("/"):
        return None
    if u.path == "/jellyfin/Users/Me" and not u.query:
        return (u.path, 128 * 1024, 10)
    if ITEM_PATTERN.fullmatch(u.path) and not u.query:
        return (u.path, 128 * 1024, 10)
    if u.path == "/jellyfin/Shows/NextUp":
        if not u.query:
            return (u.path, 2 * 1024 * 1024, 22)
        try:
            params = parse.parse_qsl(u.query, keep_blank_values=True,
                                     strict_parsing=True, max_num_fields=1)
        except ValueError:
            return None
        if len(params) != 1 or params[0][0] != "Limit":
            return None
        raw = params[0][1]
        if not raw.isascii() or not re.fullmatch(r"[1-9][0-9]?", raw):
            return None
        if int(raw) > 50:
            return None
        return (u.path + "?Limit=" + str(int(raw)), 2 * 1024 * 1024, 22)
    return None

def fetch_aio(path, token, agent, max_bytes, timeout):
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "X-Emby-Token": token,
        "X-Emby-Authorization": agent or AGENT,
    }
    req = request.Request(UPSTREAM + path, headers=headers, method="GET")
    try:
        with OPENER.open(req, timeout=timeout) as res:
            if res.status != 200:
                return res.status, b'{"error":"upstream_status"}'
            raw = res.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return 502, b'{"error":"response_too_large"}'
            try:
                if not isinstance(json.loads(raw), dict):
                    raise ValueError("non-object JSON")
            except (ValueError, UnicodeError, TypeError):
                return 502, b'{"error":"invalid_upstream_json"}'
            return 200, raw
    except error.HTTPError as ex:
        status = ex.code if ex.code in (400, 401, 403, 404, 405, 429, 500, 502, 503) else 502
        return status, b'{"error":"upstream_status"}'
    except (error.URLError, OSError, TimeoutError):
        return 502, b'{"error":"upstream_unavailable"}'

def handler_factory(allowed_ip, expected_host, upstream_get=fetch_aio):
    class Handler(BaseHTTPRequestHandler):
        server_version = "R23DGetRelay"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            # NEVER log Authorization, URI, media IDs, username or client IP.
            return

        def reply(self, status, payload):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def deny(self, code=404):
            self.reply(code, b'{"error":"relay_route_rejected"}')

        def do_GET(self):
            if self.client_address[0] != allowed_ip:
                return self.deny(403)
            if self.headers.get("Host") != expected_host:
                return self.deny(403)
            if self.headers.get("Content-Length") not in (None, "0"):
                return self.deny(400)
            if "Transfer-Encoding" in self.headers:
                return self.deny(400)
            scope = path_to_upstream(self.path)
            if not scope:
                return self.deny()
            tokens = self.headers.get_all("X-Emby-Token", [])
            if len(tokens) != 1 or not TOKEN_PATTERN.fullmatch(tokens[0]):
                return self.deny(401)
            authorization = self.headers.get_all("X-Emby-Authorization", [])
            if len(authorization) > 1:
                return self.deny(400)
            agent = authorization[0] if authorization else AGENT
            if len(agent) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in agent):
                return self.deny(400)
            path, cap, deadline = scope
            try:
                status, body = upstream_get(path, tokens[0], agent, cap, deadline)
            except Exception:
                return self.reply(502, b'{"error":"upstream_unavailable"}')
            if status not in (200, 400, 401, 403, 404, 405, 429, 500, 502, 503):
                return self.deny(502)
            if not isinstance(body, bytes) or len(body) > cap:
                return self.deny(502)
            self.reply(status, body)

        def do_POST(self):
            self.deny(405)

        def do_PUT(self):
            self.deny(405)

        def do_PATCH(self):
            self.deny(405)

        def do_DELETE(self):
            self.deny(405)

        def do_HEAD(self):
            self.deny(405)

    return Handler

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.confirm != "R23D_TEMPORARY_AIO_GET_ONLY_RELAY":
        raise Stop("explicit_session_relay_confirmation_required")
    gateway_ip, canary_ip = canary_topology()
    expected = gateway_ip + ":" + str(RELAY_PORT)
    server = ThreadingHTTPServer(
        (gateway_ip, RELAY_PORT), handler_factory(canary_ip, expected)
    )
    server.daemon_threads = True
    server.allow_reuse_address = False
    print(json.dumps({
        "audit": "R23D_TEMPORARY_HOST_AIO_GET_RELAY",
        "status": "LISTENING_FOREGROUND",
        "bound_to_verified_docker_bridge_only": True,
        "one_verified_canary_ip_only": True,
        "allowed_methods": ["GET"],
        "allowed_paths": ["Users/Me", "Shows/NextUp", "Items/{opaqueSeriesId}"],
        "allows_login_post_or_history_write": False,
        "canary_direct_media_network_access_required": False,
        "no_secret_persisted_or_logged": True,
        "docker_egress_firewall_audited": False,
        "shutdown": "CTRL_C",
    }, indent=2), flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("R23D_TEMPORARY_RELAY_STOPPED", flush=True)

if __name__ == "__main__":
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, TypeError):
        print("STOP: R23D relay not running; no Docker/AIO changes made",
              file=sys.stderr)
        sys.exit(1)
