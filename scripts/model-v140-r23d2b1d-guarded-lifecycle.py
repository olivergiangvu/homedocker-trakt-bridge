#!/usr/bin/env python3
"""R23D-2B1D: fail-closed synthetic runtime lifecycle *model*, no host writes.

No Docker/nft subprocess, no network calls, no token access, no container run,
no daemon process and no permission to apply the modeled steps. Approval of a
later runtime orchestrator requires independent supervision and kernel checks.
"""
import argparse
import json

STAGES=(
    "PROTECTED_BASELINE",
    "EXACT_IMAGE_ATTESTED",
    "SUPERVISOR_READY",
    "NETWORK_CREATED_EMPTY",
    "GUARD_INSTALLED",
    "GUARD_KERNEL_ATTESTED",
    "FAKE_CONTROLS_PASS",
    "FAKE_CANARY_STARTED",
    "PEER_AND_GUARD_ATTESTED",
    "FAKE_HEALTH_PASS",
    "CANARY_REMOVED",
    "NETWORK_VERIFIED_EMPTY_AND_REMOVED",
    "GUARD_VERIFIED_UNCHANGED_AND_REMOVED",
    "PROTECTED_POSTFLIGHT_PASS",
)
class Stop(Exception):
    pass

def evaluate(evidence):
    """Evaluate separately supplied synthetic test evidence, never live host state.

    Evidence keys intentionally represent independent attestations. A True in
    a JSON file is NOT proof of real-world enforcement; this function tests the
    lifecycle decision contract only.
    """
    if not isinstance(evidence,dict) or set(evidence)!={"synthetic","stages","guard_retained_on_failure"}:
        raise Stop("EVIDENCE_SHAPE_REJECTED")
    if evidence["synthetic"] is not True or evidence["guard_retained_on_failure"] is not True:
        raise Stop("FAIL_CLOSED_REQUIREMENTS_MISSING")
    steps=evidence["stages"]
    if not isinstance(steps,list) or len(steps)!=len(STAGES):
        raise Stop("LIFECYCLE_LENGTH_REJECTED")
    seen=set()
    for idx,item in enumerate(steps):
        if not isinstance(item,dict) or set(item)!={"stage","passed"}:
            raise Stop("STAGE_EVIDENCE_SHAPE_REJECTED")
        name=item["stage"]
        if name in seen or name!=STAGES[idx] or item["passed"] is not True:
            raise Stop("STAGE_ORDER_OR_EVIDENCE_REJECTED")
        seen.add(name)
    return True

def safe_cleanup(*,canary_absent,network_absent,guard_unchanged,
                 supervision_alive):
    if any(type(value) is not bool for value in (
        canary_absent,network_absent,guard_unchanged,supervision_alive)):
        raise Stop("CLEANUP_EVIDENCE_INVALID")
    if not supervision_alive:
        return "HOLD_KEEP_GUARD_REQUIRE_INDEPENDENT_RECOVERY"
    if not canary_absent:
        return "HOLD_KEEP_GUARD_AND_NETWORK"
    if not network_absent:
        return "HOLD_KEEP_GUARD"
    if not guard_unchanged:
        return "HOLD_KEEP_GUARD_OWNERSHIP_REVIEW"
    return "ELIGIBLE_FOR_SEPARATELY_AUTHORIZED_GUARD_REMOVAL"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--model-only",action="store_true")
    args=p.parse_args()
    if not args.model_only:
        p.error("NO_APPLY_IMPLEMENTED_USE_MODEL_ONLY")
    result={
        "audit":"R23D2B1D_SYNTHETIC_LIFECYCLE_MODEL",
        "status":"SOURCE_ONLY_MODEL_NOT_RUNTIME_ATTESTATION",
        "stages_ordered":len(STAGES),
        "guard_precedes_fake_canary":STAGES.index("GUARD_KERNEL_ATTESTED")<STAGES.index("FAKE_CANARY_STARTED"),
        "guard_cleanup_after_canary_and_network":STAGES.index("GUARD_VERIFIED_UNCHANGED_AND_REMOVED")>STAGES.index("NETWORK_VERIFIED_EMPTY_AND_REMOVED"),
        "independent_supervisor_implemented":False,
        "live_kernel_guard_tested":False,
        "docker_or_nft_modified":False,
        "aio_authenticated_relay_allowed":False,
        "next":"IMPLEMENT_AND_TEST_INDEPENDENT_FAIL_CLOSED_SUPERVISOR_BEFORE_ANY_APPLY",
    }
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
