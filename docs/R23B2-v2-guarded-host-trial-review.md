# R23B2 guarded Unix GET relay: v2 host trial review

Status: DRAFT SOURCE REVIEW ONLY — NO HOME-DOCKER APPLY / NO PRODUCTION ROUTE.

This branch adds:
- scripts/host-trial-r23b2.py — dry-run by default; optional short-lived sidecar and private Unix GET relay; exact non-production R23B owner and nonce checks; TTL 60–900s.
- test/test_r23b2_host_trial_launcher.py — seven mocked, non-mutating safety regressions.

The two required sibling scripts already exist in the Draft v1.4 branch. The review branch starts at 5c6485533f36b1c48c51b546f833d78b2cf4faf8.

## Operator evidence from HomeDocker (2026-10-16)

Using original R23B2 source commit 42a2717222f05f48f2b040c93f3b0b6d9d3c5d0e:
- Original R23B2 regression: 16/16 PASS.
- R23B2_HOST_READONLY_DEPLOYMENT_PREFLIGHT: trial_environment_ready=true, no observed R23B drift, port 9999 free, python:3.12-alpine locally present.
- AIO public endpoint on 127.0.0.1:3240 returned HTTP 200.
- Private /run/homedocker-r23b2-aio path initially absent.
- B1V unauthenticated health-only preflight: AIO and Gateway 200. This was NOT an authenticated comparison.
- Transferred v2 files matched SHA-256; seven launcher tests passed.
- Temporary detached checkout with original source and v2 launcher returned R23B2_LAUNCHER_DRY_RUN_READY_NO_CHANGES.

These are operator-reported outputs, not evidence of --apply or live authenticated Gateway acceptance.

## Mandatory reviewer gates before any --apply

1. CI on this exact branch: R23B2 real Docker namespace + Unix relay, launcher regression and broader CI. Classify any unrelated R23D failures separately.
2. Fault-injection review: Docker create returning error after creation; start failure; host relay crash; Ctrl-C/SIGHUP/SIGTERM; end-of-TTL; SSH disconnect.
3. Explicitly resolve SIGKILL / reboot recovery: Python finally cannot run after SIGKILL. restart=no and child TTL limit exposure but do NOT prove resource removal. Require tightly scoped recovery and exact ownership verification; no blanket prune.
4. Check races involving socket directory, owner/nonce, names and IDs, Docker daemon failures, and R23B network namespace drift.
5. Only fake AIO tokens in disposable Docker tests. The shared-netns sidecar is not an independent production egress firewall; R23D boot/fence/privileged Docker API bypass remains open.
6. Separate operator approval before any time-bounded HomeDocker trial; confirm no leftover sidecar/socket, and unchanged original R23B fingerprint.

No PR #95 merge, v1.4 release, nginx/AIO/RC6/Trakt/history/firewall changes. V140_STABLE_RELEASE_HOLD persists.

## v3 crash-recovery source checkpoint (2026-10-16; still DRAFT)

The launcher now writes a root-only, no-credential receipt under /var/lib/homedocker-r23b2-trial/receipt.json **only after explicit --apply**. The directory and receipt are mode 0700/0600. The receipt records a random 32-hex nonce, exact canary and local image IDs, boot ID, trial controller PID/start ticks, host relay PID/start ticks, and a 60–900s TTL. A new trial refuses a pre-existing receipt, even if /run socket is absent.

- --recover without confirmation performs inspection only, requiring the receipt and restricted ownership, schema, sidecar image, network, mount, UID, restart=no, readonly-rootfs and nonce checks.
- --recover --confirm R23B2_EXACT_OWNER_RECOVERY_ONLY is a separate opt-in recovery mutation (NOT permission to deploy to production). It refuses concurrent recovery while the original controller remains alive. For a dead controller it may stop/remove only the exact receipt-bound sidecar and verified host relay, using pidfd and original boot ID, PID start-time, UID 65534 and expected command to prevent killing reused PIDs. It then verifies exact socket path cleanup and removes the root receipt only after owned-resource absence checks.
- If receipt, ownership, socket, process or Docker state is ambiguous, recovery STOPs and leaves evidence for manual review. No prune, network firewall changes, nginx, Trakt or AIO DB writes.
- This is not protection against malicious host root/Docker administrator. SIGKILL cannot be trapped; **automatic cleanup on SIGKILL is NOT claimed**. An operator must run exact-owner recovery. TTL and restart=no bound the runtime, not removal of Docker objects. Reboot can leave stopped sidecar and persistent receipt.

The operator-transferred v2 package is not the v3 recovery source. All new code remains on Draft PR #117; NO HomeDocker --apply.

Unit test evidence in local development: 7 inherited plus 12 receipt/recovery tests, total 19 PASS. Real crash/restart fault-injection in a disposable Docker runtime and HomeDocker operator qualification remain OPEN, even after CI passes.

V140_STABLE_RELEASE_HOLD persists. No change to Draft PR #95, RC6, nginx, AIOStreams, Trakt history or protected Docker networks.
