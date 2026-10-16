#!/usr/bin/env python3
"""R23B2 HomeDocker PRE-APPLY SAFETY AUDIT. INSPECT ONLY; no Docker mutation."""
import json
import os
import pwd
import subprocess
import sys

C="homedocker-v140-r23b-canary"
NET="homedocker_v140_r23b_local"
SENTINEL="JELLYFIN_GATEWAY_UPSTREAM_URL=http://127.0.0.1:9999/jellyfin"
DIRECTORY="/run/homedocker-r23b2-aio"
SIDE_IMAGE="python:3.12-alpine"

def docker(*args):
    result=subprocess.run(("docker",)+args,timeout=12,capture_output=True,
                          text=True,check=False)
    if result.returncode:return None
    return result.stdout
def see(*args):
    result=subprocess.run(args,timeout=8,capture_output=True,text=True,
                          check=False)
    return result.stdout if result.returncode==0 else None
def main():
    c_raw=docker("container","inspect",C)
    n_raw=docker("network","inspect",NET)
    if not c_raw or not n_raw:
        print('R23B2_PRECHECK_STOP_CONTAINER_OR_NETWORK_NOT_FOUND')
        return 5
    try:
        c=json.loads(c_raw)[0]
        n=json.loads(n_raw)[0]
    except (ValueError,IndexError,KeyError,TypeError):
        print("R23B2_PRECHECK_STOP_INVALID_DOCKER_INSPECT")
        return 5
    state=c.get("State") or {}
    conf=c.get("Config") or {}
    host=c.get("HostConfig") or {}
    mapping=(c.get("NetworkSettings") or {}).get("Networks") or {}
    userns=host.get("UsernsMode") or ""
    privileged=host.get("Privileged")
    restart=(host.get("RestartPolicy") or {}).get("Name")
    pb=host.get("PortBindings") or {}
    expected_ports={("7000/tcp","17041"),("7101/tcp","17141")}
    mapped={(inner,str(b.get("HostPort"))) for inner,arr in pb.items()
            for b in (arr or []) if b}
    local_only=bool(pb) and all(
        str(b.get("HostIp")) in ("127.0.0.1","::1")
        for arr in pb.values() for b in (arr or []))
    try:
        nobody=pwd.getpwnam("nobody").pw_uid
    except KeyError:
        nobody=-1
    pid=state.get("Pid")
    port_free=False
    if os.geteuid()==0 and isinstance(pid,int) and pid>1:
        listen=see("nsenter","-t",str(pid),"-n","ss","-H","-lnt",
                   "( sport = :9999 )")
        port_free=listen is not None and not listen.strip()
    report={
      "audit":"R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT",
      "running":bool(state.get("Running")),
      "canary_only_private_network":set(mapping)=={NET},
      "canary_image_and_config_unchanged":(
          conf.get("Labels",{}).get("homedocker.phase")=="r23b1" and
          SENTINEL in (conf.get("Env") or []) and
          host.get("ReadonlyRootfs") is True and
          privileged is False and restart=="no"),
      "localhost_gateway_ports_only":local_only,
      "canary_gateway_17141_published":any(v=="17141" for _,v in mapped),
      "network_internal_false":n.get("Internal") is False,
      "network_masquerade_false":(
        (n.get("Options") or {}).get(
            "com.docker.network.bridge.enable_ip_masquerade")=="false"),
      "netns_port9999_free":port_free,
      "private_socket_path_available":not os.path.lexists(DIRECTORY),
      "nobody_uid_65534":nobody==65534,
      "user_namespace_remapping_not_requested":userns=="",
      "python_312_alpine_already_local":docker(
          "image","inspect",SIDE_IMAGE) is not None,
      "host_resources_modified":0,
      "host_secrets_or_docker_env_displayed":False,
      "production_route_modified":False,
    }
    required=[k for k in report if k not in
      ("audit","host_resources_modified","host_secrets_or_docker_env_displayed",
       "production_route_modified")]
    report["trial_environment_ready"]=all(report[k] is True for k in required)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report["trial_environment_ready"] else 4

if __name__=="__main__":
    try:sys.exit(main())
    except (OSError,subprocess.TimeoutExpired):
        print("R23B2_PRECHECK_STOP_READONLY_COMMAND_UNAVAILABLE")
        raise SystemExit(5) from None
