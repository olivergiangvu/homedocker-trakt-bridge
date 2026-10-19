#!/usr/bin/env python3
"""Guarded RC2 Safari Referrer-Policy compatibility hotfix, reuse proven rollback.

This wraps the attested single-Bridge Origin hotfix cutover and only tightens
its baseline and acceptance checks. Read-only by default. Does not route
clients, change AIO/Trakt, or publish Gateway port 7101.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "deploy-v140-rc2-origin-hotfix-homedocker.py"
spec = importlib.util.spec_from_file_location("rc2_origin_hotfix_ops", SCRIPT)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

# Attested image successfully deployed before this page-only policy fix.
ops.OLD_TAG = "homedocker-trakt-bridge:v1.4.0-rc.2-ci-d278050b2baf"
CONFIRM = "AUTHORIZE_V140_RC2_SETUP_REFERRER_POLICY_HOTFIX"
original_probe = ops.js_probe


def probe(expect_fallback=False):
    # Both images already implement strict Origin / Referer validation.
    # Only response Referrer-Policy differs; never submit a valid ON/OFF state.
    passed = original_probe(expect_fallback=True)
    expected_policy = "strict-origin" if expect_fallback else "no-referrer"
    js = r"""
import { loadConfig } from './src/config.mjs';
const config = loadConfig();
const setup = 'http://127.0.0.1:7000/setup?key='
    + encodeURIComponent(config.adminKey);
const response = await fetch(setup, {
  method:'GET', redirect:'manual', signal:AbortSignal.timeout(5000)
});
const expected = """ + repr(expected_policy) + r""";
if (response.status !== 200
    || response.headers.get('referrer-policy') !== expected) {
  throw new Error('SETUP_REFERRER_POLICY_NOT_'+expected);
}
const health = await fetch('http://127.0.0.1:7000/health', {
  signal:AbortSignal.timeout(5000)
});
if (health.status !== 200
    || health.headers.get('referrer-policy') !== 'no-referrer') {
  throw new Error('GLOBAL_NO_REFERRER_REGRESSION');
}
console.log('AUTHENTICATED_SETUP_REFERRER_POLICY='+expected);
console.log('HEALTH_REFERRER_POLICY=no-referrer');
console.log('SETUP_KEY_NOT_EMITTED=TRUE');
"""
    # No URLs, headers or credential values printed by the probe.
    checked = ops.old.run("docker", "exec", "trakt-bridge", "node",
                          "--input-type=module", "-e", js)
    return passed.strip() + "\n" + checked.strip()


# The underlying inspection, same-writer check, image attestation, root-only
# Compose handling, Gateway/AIO assertions and rollback call this probe.
ops.js_probe = probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.apply and args.confirm != CONFIRM:
        raise ops.Stop("EXPLICIT_REFERRER_HOTFIX_APPROVAL_REQUIRED")
    if not args.apply and args.confirm:
        raise ops.Stop("CONFIRM_ONLY_VALID_WITH_APPLY")
    state = ops.inspect(args.run_id, args.expected_head)
    print("REFERRER_HOTFIX_ARTIFACT=VERIFIED")
    if not args.apply:
        print("MODE=READ_ONLY_PREFLIGHT")
        print("PRODUCTION_CHANGES=0")
        return
    ops.apply(*state)
    print("REFERRER_POLICY_CUTOVER=HEALTHY")
    print("SETUP_REFERRER_POLICY=strict-origin")
    print("PUBLIC_NGINX_HEADER_NOT_YET_QUALIFIED")
    print("GATEWAY_FILTER=OFF")
    print("STABLE_RELEASE_HOLD=ACTIVE")


if __name__ == "__main__":
    try:
        main()
    except (ops.Stop, ops.rc2.Stop, ops.old.Stop, OSError, KeyError,
            IndexError, ValueError, subprocess.SubprocessError) as error:
        print("STOP: " + str(error), file=sys.stderr)
        raise SystemExit(1)
