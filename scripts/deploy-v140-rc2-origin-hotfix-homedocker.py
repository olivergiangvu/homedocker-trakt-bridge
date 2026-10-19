#!/usr/bin/env python3
"""Guarded RC2 -> RC2 Safari form Origin hotfix. READ-ONLY by default.

Must run under SSH user (not sudo python); uses sudo -n for protected Compose.
Preserves BOTH existing Compose files, existing networks, /app/data, AIO and
the one Trakt writer. No nginx, client, token or media-state changes.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OLD_TAG = "homedocker-trakt-bridge:v1.4.0-rc.2-ci-fa64a91ad827"
CONFIRM = "AUTHORIZE_V140_RC2_SAFARI_ORIGIN_HOTFIX"
NETS = {"homedocker-trakt-bridge_default", "media_core_net"}


def import_ops(filename):
    spec = importlib.util.spec_from_file_location(
        "ops_" + filename.replace("-", "_").replace(".", "_"), HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc2 = import_ops("deploy-v140-rc2-homedocker.py")
old = rc2.old


class Stop(Exception):
    pass


def docker_json(*args):
    return json.loads(old.run("docker", *args))


def js_probe(expect_fallback=False):
    # All credentials remain within the existing Bridge process/container.
    # Invalid form state is deliberately non-mutating even after authentication.
    js = r"""
import { loadConfig } from './src/config.mjs';
import { deriveProfileKey } from './src/crypto.mjs';
const config = loadConfig();
const profile = config.jellyfinGatewayProfileId;
const key = deriveProfileKey(config.bridgeSecret,'setup',profile);
const path = '/u/' + profile + '/gateway/filter?key=' + encodeURIComponent(key);
const local = 'http://127.0.0.1:7000' + path;
const referer = config.publicBaseUrl + '/u/' + profile + '/setup';
async function check(headers,expected) {
  const response = await fetch(local,{method:'POST',redirect:'manual',
    headers:{'content-type':'application/x-www-form-urlencoded',...headers},
    body:'state=invalid'});
  if(response.status!==expected) throw new Error('UNEXPECTED_CONTROL_HTTP_'+response.status);
}
await check({referer,'sec-fetch-site':'same-origin'}, """ + ("400" if expect_fallback else "403") + r""");
await check({referer,origin:'https://not-authorized.invalid'},403);
await check({'sec-fetch-site':'cross-site',referer},403);
const u='http://127.0.0.1:7000/u/'+profile+'/gateway/status.json?key='+encodeURIComponent(key);
const r=await fetch(u,{signal:AbortSignal.timeout(5000)});
if(r.status!==200)throw new Error('GATEWAY_STATUS_HTTP_'+r.status);
const s=await r.json();
if(s.enabled!==true||s.listening!==true||s.filteringEnabled!==false
   ||s.sourceKind!=='aiostreams'||s.identity.aliasGroups<1)
   throw new Error('GATEWAY_STATE_NOT_SAFE');
const health=await fetch('http://127.0.0.1:7101/health',{signal:AbortSignal.timeout(5000)});
if(health.status!==200)throw new Error('GATEWAY_HEALTH_HTTP_'+health.status);
const aio=await fetch('http://aiostreams:3000/jellyfin/System/Info/Public',
                      {signal:AbortSignal.timeout(5000)});
if(aio.status!==200)throw new Error('AIO_HTTP_'+aio.status);
console.log('PROFILE_UI_GATEWAY=OFF_AND_LISTENING');
console.log('GATEWAY_INTERNAL_HTTP=200');
console.log('AIO_INTERNAL_HTTP=200');
console.log('ORIGIN_FALLBACK_POLICY=' + (""" + ("'ACCEPTS_EXACT_REFERER'" if expect_fallback else "'OLD_FAIL_CLOSED'") + r"""));
console.log('CROSS_SITE_POST=REJECTED');
console.log('WATCH_STATE_WRITES=0');
"""
    return old.run("docker", "exec", "trakt-bridge", "node",
                   "--input-type=module", "-e", js)


def runtime_ready(retries=1):
    for i in range(retries):
        if rc2.previous_ops.runtime_ready("1.4.0-rc.2"):
            return True
        if i + 1 < retries:
            time.sleep(2)
    return False


def check_runtime(info, image_tag, image_id, aio_id, source):
    if info["Config"]["Image"] != image_tag or info["Image"] != image_id:
        raise Stop("BRIDGE_IMAGE_MISMATCH")
    if not info["State"]["Running"] or set(info["NetworkSettings"]["Networks"]) != NETS:
        raise Stop("BRIDGE_NETWORKS_OR_RUNNING_CHANGED")
    mounts = [m.get("Source") for m in info.get("Mounts", [])
              if m.get("Destination") == "/app/data"]
    if mounts != [source]:
        raise Stop("BRIDGE_DATA_MOUNT_CHANGED")
    ports = info.get("NetworkSettings", {}).get("Ports") or {}
    if list(ports.get("7000/tcp") or []) != [
            {"HostIp": "127.0.0.1", "HostPort": "7000"}]:
        raise Stop("BRIDGE_PUBLIC_PORT_CHANGED")
    if ports.get("7101/tcp"):
        raise Stop("GATEWAY_PORT_PUBLISHED")
    if old.container("aiostreams")["Image"] != aio_id:
        raise Stop("AIO_IMAGE_CHANGED")


def inspect(run_id, expected_head):
    if os.geteuid() == 0:
        raise Stop("USE_NORMAL_SSH_USER_NOT_SUDO_PYTHON")
    if not re.fullmatch(r"[a-f0-9]{40}", expected_head):
        raise Stop("INVALID_SOURCE_HEAD")
    checked_out = old.run("git", "-C", str(HERE.parent), "rev-parse", "HEAD")
    if checked_out.strip() != expected_head:
        raise Stop("HOST_SOURCE_CHECKOUT_NOT_EXPECTED")
    artifact = rc2.artifact(run_id, expected_head)
    prev, compose, env, paths, source = old.inspect_stack()
    if prev["Config"]["Image"] != OLD_TAG:
        raise Stop("EXPECTED_ORIGINAL_RC2_IMAGE_ONLY")
    if {p.name for p in paths} != {"compose.yml", "compose.gateway.yml"} or len(paths) != 2:
        raise Stop("GATEWAY_COMPOSE_OVERRIDE_NOT_PERSISTENT")
    if set(prev["NetworkSettings"]["Networks"]) != NETS:
        raise Stop("GATEWAY_NETWORK_NOT_READY")
    rendered = json.loads(old.run(*compose, "config", "--format", "json"))
    svc = rendered["services"]["trakt-bridge"]
    env_rendered = svc.get("environment") or {}
    if (svc["image"] != OLD_TAG
        or str(env_rendered.get("JELLYFIN_GATEWAY_ENABLED", "")).lower() != "true"
        or env_rendered.get("JELLYFIN_GATEWAY_SOURCE_KIND") != "aiostreams"
        or env_rendered.get("JELLYFIN_GATEWAY_UPSTREAM_URL")
            != "http://aiostreams:3000/jellyfin"
        or str(env_rendered.get("JELLYFIN_GATEWAY_RESUME_PASSTHROUGH", "")).lower() != "false"
        or str(env_rendered.get("JELLYFIN_GATEWAY_LEGACY_IMPORT", "")).lower() != "off"):
        raise Stop("GATEWAY_COMPOSE_CONFIGURATION_DRIFT")
    old.check_flags(prev)
    if not runtime_ready():
        raise Stop("RC2_NOT_READY")
    before_img = docker_json("image", "inspect", OLD_TAG)
    if len(before_img) != 1 or before_img[0]["Id"] != prev["Image"]:
        raise Stop("ORIGINAL_RC2_IMAGE_NOT_AVAILABLE")
    aio = old.container("aiostreams")
    if (aio["Config"]["Image"] != "ghcr.io/viren070/aiostreams:latest"
        or not aio["State"]["Running"]):
        raise Stop("AIO_NOT_HEALTHY")
    check_runtime(prev, OLD_TAG, prev["Image"], aio["Image"], source)
    ids = old.run("docker", "ps", "-q", "--no-trunc").splitlines()
    others = docker_json("container", "inspect", *ids) if ids else []
    if any(row["Id"] != prev["Id"]
           and any(m.get("Source") == source for m in row.get("Mounts", []))
           for row in others):
        raise Stop("SECOND_WRITER_MOUNT_DETECTED")
    print(js_probe(expect_fallback=False).strip())
    print("ORIGIN_FIX_ARTIFACT_SHA256=PASS")
    print("RC2_GATEWAY_BASELINE=PASS")
    print("COMPOSE_OVERRIDE=UNCHANGED_TWO_FILES")
    return artifact, prev, compose, env, source, aio["Image"]


def restore(saved, env, compose, prev, source, aio_id):
    try:
        old.run("sudo", "-n", "cp", "-p", str(saved), str(env))
        old.run(*compose, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not runtime_ready(retries=30):
            raise Stop("ROLLBACK_READINESS_FAILED")
        restored = old.container("trakt-bridge")
        check_runtime(restored, OLD_TAG, prev["Image"], aio_id, source)
        print(js_probe(expect_fallback=False).strip())
        print("RC2_ORIGIN_FIX_ROLLBACK=HEALTHY")
    except Exception as exc:
        print("RC2_ORIGIN_FIX_ROLLBACK=MANUAL_RECOVERY_REQUIRED",
              file=sys.stderr)
        raise Stop("ORIGIN_FIX_AND_AUTOMATIC_ROLLBACK_FAILED") from exc


def apply(artifact, prev, compose, env, source, aio_id):
    old.run("docker", "load", "-i", str(artifact["archive"]))
    img = docker_json("image", "inspect", artifact["tag"])
    if len(img) != 1 or img[0]["Id"] != artifact["image_id"]:
        raise Stop("NEW_IMAGE_ID_MISMATCH")
    print("HOTFIX_IMAGE_LOAD=VERIFIED")
    root = Path.home() / ".local/share/homedocker/release-backups/rc2-origin-hotfix"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    snapshot = root / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    snapshot.mkdir(mode=0o700)
    saved = snapshot / "compose.env.before"
    old.run("sudo", "-n", "cp", "-p", str(env), str(saved))
    old.run("sudo", "-n", "chmod", "600", str(saved))
    try:
        # Rollback is eligible before any mutating .env write.
        old.set_image(env, artifact["tag"])
        config = json.loads(old.run(*compose, "config", "--format", "json"))
        if config["services"]["trakt-bridge"]["image"] != artifact["tag"]:
            raise Stop("HOTFIX_COMPOSE_IMAGE_NOT_RENDERED")
        old.run(*compose, "up", "-d", "--no-deps", "--no-build",
                "--pull", "never", "--force-recreate", "trakt-bridge")
        if not runtime_ready(retries=30):
            raise Stop("HOTFIX_BRIDGE_NOT_READY")
        updated = old.container("trakt-bridge")
        check_runtime(updated, artifact["tag"], artifact["image_id"], aio_id, source)
        old.check_flags(updated)
        print(js_probe(expect_fallback=True).strip())
    except Exception as exc:
        print("ORIGIN_FIX_FAILED=ATTEMPTING_RC2_ROLLBACK")
        restore(saved, env, compose, prev, source, aio_id)
        raise Stop("ORIGIN_FIX_FAILED_ROLLBACK_COMPLETED") from exc
    print("ORIGIN_FIX_CUTOVER=HEALTHY")
    print("GATEWAY_UI_FILTER=OFF")
    print("AIO_CLIENT_ROUTING_CHANGED=NO")
    print("BASE_COMPOSE_AND_OVERRIDE_UNCHANGED=YES")
    print("STABLE_RELEASE_HOLD=ACTIVE")
    print("PRIVATE_ROLLBACK_SNAPSHOT=AVAILABLE")


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--expected-head", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args()
    if args.apply and args.confirm != CONFIRM:
        raise Stop("EXPLICIT_HOTFIX_APPROVAL_REQUIRED")
    if not args.apply and args.confirm:
        raise Stop("CONFIRM_WITHOUT_APPLY_FORBIDDEN")
    state = inspect(args.run_id, args.expected_head)
    print("MODE=" + ("APPLY_GUARDED" if args.apply else "READ_ONLY_PREFLIGHT"))
    if not args.apply:
        print("PRODUCTION_CHANGES=0")
        return
    apply(*state)


if __name__ == "__main__":
    try:
        main()
    except (Stop, rc2.Stop, old.Stop, OSError, KeyError, IndexError,
            ValueError, subprocess.SubprocessError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
