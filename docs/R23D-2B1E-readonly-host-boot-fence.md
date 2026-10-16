# R23D-2B1E — read-only host boot-fence audit and HOLD gate

This checkpoint is a **precondition discovery gate**, NOT the implementation of an independent host supervisor. Its only executable, `scripts/audit-v140-r23d2b1e-host-boot-fence-readonly.py --audit-only`, reuses the R23D-2B1C immutable-image recovery verifier and RC6/R23B/R23A protected baseline, including its existing loopback health reads and nft syntax check. No authenticated Jellyfin/AIO session is created.

## Examined by the audit

- Docker daemon is reachable; `docker.service` is loaded/active via systemd.
- Exact original source-pinned R23D Gateway image remains attested by the already-reviewed read-only verifier; no rebuild/re-tag.
- No running or stopped container owns phase label `homedocker.phase=r23d-persistent-canary` (Docker inspect through an exact filtered query).
- Reserved systemd service names `homedocker-r23d-guardian.service` and `homedocker-r23d-gateway-canary.service` do not already exist, preventing reuse or accidental override of unknown unit ownership.
- R23D network/container names and port use are rechecked by inherited protected planner.

Only allowlisted `docker info`, `docker ps -a --filter`, and `systemctl show` commands are added. No `systemctl start/enable`, `docker run/build/pull`, `nft add/delete/flush`, unit-file writes, or service installation. Python output is bounded and intentionally redacts image IDs, live network routes and credentials.

## Meaning of success

`PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD` means the host preconditions and reserved-name absence have been observed; it **does not imply** that a boot-time fence exists. Output always reports `independent_host_fence_installed=false`, `boot_order_enforcement_proven=false`, `reboot_crash_fail_closed_proven=false`, and `authenticated_aio_relay_allowed=false`.

If an R23D unit or labeled container is already present, STOP rather than overwrite, stop, delete, or assume ownership. If Docker/systemd inventory is unavailable, STOP rather than guess.

## Required implementation AFTER this audit

1. Implement a genuinely independent root-controlled startup guard/fence and verified systemd dependency ordering. Docker daemon restoration semantics must not permit R23D container autostart before guard is effective, including daemon restart and host reboot.
2. Verify real nft INPUT/FORWARD kernel rule state, bridge interface identity, IPv6 restrictions, effective packet paths, and negative controls. The current table renderer alone does not establish actual isolation.
3. Provide a separate fail-safe supervisor and fencing/kill escalation whose failure does not permit unguarded container traffic. A Python polling thread and process-owned health endpoint are insufficient.
4. Exercise crash/reboot/daemon-restart tests in a controlled context; confirm identity-safe cleanup in strict canary -> network -> owned nft table order.
5. Leave authenticated AIO GET relay, Trakt history, RC6 production, nginx routing, and v1.4 stable cutover blocked until these gates independently PASS.

## Invocation

Use a detached clean checkout of the exact audited source commit and then:

```sh
sudo -v
sudo -n python3 -B scripts/audit-v140-r23d2b1e-host-boot-fence-readonly.py --audit-only
```

Invocation must be on HomeDocker in a checkout of the source code; do not run the script from an unverified clone. The standalone audit script is intentionally **not** installable as a systemd service.

**Status:** source and test integration only; Draft PR #95, no production or host network modifications.
