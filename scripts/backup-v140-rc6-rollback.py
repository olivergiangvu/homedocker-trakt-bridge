#!/usr/bin/env python3
"""R22 HomeDocker RC6 recovery snapshot + isolated SQLite restore rehearsal.

Dry-run by default. An explicitly confirmed APPLY copies only the current
Bridge's root-only Compose/.env sources and uses sqlite3.online_backup on the
running RC6 database, to a new mode-0700 directory under /var/backups.
The rehearsal restores that snapshot to a *different* disposable SQLite file
under the same private directory and validates integrity; it never connects
to any production service with the restored data.

The backup contains secrets and watch metadata: never upload it unencrypted
or share its path/content/logs. It is NOT a full-system or AIO backup.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys

RC6_REF = ("ghcr.io/olivergiangvu/homedocker-trakt-bridge@"
           "sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2")
BACKUP_ROOT = Path("/var/backups/homedocker-trakt-bridge/r22")
CONFIRM = "CREATE_PRIVATE_RC6_RECOVERY_SNAPSHOT_ONLY"
REQUIRED = {"trakt-bridge", "aiostreams", "homedocker-resume-contract-canary"}

class Stop(Exception):
    pass

def cmd(*args):
    cp = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if cp.returncode:
        raise Stop("inspection failed")
    return cp.stdout.strip()

def inspect(name):
    try:
        data = json.loads(cmd("docker", "container", "inspect", name))
        if len(data) != 1 or data[0].get("Name") != "/" + name:
            raise Stop("container identity mismatch")
        return data[0]
    except (ValueError, TypeError, KeyError):
        raise Stop("malformed Docker inspect") from None

def regular(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or not stat.S_ISREG(p.stat().st_mode):
        raise Stop("required regular source file missing or symlinked")
    return p

def collect():
    bridge = inspect("trakt-bridge")
    aio = inspect("aiostreams")
    old = inspect("homedocker-resume-contract-canary")
    if not all(x.get("State", {}).get("Running") for x in (bridge, aio, old)):
        raise Stop("production or retained canary is not running")
    if (bridge.get("Config") or {}).get("Image") != RC6_REF:
        raise Stop("RC6 immutable reference changed")
    if (aio.get("Config") or {}).get("Image") != "ghcr.io/viren070/aiostreams:latest":
        raise Stop("AIO configured image changed")
    image_id = bridge.get("Image")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise Stop("RC6 image ID unavailable")
    labels = (bridge.get("Config") or {}).get("Labels") or {}
    wd = labels.get("com.docker.compose.project.working_dir")
    sources = labels.get("com.docker.compose.project.config_files")
    service = labels.get("com.docker.compose.service")
    project = labels.get("com.docker.compose.project")
    if not wd or not sources or service != "trakt-bridge" or not project:
        raise Stop("exact Compose provenance missing")
    working_dir = Path(wd)
    if working_dir.is_symlink() or not working_dir.is_dir():
        raise Stop("Compose working directory untrusted")
    root = working_dir.resolve(strict=True)
    # No absolute paths outside the original, authenticated Compose project.
    compose = []
    for raw in sources.split(","):
        raw = raw.strip()
        if not raw:
            raise Stop("empty Compose file entry")
        p = regular(raw if Path(raw).is_absolute() else root / raw)
        if not p.resolve(strict=True).is_relative_to(root):
            raise Stop("Compose source outside project directory")
        compose.append(p)
    if not compose or len(compose) > 12 or len(set(compose)) != len(compose):
        raise Stop("Compose source list ambiguous")
    env = regular(root / ".env")
    mounts = [m for m in bridge.get("Mounts") or []
              if m.get("Destination") == "/app/data"
              and m.get("Type") in ("volume", "bind")]
    if len(mounts) != 1:
        raise Stop("Bridge DB mount missing or ambiguous")
    db = regular(Path(mounts[0]["Source"]) / "bridge.db")
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=6)) as con:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise Stop("source SQLite quick_check non-ok")
        schema = con.execute("PRAGMA user_version").fetchone()[0]
        if schema != 1:
            raise Stop("RC6 SQLite schema unexpected")
    return {"db": db, "compose": compose, "env": env,
            "image_ref": RC6_REF, "image_id": image_id,
            "compose_project": project, "working_dir": str(root),
            "schema": schema}

def check_backup_root(path):
    p = Path(path)
    if not p.is_absolute() or p.is_symlink():
        raise Stop("unsafe backup root")
    for ancestor in [p, *p.parents]:
        if ancestor.exists() and ancestor.is_symlink():
            raise Stop("symlink in backup directory path")
    # Avoid writes to a relative or operator-controlled default shell path.
    return p

def private_dir(path):
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(path, 0o700)

def private_copy(source, dest):
    # copyfile creates the target; chmod immediately after, without ever
    # deliberately inheriting permissive source modes.
    with open(source, "rb") as src:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
    os.chmod(dest, 0o600)

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def logical_fingerprint(path):
    """SHA-256 of the complete SQLite logical dump; never print/return rows."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=8)) as conn:
        conn.execute("PRAGMA query_only=ON")
        result = hashlib.sha256()
        for statement in conn.iterdump():
            result.update(statement.encode("utf-8"))
            result.update(b"\n")
        return result.hexdigest()

def check_integrity(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=8)) as conn:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Stop("snapshot SQLite integrity check failed")
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise Stop("snapshot SQLite schema mismatch")
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        must_have = {"profiles", "media_cache"}
        if not must_have <= {r[0] for r in tables}:
            raise Stop("snapshot SQLite expected tables missing")
        return {
            "profile_rows": conn.execute("SELECT count(*) FROM profiles").fetchone()[0],
            "identity_rows": conn.execute(
                "SELECT count(*) FROM media_cache "
                "WHERE cache_key LIKE 'identity-%'"
            ).fetchone()[0],
        }

def snapshot(src, root=BACKUP_ROOT, stamp=None):
    if os.geteuid() != 0:
        raise Stop("R22 apply requires sudo/root")
    base = check_backup_root(root)
    # Never weaken existing directory permissions, and refuse symlinks.
    base_parent = base.parent
    if base_parent.is_symlink():
        raise Stop("unsafe backup parent")
    if not base_parent.exists():
        base_parent.mkdir(parents=True, mode=0o700, exist_ok=False)
    if base.exists():
        if not base.is_dir() or stat.S_IMODE(base.stat().st_mode) & 0o077:
            raise Stop("backup parent must be private 0700")
    else:
        private_dir(base)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", stamp):
        raise Stop("invalid backup timestamp")
    folder = base / stamp
    private_dir(folder)
    # Deliberately leave an INCOMPLETE private snapshot for inspection if a
    # failure occurs; never remove or overwrite existing recovery material.
    copied = {}
    private_copy(src["env"], folder / "compose.env.rc6")
    copied["compose.env.rc6"] = src["env"]
    for n, file in enumerate(src["compose"]):
        name = f"compose-{n:02d}.rc6"
        private_copy(file, folder / name)
        copied[name] = file
    db_copy = folder / "bridge.db"
    fd = os.open(db_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(src["db"].resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=10)) as original:
        with closing(sqlite3.connect(str(db_copy), timeout=10)) as backup:
            original.backup(backup, pages=128, sleep=0.025)
    os.chmod(db_copy, 0o600)
    original_counts = check_integrity(db_copy)
    rehearsal = folder / ".restore-rehearsal.db"
    with closing(sqlite3.connect(db_copy.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=8)) as backed_up:
        with closing(sqlite3.connect(str(rehearsal), timeout=8)) as restored:
            backed_up.backup(restored, pages=128)
    os.chmod(rehearsal, 0o600)
    if check_integrity(rehearsal) != original_counts:
        raise Stop("restore rehearsal row-count mismatch")
    if logical_fingerprint(db_copy) != logical_fingerprint(rehearsal):
        raise Stop("restore rehearsal logical content differs from snapshot")
    rehearsal.unlink()
    manifest = {
        "schema_version": 1,
        "created_at_utc": stamp,
        "source_container": "trakt-bridge",
        "image_ref": src["image_ref"],
        "image_id": src["image_id"],
        "compose_project": src["compose_project"],
        "compose_working_dir": src["working_dir"],
        "source_files": {name: str(path) for name, path in copied.items()},
        "sha256": {p.name: digest(p) for p in folder.iterdir() if p.is_file()},
        "sqlite_snapshot": original_counts,
        "restore_rehearsal": "isolated_sqlite_backup_and_integrity_pass",
        "production_service_modified": False,
    }
    fd = os.open(folder / "manifest.json",
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(folder / "manifest.json", 0o600)
    if any(stat.S_IMODE(p.stat().st_mode) & 0o077 for p in folder.iterdir()):
        raise Stop("private recovery artifact permissions weakened")
    return {
        "status": "PRIVATE_SNAPSHOT_AND_SQLITE_RESTORE_REHEARSAL_PASS",
        "directory_mode": "0700",
        "artifact_mode": "0600",
        "snapshot_contains_private_secrets": True,
        "bridge_sqlite_integrity": "ok",
        "rehearsal_integrity": "ok",
        "rehearsal_logical_content_match": True,
        "compose_files_copied": len(src["compose"]),
        "env_copied": True,
        "rollback_image_recorded": True,
        "production_db_writes": 0,
        "production_container_restarts": 0,
        "nginx_or_network_changes": 0,
        "aio_history_modified": False,
        "external_backup_or_full_restore_verified": False,
        "v14_pilot_deployment_authorized": False,
        "backup_path_or_secrets_printed": False,
    }

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", default="")
    args = p.parse_args()
    try:
        info = collect()
        if not args.apply:
            print(json.dumps({
                "audit": "R22_RC6_SNAPSHOT_PREFLIGHT",
                "mode": "DRY_RUN",
                "rc6_image_pinned": True,
                "production_bridge_sqlite_quick_check": "ok",
                "compose_files_found": len(info["compose"]),
                "root_only_snapshot_created": False,
                "rollback_copy_verified": False,
                "writes": 0,
                "next": "explicit --apply --confirm after operator review",
            }, indent=2))
            print("R22_DRY_RUN_PASS")
            return
        if args.confirm != CONFIRM:
            raise Stop("explicit R22 snapshot confirmation missing")
        result = snapshot(info)
        print(json.dumps(result, indent=2))
        print("R22_PRIVATE_RECOVERY_SNAPSHOT=PASS")
    except (Stop, OSError, sqlite3.Error, ValueError, TypeError,
            subprocess.TimeoutExpired):
        print("STOP: R22 recovery preflight/backup failed; production not changed",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
