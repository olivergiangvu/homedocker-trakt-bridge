# R23D-2B1R — Installed systemd controller and live nft-loss response

**Scope:** ONLY a dedicated ephemeral GitHub-hosted Linux VM. The lab creates an actual, enabled systemd unit file at `/etc/systemd/system/r23d2b1r-monitor-$PID.service` for the duration of the CI job, **then removes it, its enablement symlink, the fake Docker network/containers and nft table**. It intentionally restarts the runner's Docker daemon. **It does not reboot the VM or its kernel, and is NOT approved for HomeDocker.**

## Why B1R matters

B1Q tested a real transient systemd **one-shot** which checked tc+nft before attaching a fake server initially created with `--network none`. It demonstrated that deleting nft while the server remains attached reopens previously authorized fake TCP until a trusted actor disconnects it. B1R tests whether a *continuously running, systemd-managed* process can observe such fence loss and disconnect an exact-owned fake Docker server **without the test shell calling `docker disconnect`**.

This monitor deliberately uses no Trakt/AIO secrets. Only two synthetic Docker peers on one `--internal` bridge, with no published host ports, no volumes, and `--restart no`. It recognizes exact project labels, image-independent Docker identities, current host veth, scoped tc filter evidence and the owned bridge-family nft timeout set. When valid, it calls the B1Q admission check and starts only the exact named fake server. When invalid, it disconnects and stops the exact server (if present). This is **polling**, not an asynchronous atomic kernel deny: up to the detection interval/command latency, a live TCP connection can be exposed after `nft` deletion.

## Required live acceptance

- Install and enable a real `Type=notify` systemd unit with `Restart=always`, `WatchdogSec`, `After`/`BindsTo`/`PartOf=docker.service`, and `WantedBy=docker.service`. Unit is only for disposable CI, not portable production code.
- Start controller while nft/tc are absent. The fake server must remain stopped and `--network none`.
- Install exact tc on the current client veth plus exact nft lease table. The systemd controller must **automatically** pass the B1Q checks and attach/start the fake server; TCP `17420` allowed, `17421` forbidden.
- Delete the B1R lab nft table *while the fake server is still attached*. The **monitor**, not the shell, must disconnect server within a bounded measured interval, and fake TCP becomes unreachable. This is NOT a zero-millisecond or host-global nft-loss fail-closed guarantee.
- Recreate the exact scoped nft policy; monitor must re-attest current tc+nft and safely re-admit without test-shell `docker start` of the server.
- `SIGKILL` only the monitor's systemd `MainPID`; systemd must restart a **new PID**, and the allowed/denied fake TCP controls still work.
- Disconnect and stop server via automated policy loss, then perform **real `systemctl restart docker` on this disposable runner**. Both `--restart no` fake peers must stay stopped. Unit is configured to bind to Docker and must be active after Docker restarts; otherwise mark failure.
- Start fake client only; prove recreated veth has no stale tc. Re-arm exact tc/nft and verify controller automatically re-admits fake server. Scope teardown verifies no named unit/symlink/container/bridge/nft table remains.

## What remains unproven

1. **True kernel/VM reboot and cold-start race:** a `systemctl restart docker` is not a VM reboot. The CI runner's main systemd manager never underwent a full boot. The installed unit's `WantedBy=docker.service` does not establish tamper-proof boot or Docker API admission control.
2. **Privileged bypass and simultaneous process failure:** a root-level caller holding Docker API privilege can attach the `--network none` server directly without calling B1Q. If the monitor process and systemd supervisor both fail, the active nft lease can temporarily continue authorizing traffic.
3. **Polling exposure:** deleting the only nft table while fake server is attached creates an interval where tc still permits authorized TCP. The monitor can shrink but **cannot eliminate** the interval. This fails an unconditional zero-leak guarantee; a third independent default deny would be needed.
4. **Policy parsing is not full semantic verification:** test checks owned table and rule representations and positive/negative live fake TCP; does not prove immunity to every similarly formatted but malicious rule.
5. **Production-shape not qualified:** no non-internal Docker bridge/published ports/NAT/IPv6, authenticated Jellyfin/AIOStreams client routing, backups/rollback or HomeDocker host integration.

**Release:** `V140_STABLE_RELEASE_HOLD` stays active, Draft PR #95 must not be merged/tagged/deployed until true-reboot and privilege/bypass/real-client requirements are met.
