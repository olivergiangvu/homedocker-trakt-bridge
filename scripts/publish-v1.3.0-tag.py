#!/usr/bin/env python3
"""Publish v1.3.0 lightweight Git tag at exactly qualified main after CI.

Requires authenticated GitHub CLI with Contents write. Idempotent when tag
already points to the intended SHA. Does not deploy HomeDocker containers.
"""
import argparse
import base64
import json
import subprocess
import sys

REPO = "olivergiangvu/homedocker-trakt-bridge"
TAG = "v1.3.0"


def gh_api(endpoint, *opts):
    p = subprocess.run(["gh", "api", *opts, endpoint], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("GitHub API operation failed: " + p.stderr.strip()[:250])
    return json.loads(p.stdout)


def check(main, expected_sha, checks, package, config):
    if main != expected_sha:
        raise RuntimeError("main changed since reviewed merge; abort tag publication")
    if package.get("version") != "1.3.0":
        raise RuntimeError("package.json version is not 1.3.0")
    if "export const APP_VERSION = '1.3.0';" not in config:
        raise RuntimeError("src/config.mjs does not advertise 1.3.0")
    if not any(x.get("head_sha") == expected_sha and x.get("event") == "push"
               and x.get("status") == "completed" and x.get("conclusion") == "success"
               for x in checks):
        raise RuntimeError("required main-push CI has not passed on exact reviewed SHA")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expected-main-sha", required=True,
                   help="complete reviewed squash merge SHA, never moving main")
    args = p.parse_args()
    try:
        sha = args.expected_main_sha.strip()
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise RuntimeError("full lowercase 40-digit SHA required")
        main_sha = gh_api(f"repos/{REPO}/commits/main")["sha"]
        runs = gh_api(f"repos/{REPO}/actions/workflows/ci.yml/runs?per_page=50")
        package_blob = gh_api(f"repos/{REPO}/contents/package.json?ref=main")
        config_blob = gh_api(f"repos/{REPO}/contents/src/config.mjs?ref=main")
        package = json.loads(base64.b64decode(package_blob["content"]))
        config = base64.b64decode(config_blob["content"]).decode("utf-8")
        check(main_sha, sha, runs.get("workflow_runs", []), package, config)
        try:
            existing = gh_api(f"repos/{REPO}/git/ref/tags/{TAG}")
        except RuntimeError as ex:
            if "404" not in str(ex) and "Not Found" not in str(ex):
                raise
            existing = None
        if existing:
            if existing.get("object", {}).get("sha") != sha:
                raise RuntimeError("release tag already exists at DIFFERENT SHA; no retag")
            print("TAG_ALREADY_EXISTS_AT_REVIEWED_COMMIT=true")
        else:
            result = gh_api(f"repos/{REPO}/git/refs", "--method", "POST",
                            "-f", "ref=refs/tags/" + TAG,
                            "-f", "sha=" + sha)
            if result.get("ref") != "refs/tags/" + TAG:
                raise RuntimeError("tag API did not return expected ref")
            print("GITHUB_TAG_CREATED=" + TAG)
        print("TAG_COMMIT=" + sha)
        print("RELEASE_WORKFLOW=TAG_PUSH_TRIGGERED")
        print("NEXT=verify release workflow and exact published GHCR digest before deploying")
    except (KeyError, ValueError, RuntimeError) as ex:
        raise SystemExit("STOP: " + str(ex)) from None


if __name__ == "__main__":
    main()
