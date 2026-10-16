# R23D-2B1F — Systemd Guardian boot-order security design (SOURCE-ONLY)

**Outcome required:** no R23D canary process or Docker daemon restart may create unguarded traffic, even transiently. This is a *design contract*, not a live install or permission to change HomeDocker. Phase 3 real HomeDocker audit confirmed all 12 regressions PASS and `PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD`; there are no R23D guardian/canary systemd units and no implemented boot guard.

## Abstract unit dependency requirements

The source-only model `scripts/model-v140-r23d2b1f-systemd-boot-order.py` validates only metadata **requirements**, not actual systemd unit files:

| Invariant | Intended design |
|---|---|
| Guardian after Docker | `After=docker.service`, `Requires=docker.service` |
| Canary waits for Guardian | `Requires=`, `After=`, and `BindsTo=homedocker-r23d-guardian.service` |
| Guardian ordered before Canary | `Before=homedocker-r23d-gateway-canary.service` |
| Guardian lifetime | `Type=notify`, bounded `WatchdogSec`, restart-on-failure |
| Canary automatic restart | Both systemd and Docker `Restart=no` |
| Accidental boot enable | No `[Install]` target for either proposed unit |
| External launcher | Must be fenced independently; direct `docker run/start` bypass is NOT solved by systemd ordering |
| Real authenticated session | **Never** allowed in synthetic trial |

The proposed dependency shape is intentionally incomplete: systemd `After=` is ordering, NOT proof that kernel nft INPUT+FORWARD guard is active; `Requires=` and `BindsTo=` likewise do not guarantee network policy or that an external Docker client cannot start a container. `Type=notify` must be coupled to an independently verified, privileged guardian that emits READY only after effective guard attestation; merely placing READY=1 in application code would be unsafe. `WatchdogSec` alone does not provide a fail-safe hardware/kernel fence. **No unit files are supplied to install.**

## Additional safety blockers before implementing real systemd service

1. **Boot and daemon restart bypass:** Docker may restart managed containers independently of systemd canary ordering. Reject any existing R23D container with non-`no` Docker restart policy. Explicitly audit Docker daemon recovery and other automation tools (Portainer/Compose/systemd jobs) which could start R23D out of band. The host's regular running services and existing rollback canaries must remain untouched.
2. **Kernel-safe startup:** Guardian must not signal ready until it verifies freshly installed nft INPUT+FORWARD kernel policy on the exact bridge interface, after independently establishing ownership. A new empty network can precede guard; attaching the first container is forbidden until both guard and independent supervision are proven.
3. **Kernel-safe failure:** Guardian crash, nft flush, stale watchdog, changed bridge/peer identity and failed synthetic forbidden-host/FORWARD probes must fail closed via separately supervised fencing. Do not rely on a thread checking once every few seconds; packet egress between checks remains possible.
4. **Reboot:** nft tables are not presumed persistent. No canary startup until an *independent boot-time block* is effective. A guard installed once after Docker is running is insufficient to prevent auto-restarted peers communicating during boot. A separate mechanism must withstand even failure of the guardian process.
5. **Cleanup:** Stop only the precisely attested synthetic canary, verify its container actually absent, verify owned network has no peers and was removed, and delete only the unchanged owned nft table last. Missing evidence means manual incident HOLD.
6. **No production changes:** no nginx proxy update, AIO login, Trakt history modification, real tokens/identities, live user playback, RC6/R23B/R23A container change, or auto-update.

## Testable order (synthetic only)

`BOOT → DOCKER_DAEMON_READY → INDEPENDENT_FENCE_READY → GUARD_INSTALLED → GUARD_KERNEL_VERIFIED → WATCHDOG_ACTIVE → SYNTHETIC_CANARY_ALLOWED → CANARY_STOPPED → NETWORK_EMPTY → NETWORK_REMOVED → GUARD_REMOVED_LAST`

The Python model uses symbolic booleans and events only; successful tests do not attest a Linux host. Any out-of-order event, missing evidence, guard flush, restart, reboot, or supervisor crash permanently latches STOP for that model instance.

**Next engineering requirement:** write a real independently supervised guardian and dedicated boot-time fence in a separate controlled lab plan, with repeatable crash, daemon-restart and reboot tests. Only after peerless guard installation and packet-level synthetic positive/negative evidence should a privileged *fake-only* runtime trial be considered. Authenticated AIO relay remains BLOCKED.

## Verification

In a SHA-pinned clean detached checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s test -p 'test_v140_r23d2b1f_systemd_boot_order.py' -v
python3 -B scripts/model-v140-r23d2b1f-systemd-boot-order.py --model-only
```

No `sudo`, host service installation, Docker build or nft mutation is required.
