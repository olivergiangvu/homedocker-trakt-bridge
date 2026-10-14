#!/usr/bin/env python3
"""Bridge-only v1.3.0 HomeDocker rollout with guarded Compose discovery.

Defaults to --preflight (read-only). --apply explicitly pulls Stable image,
takes an SQLite online backup and saves current Compose/.env (secret-bearing),
pins Bridge ONLY to the stable digest, restarts Bridge ONLY, validates, and
attempts to restore the original image reference on failure.

This script does NOT touch AIOStreams, nginx or Trakt history, nor restore
the old database over a live DB. Requires current RC6 pinned baseline and a
local Compose stack interpolating TRAKT_BRIDGE_IMAGE from a .env file.
Portainer-only/in-memory stacks intentionally stop rather than guessing.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler, Request

REPO = "olivergiangvu/homedocker-trakt-bridge"
IMAGE = "ghcr.io/olivergiangvu/homedocker-trakt-bridge"
RC6 = IMAGE + "@sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2"
TAG = IMAGE + ":1.3.0"
BRIDGE = "trakt-bridge"
AIO = "aiostreams"
OPENER = build_opener(ProxyHandler({}))


class Stop(Exception):
    pass


def run(*args, env=None, output=True):
    p = subprocess.run(args, text=True, capture_output=True, env=env)
    if p.returncode:
        raise Stop("command failed: " + " ".join(str(a) for a in args[:3]))
    return p.stdout if output else ""


def docker(*args):
    return run("docker", *args)


def container(name):
    data = json.loads(docker("container", "inspect", name))
    if len(data) != 1:
        raise Stop("exact Docker container not found: " + name)
    return data[0]


def get_json(endpoint):
    req = Request("http://127.0.0.1:7000/" + endpoint, headers={"Accept":"application/json"})
    try:
        with OPENER.open(req, timeout=4) as response:
            if response.status != 200:
                raise Stop("Bridge local endpoint unavailable: " + endpoint)
            return json.loads(response.read(100000))
    except Exception as e:
        raise Stop("Bridge local endpoint check failed: " + endpoint) from e


def check_ready(version):
    h, r = get_json("health"), get_json("readiness")
    if not (h.get("status") == "ok" and h.get("version") == version
            and r.get("status") == "ready" and r.get("ready") is True
            and r.get("database") == "ok" and r.get("schemaVersion") == 1
            and r.get("schemaExpected") == 1 and r.get("connectedProfiles") == 1):
        raise Stop("Bridge health/readiness failed expected " + version)


def check_flags(info):
    env = dict(e.split("=", 1) for e in info["Config"]["Env"] if "=" in e)
    if not (env.get("PULL_IDENTITY_MODE") == "trakt"
            and env.get("AIO_RECONCILER_MODE") == "compare"
            and env.get("CANONICAL_ON_DEMAND_VERIFY", "false").lower() == "false"):
        raise Stop("Bridge existing canonical/compare-only flags changed")
    print("SAFE_FLAGS=TRAKT_COMPARE_WITHOUT_ONDEMAND")


def inspect_stack():
    # Production Compose lives at /opt/homedocker/stacks and its .env
    # intentionally has root-only access. Preflight must support that
    # without printing/copying credentials, chown or chmod.
    run("sudo", "-v")
    info = container(BRIDGE)
    labels = info["Config"].get("Labels") or {}
    project = labels.get("com.docker.compose.project")
    wd = labels.get("com.docker.compose.project.working_dir")
    config = labels.get("com.docker.compose.project.config_files")
    service = labels.get("com.docker.compose.service")
    if service != BRIDGE or not (project and wd and config):
        raise Stop("current Bridge is not a directly reproducible Compose service")
    root = Path(wd)
    paths = [Path(v) for v in config.split(",")]
    if not root.is_dir() or not paths or any(not p.is_file() or p.is_symlink() for p in paths):
        raise Stop("Compose configuration inaccessible; manage via Portainer instead")
    envfile = root / ".env"
    if not envfile.is_file() or envfile.is_symlink():
        raise Stop("Compose project .env missing or symlink; deployment aborted")
    if "TRAKT_BRIDGE_IMAGE" in os.environ:
        raise Stop("TRAKT_BRIDGE_IMAGE shell env would override .env")
    # Ask root to inspect only *the presence and uniqueness* of image
    # interpolation; never return .env contents, file modes or credentials.
    check = r"""
from pathlib import Path
import re,sys
envfile,*files=sys.argv[1:]
p=Path(envfile)
if not p.is_file() or p.is_symlink():
    raise SystemExit("STOP: .env is not a regular file")
data=p.read_text(encoding="utf-8")
if len(re.findall(r"(?m)^\s*TRAKT_BRIDGE_IMAGE\s*=",data))>1:
    raise SystemExit("STOP: duplicate TRAKT_BRIDGE_IMAGE")
if not any(re.search(r"\$\{TRAKT_BRIDGE_IMAGE(?=[:}?])",Path(path).read_text(encoding="utf-8")) for path in files):
    raise SystemExit("STOP: Compose does not interpolate TRAKT_BRIDGE_IMAGE")
print("ROOT_ONLY_COMPOSE_ENV=VALIDATED_WITHOUT_SECRET_OUTPUT")
"""
    summary = run("sudo", "-n", "python3", "-c", check,
                  str(envfile), *(str(p) for p in paths))
    if "ROOT_ONLY_COMPOSE_ENV=VALIDATED_WITHOUT_SECRET_OUTPUT" not in summary:
        raise Stop("root-only Compose source validation failed")
    print("ROOT_ONLY_COMPOSE_ENV=VALIDATED_WITHOUT_SECRET_OUTPUT")
    # Use the SAME privileged Compose context for read-only config, apply,
    # and rollback. Nonroot docker compose cannot read protected .env.
    cmd = ["sudo", "-n", "docker", "compose", "-p", project, "--project-directory", wd]
    for p in paths:
        cmd += ["-f", str(p)]
    render = json.loads(run(*cmd, "config", "--format", "json"))
    image_rendered = (render.get("services") or {}).get(BRIDGE, {}).get("image")
    current = info["Config"]["Image"]
    if image_rendered != current:
        raise Stop("Compose rendered image does not equal current image; manager drift")
    mounts = [m for m in info.get("Mounts", []) if m.get("Destination") == "/app/data"]
    if len(mounts) != 1 or not Path(mounts[0]["Source"]).is_dir():
        raise Stop("Bridge /app/data persistent mount is missing/ambiguous")
    return info, cmd, envfile, paths, mounts[0]["Source"]


def backup(src_db, envfile, compose_files, prev_ref, aio_id):
    run("sudo", "-v")
    path = (Path.home() / ".local/share/homedocker/release-backups/v1.3.0"
            / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(path, 0o700)
    run("sudo", "cp", "-p", str(envfile), str(path / "compose.env.rc6"))
    for i, file in enumerate(compose_files):
        run("sudo", "cp", "-p", str(file), str(path / ("compose-%s.rc6" % i)))
    db_backup = path / "bridge.db"
    code = (
        "import sqlite3,sys;from pathlib import Path;"
        "p=Path(sys.argv[1]);"
        "assert p.is_file() and not p.is_symlink();"
        "old=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True);"
        "new=sqlite3.connect(sys.argv[2]);"
        "old.backup(new);"
        "assert new.execute('PRAGMA integrity_check').fetchone()[0]=='ok';"
        "assert new.execute('PRAGMA user_version').fetchone()[0]==1;"
        "new.close();old.close()"
    )
    run("sudo", "python3", "-c", code, str(Path(src_db)/"bridge.db"), str(db_backup))
    run("sudo", "chmod", "600", str(db_backup), str(path / "compose.env.rc6"))
    (path / "rollback-reference.txt").write_text(
        "BRIDGE_RC6_IMAGE=" + prev_ref + "\nAIO_IMAGE_ID=" + aio_id + "\n"
    )
    os.chmod(path / "rollback-reference.txt", 0o600)
    return path


def set_image(envfile, ref):
    # Elevated mutation only to this one variable, preserving original mode/owner.
    code = r"""
import os,re,stat,sys,tempfile
path,ref=sys.argv[1:]
if os.path.islink(path): raise SystemExit("STOP: .env symlink")
st=os.stat(path)
if not stat.S_ISREG(st.st_mode): raise SystemExit("STOP: .env not a regular file")
with open(path, encoding="utf-8") as f: data=f.read()
m=re.findall(r"(?m)^TRAKT_BRIDGE_IMAGE=.*$",data)
if len(m)>1: raise SystemExit("STOP: repeated image variables")
if m:
  data=re.sub(r"(?m)^TRAKT_BRIDGE_IMAGE=.*$", "TRAKT_BRIDGE_IMAGE="+ref,data)
else:
  data=data.rstrip("\n")+"\nTRAKT_BRIDGE_IMAGE="+ref+"\n"
fd,tmp=tempfile.mkstemp(prefix=".env.stable.",dir=os.path.dirname(path))
try:
 with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(data)
 os.chmod(tmp,stat.S_IMODE(st.st_mode))
 os.chown(tmp,st.st_uid,st.st_gid)
 os.replace(tmp,path)
finally:
 if os.path.exists(tmp): os.unlink(tmp)
"""
    run("sudo", "python3", "-c", code, str(envfile), ref)


def rc7_identity_guard():
    # The RC7 duplicate-NextUp remedy was an AIO-side dropped preference,
    # NOT a runtime code patch. Require that preference to remain effective
    # both before and after cutover; never issue any Jellyfin POST/write.
    monitor = Path.home() / "monitor-identity-drift.py"
    if not monitor.is_file() or monitor.is_symlink():
        raise Stop("RC7 identity monitor missing: ~/monitor-identity-drift.py")
    raw = run("sudo", "-n", "python3", str(monitor),
              "--json", "--fail-on-drift")
    try:
        state = json.loads(raw)
        pairs = state["pairs"]
        fixture = [p for p in pairs
                   if p.get("alias_imdb") == "tt44094505"
                   and p.get("canonical_imdb") == "tt44051354"]
        pair = fixture[0] if len(fixture) == 1 else {}
        # A legitimate later completed S01E10 may eliminate the active resume.
        # It MUST NOT make the Stable cutover permanently impossible.
        drop_effective = (
            pair.get("status") == "MITIGATED_BY_DROP"
            and pair.get("alias_drop_effective") is True
        )
        old_alias_gone = (
            pair.get("status") == "SINGLE_TIMELINE"
            and pair.get("alias_episode_rows") == 0
        )
        if not (state.get("mode") == "SQLITE_READ_ONLY"
                and state.get("attention") == 0
                and state.get("database_writes") == 0
                and len(fixture) == 1
                and pair.get("canonical_episode_rows", 0) >= 1
                and (drop_effective or old_alias_gone)):
            raise Stop("RC7 dual-IMDb mitigation/canonical timeline not verified")
    except (KeyError, TypeError, ValueError) as exc:
        raise Stop("invalid RC7 read-only identity monitor output") from exc
    print("RC7_IDENTITY=KNOWN_PAIR_SAFE_READONLY")


def stable_image():
    if shutil.which("gh") is None:
        raise Stop("GitHub CLI required to verify published v1.3 release")
    run("gh", "release", "view", "v1.3.0", "--repo", REPO, "--json", "tagName")
    print("GITHUB_RELEASE=CONFIRMED")
    run("docker", "pull", TAG)
    imgs = json.loads(docker("image", "inspect", TAG))
    digests = (imgs[0].get("RepoDigests") or [])
    matching = sorted(d for d in digests if d.startswith(IMAGE+"@sha256:"))
    if not matching:
        raise Stop("stable digest not found in local Docker RepoDigests")
    pinned = matching[0]
    docker("image", "inspect", pinned)
    return pinned


def do_deploy():
    info, compose, envfile, paths, dbdir = inspect_stack()
    old_ref = info["Config"]["Image"]
    aio_info = container(AIO)
    aio_id = aio_info["Image"]
    if old_ref != RC6:
        raise Stop("running Bridge is not the exact qualified RC6 digest")
    if aio_info["Config"]["Image"] != "ghcr.io/viren070/aiostreams:latest":
        raise Stop("AIO upstream image reference not latest")
    check_flags(info)
    check_ready("1.3.0-rc.6")
    rc7_identity_guard()
    docker("image","inspect",info["Image"])
    print("RC6_BASELINE=READY")
    print("COMPOSE_PROJECT=" + (info["Config"]["Labels"] or {}).get("com.docker.compose.project","?"))
    print("COMPOSE_FILES=" + str(len(paths)))
    print("AIOSTREAMS_LATEST_UNCHANGED=true")
    return info, compose, envfile, paths, dbdir, aio_id


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="explicit Bridge-only promotion")
    args = ap.parse_args()
    prior, cmd, envfile, compose_files, dbdir, aio_id = do_deploy()
    if not args.apply:
        print("MODE=PREFLIGHT_READ_ONLY")
        print("NO_CHANGES_MADE=true")
        return
    pinned = stable_image()
    if pinned == RC6:
        raise Stop("published stable digest equals RC6 unexpectedly")
    print("STABLE_GHCR_DIGEST=" + pinned)
    saved = backup(dbdir, envfile, compose_files, prior["Config"]["Image"], aio_id)
    print("SQLITE_ONLINE_BACKUP_VERIFIED=true")
    print("BACKUP_LOCATION=" + str(saved))
    print("BACKUP_CONTAINS_SECRETS=true")

    changed = False
    try:
        set_image(envfile, pinned)
        changed = True
        rendered = json.loads(run(*cmd, "config", "--format", "json"))
        if rendered["services"][BRIDGE]["image"] != pinned:
            raise Stop("Compose did not render exact stable digest")
        print("=== BRIDGE_ONLY_RECREATE ===")
        run(*cmd, "up", "-d", "--no-deps", "--no-build", "--pull", "never", BRIDGE)
        success = False
        for _ in range(45):
            try:
                check_ready("1.3.0")
                success = True
                break
            except Stop:
                time.sleep(2)
        if not success:
            raise Stop("stable health/readiness failed after Bridge-only recreate")
        current = container(BRIDGE)
        if current["Config"]["Image"] != pinned:
            raise Stop("running Bridge not pinned at stable digest")
        if container(AIO)["Image"] != aio_id:
            raise Stop("AIOStreams image changed during Bridge-only deploy")
        check_flags(current)
        rc7_identity_guard()
    except Exception:
        if changed:
            print("DEPLOY_FAILED=AUTOMATIC_RC6_REFERENCE_ROLLBACK_ATTEMPT")
            try:
                run("sudo","cp","-p",str(saved/"compose.env.rc6"),str(envfile))
                run(*cmd,"up","-d","--no-deps","--no-build","--pull","never",BRIDGE)
                check_ready("1.3.0-rc.6")
                print("ROLLBACK_TO_RC6=HEALTHY")
            except Exception:
                print("ROLLBACK_FAILED=MANUAL_RECOVERY_REQUIRED",file=sys.stderr)
        raise
    print("DEPLOYMENT=SUCCESS")
    print("BRIDGE_VERSION=1.3.0")
    print("BRIDGE_PINNED_IMAGE=" + pinned)
    print("AIOSTREAMS_LATEST_NOT_TOUCHED=true")
    print("NEXT=rerun_onhost_identity_monitor_and_Jellyfin_rolling_contract")


if __name__ == "__main__":
    try:
        main()
    except (Stop, subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        sys.exit("STOP: " + str(e))
