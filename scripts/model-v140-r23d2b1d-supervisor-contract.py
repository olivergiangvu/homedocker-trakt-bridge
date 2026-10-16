#!/usr/bin/env python3
"""R23D-2B1D PHASE 2: offline independent-supervisor protocol MODEL.

No side effects, Docker, nft, socket, processes, filesystem writes, credentials
or real AIO/Jellyfin calls. This evaluates *synthetic, externally supplied*
attestation evidence. A healthy result never authorizes real relay or apply.
Evidence booleans alone cannot establish independent kernel enforcement.
"""
import argparse
import json
import re

EPOCH_RE = re.compile(r"^R23D_SYNTHETIC_[A-Z0-9_]{8,40}$")
MAX_AGE_NS = 3_000_000_000  # modeling bound, NOT a production interval
PROOF_KEYS = frozenset({
    "epoch", "now_ns", "heartbeat_ns", "supervisor_independent",
    "fence_ready", "kernel_guard_attested", "network_identity_attested",
    "image_identity_attested", "host_baseline_unchanged",
    "allowed_tcp_control_pass", "denied_tcp_control_pass",
    "canary_synthetic_only", "host_reboot_detected",
})
BOOL_KEYS = PROOF_KEYS - {"epoch", "now_ns", "heartbeat_ns"}

class Stop(Exception):
    pass

class SupervisorContract:
    """Latch failure forever for one epoch: no automatic recovery or cleanup.

    State transitions: CLOSED -> ELIGIBLE_FOR_SYNTHETIC_CANARY -> LATCHED_STOP.
    This class is strictly offline; ELIGIBLE is NOT permission for Docker run,
    nft apply, authenticated relay, or real host activity.
    """
    def __init__(self, epoch):
        if not isinstance(epoch, str) or not EPOCH_RE.fullmatch(epoch):
            raise Stop("SYNTHETIC_EPOCH_INVALID")
        self.epoch = epoch
        self.state = "CLOSED"
        self.last_observed_ns = None
        self.reason = "NOT_ATTESTED"

    def observe(self, proof):
        if self.state == "LATCHED_STOP":
            return self.report()
        fail = self._evaluate(proof)
        if fail:
            self.state = "LATCHED_STOP"
            self.reason = fail
        else:
            self.state = "ELIGIBLE_FOR_SYNTHETIC_CANARY"
            self.reason = "SYNTHETIC_PROOFS_ACCEPTED_NOT_LIVE_ATTESTATION"
            self.last_observed_ns = proof["now_ns"]
        return self.report()

    def _evaluate(self, proof):
        if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
            return "PROOF_SHAPE_REJECTED"
        if proof["epoch"] != self.epoch:
            return "EPOCH_DRIFT_OR_RESTART"
        if any(type(proof[k]) is not bool for k in BOOL_KEYS):
            return "PROOF_BOOLEAN_TYPES_REJECTED"
        now = proof["now_ns"]
        last = proof["heartbeat_ns"]
        if type(now) is not int or type(last) is not int or now < 0 or last < 0:
            return "MONOTONIC_TIMESTAMP_REJECTED"
        if self.last_observed_ns is not None and now <= self.last_observed_ns:
            return "MONOTONIC_CLOCK_REWIND_OR_REPLAY"
        if last > now or now - last > MAX_AGE_NS:
            return "SUPERVISOR_HEARTBEAT_MISSING_OR_STALE"
        if proof["host_reboot_detected"]:
            return "REBOOT_REQUIRES_INDEPENDENT_REARM"
        for key in (
            "supervisor_independent", "fence_ready", "kernel_guard_attested",
            "network_identity_attested", "image_identity_attested",
            "host_baseline_unchanged", "allowed_tcp_control_pass",
            "denied_tcp_control_pass", "canary_synthetic_only",
        ):
            if not proof[key]:
                return "INDEPENDENT_PROOF_MISSING_" + key.upper()
        return None

    def report(self):
        return {
            "status": self.state,
            "reason": self.reason,
            "synthetic_canary_model_eligible": self.state == "ELIGIBLE_FOR_SYNTHETIC_CANARY",
            "on_failure": "REQUIRE_INDEPENDENT_FENCE_KEEP_GUARD_AND_STOP_CANARY",
            "live_supervisor_implemented": False,
            "live_kernel_guard_attested": False,
            "docker_nft_network_modified": False,
            "authenticated_aio_relay_allowed": False,
        }

def example_proof(epoch, now_ns=10_000_000_000):
    """Fixture ONLY; never substitute for independently acquired host proof."""
    evidence = {k: True for k in BOOL_KEYS}
    evidence.update(epoch=epoch, now_ns=now_ns, heartbeat_ns=now_ns - 1_000_000_000,
                    host_reboot_detected=False)
    return evidence

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-only", action="store_true")
    args = p.parse_args()
    if not args.model_only:
        p.error("NO_RUNTIME_OR_APPLY_MODE_SUPPORTED")
    epoch = "R23D_SYNTHETIC_EPOCH_001"
    contract = SupervisorContract(epoch)
    before = contract.observe(example_proof(epoch))
    failed = example_proof(epoch, 11_000_000_000)
    failed["fence_ready"] = False
    after = contract.observe(failed)
    after_recovery = contract.observe(example_proof(epoch, 12_000_000_000))
    print(json.dumps({
        "audit": "R23D2B1D_SUPERVISOR_PROTOCOL_MODEL",
        "status": "SOURCE_ONLY_FAIL_CLOSED_MODEL_PASS" if (
            before["synthetic_canary_model_eligible"]
            and after["status"] == "LATCHED_STOP"
            and after_recovery["status"] == "LATCHED_STOP") else "MODEL_HOLD",
        "synthetic_failure_latched": after["status"] == "LATCHED_STOP",
        "automatic_rearm_possible": False,
        "independent_host_supervisor_installed": False,
        "host_reboot_safety_verified": False,
        "live_kernel_guard_attested": False,
        "docker_or_nft_modified": False,
        "authenticated_aio_relay_allowed": False,
        "next": "IMPLEMENT_INDEPENDENT_HOST_FENCE_AND_CRASH_REBOOT_SAFETY_BEFORE_ANY_APPLY",
    }, indent=2))

if __name__ == "__main__":
    main()
