#!/usr/bin/env python3
"""Safely garbage-collect historical merged GitHub branches via authenticated gh.

Dry run unless --apply. No tag, repo, issue, open PR, or main deletion.
Also considers exactly three known, closed-unmerged RC7 experiment heads.
Uses only GitHub CLI and standard Python library.
"""
import argparse
import json
import shutil
import subprocess
import sys
from urllib.parse import quote

REPO = "olivergiangvu/homedocker-trakt-bridge"
ARCHIVED = {
    "diagnostics/rc7-jellyfin-shelves": ("83", "ca337a7fd43e1e0e3a5d5af3d81e7dd0bc98197d"),
    "fix/rc7-bridge-side-stale-nextup": ("85", "0ac113299d8dcf5b6df7faef0d44d6c5cb542bcd"),
    "feat/rc7-nextup-canary-patch": ("84", "1d5c3d5500d1b46ad7024a027bd87db17855936e"),
}


def api(path, *extra):
    cp = subprocess.run(["gh", "api", *extra, path], text=True,
                        capture_output=True, check=False)
    if cp.returncode:
        raise RuntimeError("GitHub CLI request failed (credentials, permission or API error): "
                           + cp.stderr.strip()[:300])
    return cp.stdout


def pages(path):
    data = json.loads(api(path, "--paginate", "--slurp"))
    if not isinstance(data, list):
        raise RuntimeError("unexpected GitHub response")
    result = []
    for page in data:
        if not isinstance(page, list):
            raise RuntimeError("unexpected GitHub paginated response")
        result.extend(page)
    return result


def select_candidates(branches, pulls):
    active = {
        p["head"]["ref"] for p in pulls
        if p["state"] == "open" and
        p.get("head", {}).get("repo") and
        p["head"]["repo"]["full_name"] == REPO
    }
    merged = {
        p["head"]["ref"]: p["head"]["sha"] for p in pulls
        if p["state"] == "closed" and p.get("merged_at") and
        p.get("head", {}).get("repo") and
        p["head"]["repo"]["full_name"] == REPO
    }
    closed_unmerged = {
        str(p["number"]): p for p in pulls
        if p["state"] == "closed" and not p.get("merged_at")
    }
    candidates = []
    for row in branches:
        name = row["name"]
        sha = row["commit"]["sha"]
        if name in ("main", "master") or name.startswith("dependabot/"):
            continue
        if name in active:
            continue
        kind = None
        if merged.get(name) == sha:
            kind = "MERGED_PR_HEAD"
        elif name in ARCHIVED:
            pr, expected = ARCHIVED[name]
            if (sha == expected and pr in closed_unmerged
                    and closed_unmerged[pr]["head"]["ref"] == name):
                kind = "CLOSED_UNMERGED_RC7_EXPERIMENT"
        if kind:
            candidates.append((name, sha, kind))
    return sorted(candidates)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete only the reviewed and verified branch refs")
    args = ap.parse_args()
    if not shutil.which("gh"):
        sys.exit("STOP: GitHub CLI (gh) not installed; no change made.")
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode:
        sys.exit("STOP: GitHub CLI not authenticated; no change made.")
    pulls = pages("repos/" + REPO + "/pulls?state=all&per_page=100")
    branches = pages("repos/" + REPO + "/branches?per_page=100")
    selected = select_candidates(branches, pulls)
    print("MODE=" + ("EXPLICIT_APPLY" if args.apply else "DRY_RUN"))
    print("REPO=" + REPO)
    print("ELIGIBLE_BRANCHES=" + str(len(selected)))
    deleted = 0
    for name, sha, kind in selected:
        print("CANDIDATE=%s HEAD=%s CLASS=%s" % (name, sha[:12], kind), flush=True)
        if not args.apply:
            continue
        ref = "repos/" + REPO + "/git/ref/heads/" + quote(name, safe="/")
        latest = json.loads(api(ref))
        if latest.get("object", {}).get("sha") != sha:
            print("SKIP_MOVED=" + name, flush=True)
            continue
        api("repos/" + REPO + "/git/refs/heads/" + quote(name, safe="/"),
            "--method", "DELETE")
        deleted += 1
        print("DELETED=" + name, flush=True)
    print("DELETED_COUNT=" + str(deleted))
    if not args.apply:
        print("NO_CHANGES_MADE=true")


if __name__ == "__main__":
    main()
