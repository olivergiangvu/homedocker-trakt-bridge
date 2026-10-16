#!/usr/bin/env python3
"""R23D-2B1G: REAL separate-process synthetic guardian *LAB*, no host guard.

Reads exact synthetic JSONL records from STDIN, enforces a monotonic-clock
deadline and irreversible failure. A successful LAB exercise is NOT a kernel
fence, Docker/network attestation, installable systemd guardian, or permission
to run a canary. This script contains NO Docker/nft/systemctl/auth actions.
No real profile IDs, tokens, credentials or file paths are accepted or printed.
"""
import argparse
import json
import os
import re
import selectors
import signal
import sys
import time

EPOCH_RE=re.compile(r"^R23D_SYNTHETIC_[A-Z0-9_]{8,40}$")
KEYS=frozenset({
    "epoch","seq","guard_attested","docker_identity_attested",
    "fence_ready","synthetic_only","reboot_detected",
})
TRUE_FIELDS=("guard_attested","docker_identity_attested","fence_ready","synthetic_only")
MAX_FRAME=1024
LAB_READY_LINE="R23D2B1G_LAB_INPUT_LOOP_READY_NO_HOST_PROOF"

class Stop(Exception):
    pass

def unique_pairs(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:
            raise Stop("DUPLICATE_JSON_KEY")
        obj[k]=v
    return obj

def check_frame(raw,*,epoch,expected_seq):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_FRAME:
        raise Stop("FRAME_TOO_LARGE_OR_EMPTY")
    try:
        value=json.loads(raw.decode("utf8","strict"),object_pairs_hook=unique_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(Stop("NONFINITE_JSON")))
    except Stop:
        raise
    except (UnicodeError,ValueError,TypeError,RecursionError):
        raise Stop("SYNTHETIC_JSON_REJECTED") from None
    if not isinstance(value,dict) or set(value)!=KEYS:
        raise Stop("EVIDENCE_SHAPE_REJECTED")
    if value["epoch"]!=epoch or type(value["seq"]) is not int or value["seq"]!=expected_seq:
        raise Stop("EPOCH_OR_SEQUENCE_REPLAY")
    for field in TRUE_FIELDS:
        if value[field] is not True:
            raise Stop("SYNTHETIC_GUARD_OR_FENCE_NOT_ATTESTED")
    if value["reboot_detected"] is not False:
        raise Stop("REBOOT_FAIL_CLOSED")
    return True

def safe_line(status,*,sequence=0,reason=""):
    return {
        "audit":"R23D2B1G_SEPARATE_PROCESS_LAB",
        "status":status,
        "sequence":sequence,
        "reason":reason,
        "kernel_or_docker_attestation_performed":False,
        "systemd_guardian_installed":False,
        "host_network_nft_or_production_modified":False,
        "real_canary_start_authorized":False,
        "authenticated_aio_relay_allowed":False,
    }

def emit(status,*,sequence=0,reason=""):
    print(json.dumps(safe_line(status,sequence=sequence,reason=reason),
                     sort_keys=True),flush=True)

def _terminated(_signal,_frame):
    raise Stop("PROCESS_INTERRUPTED_FAIL_CLOSED")

def run_lab(*,epoch,timeout_ms,max_events):
    if not isinstance(epoch,str) or not EPOCH_RE.fullmatch(epoch):
        raise Stop("SYNTHETIC_EPOCH_INVALID")
    if (type(timeout_ms) is not int or not 100<=timeout_ms<=3000
        or type(max_events) is not int or not 1<=max_events<=20):
        raise Stop("LAB_BOUNDS_INVALID")
    signal.signal(signal.SIGTERM,_terminated)
    signal.signal(signal.SIGINT,_terminated)
    selector=selectors.DefaultSelector()
    buffer=bytearray()
    seq=0
    # A real monotonic clock, not a clock or heartbeat supplied by stdin.
    deadline=time.monotonic()+timeout_ms/1000.0
    try:
        selector.register(sys.stdin.buffer,selectors.EVENT_READ)
        # Exact LAB-only synchronization point: handlers are installed and
        # the event loop has registered its input. This is NOT kernel proof
        # or a systemd-ready notification; stderr is not an authority channel.
        print(LAB_READY_LINE,file=sys.stderr,flush=True)
        while seq < max_events:
            wait=max(0.0,deadline-time.monotonic())
            if wait==0:
                raise Stop("HEARTBEAT_TIMED_OUT")
            if not selector.select(wait):
                raise Stop("HEARTBEAT_TIMED_OUT")
            chunk=os.read(sys.stdin.fileno(),4096)
            if not chunk:
                raise Stop("EVIDENCE_PIPE_EOF")
            buffer.extend(chunk)
            if len(buffer)>(MAX_FRAME+1)*20:
                raise Stop("FRAME_BUFFER_OVERSIZED")
            while b"\n" in buffer:
                if time.monotonic()>deadline:
                    raise Stop("HEARTBEAT_TIMED_OUT")
                idx=buffer.index(10)
                frame=bytes(buffer[:idx])
                del buffer[:idx+1]
                if len(frame)>MAX_FRAME:
                    raise Stop("FRAME_TOO_LARGE_OR_EMPTY")
                check_frame(frame,epoch=epoch,expected_seq=seq+1)
                seq+=1
                emit("LAB_EVIDENCE_ACCEPTED_NOT_HOST_PROOF",sequence=seq)
                deadline=time.monotonic()+timeout_ms/1000.0
                if seq==max_events:
                    if buffer:
                        raise Stop("EXTRA_EVIDENCE_AFTER_LAB_BOUND")
                    emit("LAB_EXERCISE_PASS_NO_RUNTIME_AUTHORIZATION",sequence=seq)
                    return 0
            if len(buffer)>MAX_FRAME:
                raise Stop("FRAME_TOO_LARGE_OR_EMPTY")
    finally:
        selector.close()
    return 2

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-only",action="store_true")
    parser.add_argument("--epoch",default="R23D_SYNTHETIC_LAB_0001")
    parser.add_argument("--timeout-ms",type=int,default=500)
    parser.add_argument("--max-events",type=int,default=2)
    args=parser.parse_args()
    if not args.lab_only:
        parser.error("NO_SYSTEMD_INSTALL_HOST_APPLY_OR_RUNTIME_MODE")
    try:
        return run_lab(epoch=args.epoch,timeout_ms=args.timeout_ms,
                       max_events=args.max_events)
    except Stop as e:
        reason=e.args[0] if len(e.args)==1 and e.args[0] in {
            "SYNTHETIC_EPOCH_INVALID","LAB_BOUNDS_INVALID","DUPLICATE_JSON_KEY",
            "NONFINITE_JSON","SYNTHETIC_JSON_REJECTED","EVIDENCE_SHAPE_REJECTED",
            "EPOCH_OR_SEQUENCE_REPLAY","SYNTHETIC_GUARD_OR_FENCE_NOT_ATTESTED",
            "REBOOT_FAIL_CLOSED","PROCESS_INTERRUPTED_FAIL_CLOSED",
            "HEARTBEAT_TIMED_OUT","EVIDENCE_PIPE_EOF","FRAME_BUFFER_OVERSIZED",
            "FRAME_TOO_LARGE_OR_EMPTY","EXTRA_EVIDENCE_AFTER_LAB_BOUND",
        } else "LAB_UNCLASSIFIED_FAIL_CLOSED"
        emit("LAB_LATCHED_STOP",reason=reason)
        return 2
    except BaseException:
        emit("LAB_LATCHED_STOP",reason="LAB_UNCLASSIFIED_FAIL_CLOSED")
        return 2

if __name__=="__main__":
    sys.exit(main())
