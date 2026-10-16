#!/usr/bin/env python3
"""R23B2 one-shot authenticated GET acceptance: exact sidecar, then rollback.

Run ONLY as root via sudo from checked-out immutable GitHub commit on HomeDocker.
Without --apply, strictly read-only preflight. With explicit confirmation:
- creates one private Unix socket directory and a rootless host relay
- creates one read-only, capless Docker sidecar sharing R23B netns (NOT R23B)
- asks for normal AIO/Jellyfin username/password via existing B1V script
- immediately removes the sidecar, host relay and exact owned directory
- both relay roles enforce TTL<=600 even if parent/SSH is interrupted.
Not atomic against SIGKILL/power loss: independent cleanup checks provided.
"""
import argparse
import json
import os
import pwd
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parent.parent
RELAY=ROOT/"scripts/r23b2-experimental-aio-unix-get-relay.py"
AUDIT=ROOT/"scripts/audit-r23b2-host-relay-prereqs.py"
B1V=ROOT/"scripts/audit-v140-r23d2b1v-real-client-readonly.py"
SOCKDIR=Path("/run/homedocker-r23b2-aio")
SOCKET=SOCKDIR/"readonly.sock"
STAGED=SOCKDIR/"relay.py"
CANARY="homedocker-v140-r23b-canary"
SIDECAR="homedocker-r23b2-single-trial"
LABEL="homedocker.r23b2.trial"
IMAGE="python@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"
EXPECTED_IMAGE_ID="sha256:979e5a8a56846771598d47ebf1f8a75b46155104e168a0b237c0783c1dedff8a"
DIGEST="python@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"
TTL=600

class Stop(Exception):pass
def cmd(*args,timeout=15,check=True):
    r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if check and r.returncode!=0:
        raise Stop("COMMAND_UNAVAILABLE_"+args[0].upper().replace("-","_"))
    return r
def inspect_one(kind,name):
    r=cmd("docker",kind,"inspect",name,check=False)
    if r.returncode!=0:return None
    try:
        val=json.loads(r.stdout)
        return val[0] if isinstance(val,list) and len(val)==1 else None
    except (ValueError,TypeError):
        return None
def owner_ok(container):
    if container is None:return False
    c=container
    h=c.get("HostConfig") or {}
    labels=(c.get("Config") or {}).get("Labels") or {}
    mount=c.get("Mounts") or []
    return (c.get("Name")=="/"+SIDECAR and labels.get(LABEL)=="ephemeral-readonly"
            and h.get("Privileged") is False and h.get("ReadonlyRootfs") is True
            and (h.get("RestartPolicy") or {}).get("Name")=="no"
            and h.get("NetworkMode","").startswith("container:")
            and (h.get("PortBindings") or {})=={}
            and len(mount)==1 and mount[0].get("RW") is False
            and mount[0].get("Source")==str(SOCKDIR))
def prereq():
    result=cmd("python3","-B",str(AUDIT),timeout=20)
    try:
        data=json.loads(result.stdout)
    except ValueError:
        raise Stop("PREFLIGHT_JSON_UNAVAILABLE")
    if data.get("audit")!="R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT" or (
            data.get("trial_environment_ready") is not True):
        raise Stop("HOST_READONLY_PREFLIGHT_NOT_PASS")
    image=inspect_one("image","python:3.12-alpine")
    if not image or image.get("Id")!=EXPECTED_IMAGE_ID or (
            DIGEST not in (image.get("RepoDigests") or [])):
        raise Stop("PINNED_LOCAL_PYTHON_IMAGE_DIGEST_MISMATCH")
    if inspect_one("container",SIDECAR) is not None:
        raise Stop("EXACT_SIDECAR_NAME_OCCUPIED")
    return data

def verify_host_dir():
    try:
        parent=os.lstat(SOCKDIR)
        stage=os.lstat(STAGED)
    except OSError:
        return False
    return (stat.S_ISDIR(parent.st_mode) and parent.st_uid==65534
            and stat.S_IMODE(parent.st_mode)==0o700
            and stat.S_ISREG(stage.st_mode) and stage.st_uid==65534
            and stat.S_IMODE(stage.st_mode)==0o600)

def ensure_writable_stage():
    if os.path.lexists(SOCKDIR):
        raise Stop("PRIVATE_DIR_NOT_AVAILABLE")
    SOCKDIR.mkdir(mode=0o700)
    os.chown(SOCKDIR,65534,65534)
    os.chmod(SOCKDIR,0o700)
    raw=RELAY.read_bytes()
    if len(raw)>30000:raise Stop("RELAY_SOURCE_TOO_LARGE")
    fd=os.open(STAGED,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(fd,raw)
    finally:
        os.close(fd)
    os.chown(STAGED,65534,65534)
    if not verify_host_dir():
        raise Stop("HOST_RELAY_PRIVATE_STAGE_INVALID")

def do_cleanup(expected_cid=None,host=None):
    """Never rm unrelated objects, volumes, networks or an unowned path."""
    safe=True
    c=inspect_one("container",SIDECAR)
    if c is not None:
        if not owner_ok(c) or (expected_cid is not None and
                              c.get("Id")!=expected_cid):
            print("R23B2_CLEANUP_STOP_SIDECAR_OWNERSHIP_DRIFT",file=sys.stderr)
            safe=False
        else:
            r=cmd("docker","rm","-f",SIDECAR,timeout=25,check=False)
            if r.returncode!=0 or inspect_one("container",SIDECAR):
                safe=False
    if host is not None and host.poll() is None:
        host.terminate()
        try:host.wait(timeout=4)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=3)
    if os.path.lexists(SOCKDIR):
        if not verify_host_dir():
            # Supports partially-created empty directory only if exact owner.
            st=os.lstat(SOCKDIR)
            if not(stat.S_ISDIR(st.st_mode) and st.st_uid==65534
                   and stat.S_IMODE(st.st_mode)==0o700):
                print("R23B2_CLEANUP_STOP_SOCKET_DIR_OWNERSHIP_DRIFT",
                      file=sys.stderr)
                return False
        try:
            for path in (SOCKET,STAGED):
                if os.path.lexists(path):
                    st=os.lstat(path)
                    if st.st_uid!=65534 or not (
                            (path==SOCKET and stat.S_ISSOCK(st.st_mode)) or
                            (path==STAGED and stat.S_ISREG(st.st_mode))):
                        safe=False
                    else:
                        path.unlink()
            SOCKDIR.rmdir()
        except OSError:
            safe=False
    return safe

def host_relay():
    return subprocess.Popen(
        ["setpriv","--reuid","65534","--regid","65534","--clear-groups",
         "python3","-B",str(STAGED),"--role","host",
         "--ttl-seconds",str(TTL)],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,close_fds=True)
def wait_socket(host):
    for _ in range(55):
        if host.poll() is not None:
            raise Stop("HOST_RELAY_PROCESS_EXITED")
        if os.path.lexists(SOCKET):
            st=os.lstat(SOCKET)
            if stat.S_ISSOCK(st.st_mode) and st.st_uid==65534 and (
                    stat.S_IMODE(st.st_mode)==0o600):
                return
            raise Stop("UNIX_SOCKET_OWNER_OR_MODE_INVALID")
        time.sleep(.1)
    raise Stop("HOST_RELAY_SOCKET_NOT_READY")
def start_sidecar(canary_id):
    cmd("docker","create","--name",SIDECAR,"--pull","never",
        "--network","container:"+canary_id,"--restart","no",
        "--user","65534:65534","--read-only","--cap-drop","ALL",
        "--security-opt","no-new-privileges","--pids-limit","64",
        "--memory","96m","--label",LABEL+"=ephemeral-readonly",
        "--mount",("type=bind,src="+str(SOCKDIR)+",dst="+str(SOCKDIR)+
                   ",readonly"),
        IMAGE,"python3","-B",str(STAGED),"--role","sidecar",
        "--ttl-seconds",str(TTL),timeout=35)
    obj=inspect_one("container",SIDECAR)
    if not owner_ok(obj) or obj["HostConfig"]["NetworkMode"] not in (
            "container:"+canary_id,"container:"+CANARY):
        raise Stop("SIDECAR_OWNERSHIP_MISMATCH_AFTER_CREATE")
    cmd("docker","start",SIDECAR,timeout=30)
    obj=inspect_one("container",SIDECAR)
    if not owner_ok(obj) or not obj["State"]["Running"]:
        raise Stop("SIDECAR_NOT_RUNNING")
    return obj["Id"]
def probe_netns(canary_pid):
    # We enter ONLY the already audited canary network namespace, not its
    # filesystem/mount namespaces. 401 is expected without auth: no client
    # token is sent to the relay during readiness.
    script=("import http.client,sys;"
            "c=http.client.HTTPConnection('127.0.0.1',9999,timeout=2);"
            "c.request('GET','/jellyfin/Users/Me');"
            "r=c.getresponse();sys.exit(0 if r.status==401 else 4)")
    for _ in range(45):
        p=cmd("nsenter","-t",str(canary_pid),"-n",
              "python3","-B","-c",script,timeout=4,check=False)
        if p.returncode==0:return
        time.sleep(.15)
    raise Stop("CANARY_NAMESPACE_LOCAL_9999_NOT_READY")
def run_b1v_operator():
    if not sys.stdin.isatty():
        raise Stop("INTERACTIVE_SSH_TTY_REQUIRED")
    if "SUDO_UID" not in os.environ or "SUDO_GID" not in os.environ:
        raise Stop("SUDO_USER_CONTEXT_REQUIRED")
    uid=int(os.environ["SUDO_UID"])
    gid=int(os.environ["SUDO_GID"])
    if uid<=0 or gid<=0:
        raise Stop("DO_NOT_RUN_AS_ROOT_INTERACTIVE_USER")
    account=pwd.getpwuid(uid)
    def demote():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    env={k:v for k,v in os.environ.items() if k not in (
        "PYTHONPATH","PYTHONSTARTUP","PYTHONINSPECT","PYTHONWARNINGS",
        "LD_PRELOAD","LD_LIBRARY_PATH")}
    env["HOME"]=account.pw_dir
    env["PYTHONDONTWRITEBYTECODE"]="1"
    print("R23B2_RELAY_READY_LOCAL_ONLY_EPHEMERAL_B1V_START",flush=True)
    try:
        p=subprocess.run(["python3","-B",str(B1V),"--compare",
             "--confirm","LIVE_AUTH_LOGIN_GET_ONLY"],
             timeout=480,check=False,env=env,preexec_fn=demote)
    except subprocess.TimeoutExpired:
        raise Stop("B1V_USER_INPUT_TIMEOUT")
    return p.returncode

def trial():
    if os.geteuid()!=0:raise Stop("SUDO_ROOT_REQUIRED_FOR_EPHEMERAL_DOCKER")
    if not all(x.is_file() for x in (RELAY,AUDIT,B1V)):
        raise Stop("CHECKED_OUT_SOURCE_MISSING")
    prereq()
    base=inspect_one("container",CANARY)
    if not base or not base["State"]["Running"] or base["State"]["Pid"]<2:
        raise Stop("CANARY_NOT_RUNNING")
    base_id=base["Id"]
    pid=base["State"]["Pid"]
    stage_created=False
    host=None
    sidecar_cid=None
    result=5
    try:
        # Stage cleanup is armed BEFORE any on-disk creation, including
        # partial mkdir/source-copy failures.
        stage_created=True
        ensure_writable_stage()
        host=host_relay()
        wait_socket(host)
        sidecar_cid=start_sidecar(base_id)
        probe_netns(pid)
        if inspect_one("container",CANARY)["Id"]!=base_id:
            raise Stop("CANARY_CHANGED_BEFORE_READONLY_ACCEPTANCE")
        result=run_b1v_operator()
        if result==0:
            print("R23B2_B1V_ACTUAL_NEXTUP_READONLY_ACCEPTANCE_PASS",flush=True)
        else:
            print("R23B2_B1V_ACCEPTANCE_INCONCLUSIVE_OR_FAILED_"+str(result),
                  flush=True)
    finally:
        if stage_created:
            safe=do_cleanup(sidecar_cid,host)
            if inspect_one("container",CANARY) is None or (
                    inspect_one("container",CANARY)["Id"]!=base_id):
                safe=False
            print("R23B2_EPHEMERAL_EXACT_ROLLBACK_"+("PASS" if safe else "FAILED"),
                  flush=True)
            if not safe: raise Stop("EXACT_ROLLBACK_NOT_VERIFIED")
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply",action="store_true")
    p.add_argument("--confirm",default="")
    args=p.parse_args()
    try:
        if os.geteuid()!=0:raise Stop("SUDO_ROOT_REQUIRED")
        if not args.apply:
            prereq()
            print("R23B2_PINNED_IMAGE_PREFLIGHT_PASS_NO_CHANGES")
            return 0
        if args.confirm!="R23B2_SINGLE_600S_EPHEMERAL_ACCEPTANCE":
            raise Stop("EXPLICIT_TRIAL_CONFIRMATION_REQUIRED")
        return trial()
    except (Stop,OSError,ValueError,subprocess.TimeoutExpired) as exc:
        if isinstance(exc,Stop):
            print("R23B2_STOP_"+str(exc),file=sys.stderr)
        else:
            print("R23B2_STOP_SAFE_LOCAL_TRIAL_EXCEPTION",file=sys.stderr)
        return 5

if __name__=="__main__":
    raise SystemExit(main())
