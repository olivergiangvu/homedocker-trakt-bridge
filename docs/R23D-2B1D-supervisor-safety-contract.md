# R23D-2B1D Phase 2 — Independent Supervisor Safety Contract

**Status:** source-only protocol model; not a production daemon, firewall, or deployment authorization. Reviewed base: R23D-2B1C recovered image source SHA `3c08d06bf08154283d5b548d371729436f9ea392`, image ID `sha256:98b1e6dc7c235ba7381cd6f798f742a5ecc460580c2f408e8d256927199f300d`. This document does not embed tokens or authorize an authenticated AIO GET.

## Trust boundaries

1. **Kernel enforcement:** nftables guard scoped to exact newly created Linux bridge interface, with both INPUT and FORWARD restrictions. Structural nft JSON parity alone does not demonstrate packet path; live **credential-free** allowed and denied TCP tests remain required. An nft *accept* rule may still be rejected by other base chains.
2. **Docker identity:** separately verify container ID, exact immutable image ID, labels, network ID, one expected peer, bridge interface name, IPAM subnet, IPv4-only and no masquerade, host-loopback-only bind, read-only proof mount and lack of privileges. Container's own claims are not evidence.
3. **Independent supervisor:** must run outside the Gateway container, independently attest kernel/Docker on a bounded heartbeat and retain an out-of-process kill/fence path. The Gateway cannot report its own guard as healthy. An ordinary periodic Python thread is *not* adequate independent supervision.
4. **Startup fencing:** container must not be created or started before nft guard is active and verified, and before the independently managed watchdog/fence is operational. Newly created bridge may exist temporarily only with zero attached peers. No production RC6, AIO, nginx, Trakt data or client route changes.
5. **Failure:** missing heartbeat, changed nft rule or Docker identity, failed negative TCP test, supervisor crash, external firewall flush, daemon restart and reboot all **BLOCK** further execution. Guard cleanup prohibited until canary absent, owned network empty/absent, ownership unchanged and separate authorization. If canary termination cannot be confirmed, keep guard and incident HOLD.

## Reboot / crash safety is not yet established

An nft rule installed at runtime may disappear on host reboot. A persistent restart policy on a canary could therefore create an **unprotected boot window**. The future deployment must prove a system-level startup ordering that establishes independent deny/fence before *any* R23D canary starts, and prove crash/reboot negative controls. Until then: no canary auto-start and no real authenticated relay. `--restart no` in a one-off Docker plan reduces a restart vector but is **not itself a proof** of reboot protection.

A test process crashing is not a fail-safe mechanism unless there is an already active kernel restriction, and an independently supervised kill path plus authenticated container ownership check. Reject "watchdog check every few seconds" as sole guard against race windows.

## Scope of the executable model

`scripts/model-v140-r23d2b1d-supervisor-contract.py --model-only` accepts no host artifacts and writes nothing. It synthesizes evidence to test decision rules. Each proof field is synthetic and **cannot demonstrate real host enforcement**. Successful model eligibility must never be used as permission for actual Docker, nft, AIO or Trakt operations.

The model latches `LATCHED_STOP` on malformed or stale heartbeat, unexpected epoch/restart, reboot, missing fence, guard drift, network/image mismatch, non-synthetic identity, or failed allow/deny TCP tests. It cannot be rearmed within the same instance; an independently approved, fresh epoch and baseline would be required in a *future* runtime implementation. The model never removes a firewall, stops a process or enables an authenticated relay.

## Required qualification gates before any experimental host apply

- [ ] Exact source commit pinned and full regression CI green; R23D-2B1C protected image recovery attestation repeated.
- [ ] Real independent supervisor service design reviewed for least privilege and boot ordering; no self attestation; external watchdog/kill escalation verified.
- [ ] Fresh kernel nft + Docker inventory jointly attested with exact ownership and interface; IPv6 and alternate egress bypasses checked.
- [ ] Negative TCP probes from a synthetic container, including host INPUT forbidden ports and external FORWARD, fail; only precisely allowed fake host relay passes.
- [ ] Crash, Docker daemon restart, nft flush and reboot safety tests performed in a controlled environment; never assume persistence across reboot.
- [ ] No Trakt OAuth credentials, Bridge DB, real Jellyfin/AIO user identity, user history, or production service changes.
- [ ] Safe cleanup sequence only after verified absence of canary and peers, followed by owned-network removal and owned-nft-table removal last; hold on uncertainty.
- [ ] RC6/R23B/R23A and nginx postflight PASS, rollback artefacts intact.

**Decision:** Phase 2 PASS means only offline protocol checks PASS. Host apply and authenticated relay remain BLOCKED until all independent enforcement gates are demonstrated.
