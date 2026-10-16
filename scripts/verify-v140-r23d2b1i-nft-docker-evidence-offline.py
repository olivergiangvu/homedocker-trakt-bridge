#!/usr/bin/env python3
"""R23D-2B1I: offline joint nft/Docker evidence verifier with synthetic fixtures.

Reuses reviewed R23D-2A structural attester. Pure Python, no host commands,
file writes, subprocesses, sockets, AIO/Trakt credentials or apply mode.
A passing fixture means structural contract only, never real kernel filtering,
kernel/daemon evidence provenance, packet path or boot-time supervision.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ATTEST=HERE/"attest-v140-r23d-persistent-guard.py"
ORIGINAL_SHA="3c08d06bf08154283d5b548d371729436f9ea392"
ORIGINAL_IMAGE_ID="sha256:98b1e6dc7c235ba7381cd6f798f742a5ecc460580c2f408e8d256927199f300d"
EXACT_IMAGE_TAG="homedocker-trakt-bridge:v140-r23d-"+ORIGINAL_SHA[:12]
NETWORK="homedocker_v140_r23d_local"
CONTAINER="homedocker-v140-r23d-gateway-canary"
BRIDGE="br-r23dgate"
PHASE="r23d-persistent-canary"
SUBNET="172.31.254.0/28"
GATEWAY_IP="172.31.254.1"
CANARY_IP="172.31.254.2"
RELAY_PORT=17420
FIXTURE_ID="a"*64
class Stop(Exception):
    pass

def verifier():
    if not ATTEST.is_file() or ATTEST.is_symlink():
        raise Stop("REVIEWED_STRUCTURAL_VERIFIER_MISSING")
    spec=importlib.util.spec_from_file_location("r23d2b1i_attester",ATTEST)
    if spec is None or spec.loader is None:
        raise Stop("REVIEWED_STRUCTURAL_VERIFIER_MISSING")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def fixture():
    """Entirely synthetic data: NEVER a snapshot read from Docker/kernel."""
    v=verifier()
    r=v.expected_rules(BRIDGE,GATEWAY_IP,CANARY_IP,RELAY_PORT)
    nft={"nftables":[
        {"metainfo":{"version":"1.0.0"}},
        {"table":{"family":"inet","name":v.renderer().TABLE}},
        {"chain":{"family":"inet","table":v.renderer().TABLE,"name":"r23d_input",
                  "type":"filter","hook":"input","prio":-20,"policy":"accept"}},
        {"chain":{"family":"inet","table":v.renderer().TABLE,"name":"r23d_forward",
                  "type":"filter","hook":"forward","prio":-20,"policy":"accept"}},
        *[{"rule":{"family":"inet","table":v.renderer().TABLE,
                   "chain":name,"expr":expr}}
          for name in ("r23d_input","r23d_forward") for expr in r[name]],
    ]}
    c={"Id":FIXTURE_ID,"Name":"/"+CONTAINER,"Image":ORIGINAL_IMAGE_ID,
       "State":{"Running":True},
       "Config":{"Image":EXACT_IMAGE_TAG,"User":"node",
                 "Labels":{"homedocker.phase":PHASE}},
       "HostConfig":{
           "ReadonlyRootfs":True,"Privileged":False,"CapAdd":[],"CapDrop":["ALL"],
           "SecurityOpt":["no-new-privileges"],"NetworkMode":NETWORK,
           "PortBindings":{"7101/tcp":[{"HostIp":"127.0.0.1","HostPort":"17145"}]},
           "RestartPolicy":{"Name":"no"}},
       "NetworkSettings":{"Networks":{NETWORK:{"IPAddress":CANARY_IP}}},
       "Mounts":[{"Type":"bind","Destination":"/run/identity-proof.json",
                  "RW":False,"Source":"/synthetic-only-identity-proof"}]}
    n={"Name":NETWORK,"Driver":"bridge","Internal":False,"EnableIPv6":False,
       "Options":{"com.docker.network.bridge.name":BRIDGE,
                  "com.docker.network.bridge.enable_ip_masquerade":"false"},
       "Labels":{"homedocker.phase":PHASE},
       "IPAM":{"Config":[{"Subnet":SUBNET,"Gateway":GATEWAY_IP}]},
       "Containers":{FIXTURE_ID:{"Name":CONTAINER,"IPv4Address":CANARY_IP+"/28"}}}
    return nft,[c],[n]

def attest_synthetic_evidence(nft_doc,containers,networks):
    """Check shape and immutable pin; ALWAYS fail closed for live authorization.

    Not an API to attest live evidence. The data source is NOT authenticated.
    It deliberately cannot emit a 'runtime-qualified' status.
    """
    v=verifier()
    if (not isinstance(containers,list) or len(containers)!=1
        or not isinstance(containers[0],dict)):
        raise Stop("SYNTHETIC_CONTAINER_EVIDENCE_REJECTED")
    c=containers[0]
    h=c.get("HostConfig") or {}
    conf=c.get("Config") or {}
    if c.get("Image")!=ORIGINAL_IMAGE_ID or conf.get("Image")!=EXACT_IMAGE_TAG:
        raise Stop("ORIGINAL_IMMUTABLE_IMAGE_MISMATCH")
    if ((h.get("RestartPolicy") or {}).get("Name")!="no"
        or h.get("PidMode") not in (None,"")
        or h.get("NetworkMode")!=NETWORK
        or (h.get("ExtraHosts") or [])
        or (h.get("Dns") or [])
        or (h.get("CapAdd") or [])):
        raise Stop("SYNTHETIC_HOST_RUNTIME_POLICY_CHANGED")
    if not isinstance(networks,list) or len(networks)!=1 or not isinstance(networks[0],dict):
        raise Stop("SYNTHETIC_NETWORK_EVIDENCE_REJECTED")
    try:
        match=v.check_persistent_state(
            nft_doc,containers,networks,
            bridge=BRIDGE,subnet=SUBNET,host_gateway=GATEWAY_IP,
            canary=CANARY_IP,relay_port=RELAY_PORT,
            expected_image_id=ORIGINAL_IMAGE_ID)
    except Exception:
        raise Stop("NFT_DOCKER_STRUCTURAL_EVIDENCE_MISMATCH") from None
    if match is not True:
        raise Stop("NFT_DOCKER_STRUCTURAL_EVIDENCE_MISMATCH")
    return {
        "audit":"R23D2B1I_OFFLINE_NFT_DOCKER_EVIDENCE_CONTRACT",
        "status":"SYNTHETIC_STRUCTURE_PASS_LIVE_GUARD_NOT_ATTESTED",
        "existing_r23d2a_joint_structural_verifier_reused":True,
        "original_image_id_and_tag_matched":True,
        "single_expected_synthetic_peer_structure_checked":True,
        "nft_input_and_forward_expected_rule_structure_checked":True,
        "synthetic_restart_policy_no":True,
        "evidence_read_from_real_kernel_or_docker":False,
        "evidence_collector_identity_authenticated":False,
        "boot_time_fence_present":False,
        "packet_path_enforcement_proven":False,
        "production_or_host_modified":False,
        "real_canary_start_authorized":False,
        "authenticated_aio_relay_allowed":False,
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture-only",action="store_true")
    args=p.parse_args()
    if not args.fixture_only:
        p.error("ONLY_SYNTHETIC_FIXTURE_SUPPORTED_NO_HOST_OR_APPLY")
    try:
        result=attest_synthetic_evidence(*fixture())
        print(json.dumps(result,indent=2))
        print("R23D2B1I_OFFLINE_FIXTURE_CONTRACT_PASS_NO_RUNTIME")
    except Exception:
        print(json.dumps({"audit":"R23D2B1I_OFFLINE_SAFE_DIAGNOSTIC",
            "status":"HOLD","authenticated_aio_relay_allowed":False},indent=2))
        raise SystemExit(1) from None

if __name__=="__main__":
    main()
