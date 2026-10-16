#!/usr/bin/env python3
"""R23D-2B1F: offline boot-order/fencing contract, NOT a systemd installer.

Models systemd unit properties, Docker restart behavior and guard readiness.
No Docker, systemd, nft, socket, filesystem writes, subprocess or network.
Even a model PASS never authorizes an R23D container to run: boot-time kernel
fencing, process ownership, actual watchdog and fail-closed kill path remain
unimplemented and require real isolated host validation.
"""
import argparse
import json

GUARDIAN = "homedocker-r23d-guardian.service"
CANARY = "homedocker-r23d-gateway-canary.service"
DOCKER = "docker.service"
EXACT_KEYS = frozenset({
    "guardian_after", "guardian_before", "guardian_requires",
    "guardian_type", "guardian_watchdog_sec", "guardian_restart",
    "canary_after", "canary_requires", "canary_binds_to",
    "canary_restart", "canary_install_targets", "guardian_install_targets",
    "docker_canary_restart_policy", "canary_external_launcher_fenced",
})
class Stop(Exception):
    pass

def proposed_units():
    """Abstract requirements, NOT deployable unit-file text or host proof."""
    return {
        "guardian_after": [DOCKER],
        "guardian_before": [CANARY],
        "guardian_requires": [DOCKER],
        "guardian_type": "notify",
        "guardian_watchdog_sec": 5,
        "guardian_restart": "on-failure",
        "canary_after": [GUARDIAN],
        "canary_requires": [GUARDIAN],
        "canary_binds_to": [GUARDIAN],
        "canary_restart": "no",
        "canary_install_targets": [],
        "guardian_install_targets": [],
        "docker_canary_restart_policy": "no",
        # This is a REQUIREMENT, not an observation of the current host.
        "canary_external_launcher_fenced": True,
    }

def validate_unit_contract(spec):
    if not isinstance(spec,dict) or set(spec)!=EXACT_KEYS:
        raise Stop("UNIT_CONTRACT_SHAPE_REJECTED")
    required_lists={
        "guardian_after":[DOCKER], "guardian_before":[CANARY],
        "guardian_requires":[DOCKER], "canary_after":[GUARDIAN],
        "canary_requires":[GUARDIAN], "canary_binds_to":[GUARDIAN],
        "canary_install_targets":[], "guardian_install_targets":[],
    }
    for key,value in required_lists.items():
        if type(spec[key]) is not list or spec[key]!=value:
            raise Stop("SYSTEMD_DEPENDENCY_OR_ENABLEMENT_UNSAFE")
    if (spec["guardian_type"]!="notify"
        or type(spec["guardian_watchdog_sec"]) is not int
        or not 1<=spec["guardian_watchdog_sec"]<=30
        or spec["guardian_restart"]!="on-failure"):
        raise Stop("GUARDIAN_SERVICE_CONTRACT_UNSAFE")
    if (spec["canary_restart"]!="no"
        or spec["docker_canary_restart_policy"]!="no"
        or spec["canary_external_launcher_fenced"] is not True):
        raise Stop("CANARY_UNFENCED_RESTART_OR_LAUNCH")
    return True

EVENTS = (
    "BOOT",
    "DOCKER_DAEMON_READY",
    "INDEPENDENT_FENCE_READY",
    "GUARD_INSTALLED",
    "GUARD_KERNEL_VERIFIED",
    "WATCHDOG_ACTIVE",
    "SYNTHETIC_CANARY_ALLOWED",
    "CANARY_STOPPED",
    "NETWORK_EMPTY",
    "NETWORK_REMOVED",
    "GUARD_REMOVED_LAST",
)
class BootSequence:
    """Synthetic state transitions. Failures latch; never self-rearm."""
    def __init__(self):
        self.index=0
        self.state="CLOSED"
        self.reason="NOT_ATTESTED"

    def event(self,name,*,trusted):
        if self.state=="LATCHED_STOP":
            return self.report()
        if type(trusted) is not bool or trusted is not True:
            return self._stop("UNTRUSTED_OR_MISSING_EVIDENCE")
        if not isinstance(name,str) or self.index>=len(EVENTS) or name!=EVENTS[self.index]:
            return self._stop("BOOT_GUARD_ORDER_VIOLATION")
        self.index+=1
        if self.index==len(EVENTS):
            self.state="MODEL_SEQUENCE_COMPLETE_NO_RUNTIME_AUTHORIZATION"
        elif name=="SYNTHETIC_CANARY_ALLOWED":
            self.state="MODEL_SYNTHETIC_CANARY_SEQUENCE_ONLY"
        else:
            self.state="CLOSED"
        self.reason="MODEL_EVENT_ACCEPTED"
        return self.report()

    def fault(self,fault_name):
        if self.state=="LATCHED_STOP":
            return self.report()
        if fault_name not in (
            "GUARD_FLUSHED", "SUPERVISOR_CRASH",
            "DOCKER_DAEMON_RESTART", "HOST_REBOOT",
            "CANARY_RESTART_REQUEST", "KERNEL_GUARD_DRIFT",
            "UNOWNED_CONTAINER_DETECTED", "FENCE_EVIDENCE_EXPIRED",
        ):
            return self._stop("UNKNOWN_FAULT_FAIL_CLOSED")
        return self._stop("FAIL_CLOSED_"+fault_name)

    def _stop(self,why):
        self.state="LATCHED_STOP"
        self.reason=why
        return self.report()

    def report(self):
        return {
            "state":self.state,
            "reason":self.reason,
            "next_step_index":self.index,
            "hold_guard_until_owned_canary_and_network_proven_absent":True,
            "real_canary_start_authorized":False,
            "authenticated_aio_relay_allowed":False,
            "docker_systemd_nft_modified":False,
        }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-only",action="store_true")
    args=parser.parse_args()
    if not args.model_only:
        parser.error("NO_INSTALL_OR_APPLY_MODE")
    seq=BootSequence()
    for item in EVENTS[:6]:
        seq.event(item,trusted=True)
    assert validate_unit_contract(proposed_units()) is True
    gap=seq.fault("HOST_REBOOT")
    retained=seq.event("SYNTHETIC_CANARY_ALLOWED",trusted=True)
    assert gap["state"]=="LATCHED_STOP" and retained["state"]=="LATCHED_STOP"
    print(json.dumps({
        "audit":"R23D2B1F_BOOT_ORDER_FENCING_MODEL",
        "status":"SOURCE_ONLY_BOOT_ORDER_CONTRACT_PASS",
        "abstract_unit_contract_checked":True,
        "boot_reboot_fault_latched":True,
        "guard_activation_and_watchdog_not_installed":True,
        "docker_or_systemd_or_nft_modified":False,
        "authenticated_aio_relay_allowed":False,
        "next":"DESIGN_ACTUAL_FENCING_SUPERVISOR_AND_VALIDATE_IN_ISOLATED_HOST",
    },indent=2))

if __name__=="__main__":
    main()
