# R23D-2B1L — Ephemeral kernel time-bound lease and guardian SIGKILL lab

**Status:** GitHub-hosted disposable Linux network namespace kernel verification, NOT a boot-recovery design that is installed on HomeDocker. Depends on previous 2B1K packet-path lab. HomeDocker R23D resources remain absent under verified R23D-2B1J read-only inventory; no production changes are authorized.

## Threat model and real experiment

Script: `scripts/probe-v140-r23d2b1l-guardian-crash-ci.sh --ci-ephemeral-only`. Runs as root exclusively on disposable GitHub-hosted Ubuntu runner. CI runner restrictions and explicit mode are hygiene controls, not a cryptographic proof of host identity. **NEVER execute it on HomeDocker.**

The script allocates exactly two new Linux network namespaces and two fake TCP listeners. Initial positive probes prove both TCP destinations are reachable. It then installs an isolated **independent nft base fence** whose INPUT/FORWARD rules deny any traffic from the lab bridge unless the source appears in a kernel-timed set for the exactly allowed fake relay port. Before issuing a lease both destinations are denied; the fence therefore remains effective even if the userspace issuer was never started.

A second table contains the previously reviewed R23D INPUT/FORWARD rules. The test starts a separate synthetic guardian process which issues a **single four-second nft timeout element**, then replaces itself with a sleeping process. This is a one-shot *lease issuer*, **not a periodic renewer or production watchdog**. The relay is reachable while the lease is valid and the other host port remains blocked.

The script forcibly SIGKILLs the issuer. No signal handler gets a chance to revoke authorization. After five seconds the real kernel timeout has expired and a fresh TCP connection to the fake relay must fail. The reviewed secondary guard table is separately removed to simulate **loss of that table only**; the independent fence still blocks. This is **NOT** equivalent to `nft flush ruleset` or loss of the independent fence itself.

Scoped rollback deletes only these two tables within the owned namespace; the synthetic TCP listeners must still be reachable afterward. Exit cleanup kills only the lab's fake listener PIDs and deletes only the two owned namespaces, verifying their absence.

## Evidence and claims

Verified on a GitHub-hosted Ubuntu 24.04 kernel job in CI prior to this documentation:

```
R23D2B1L_PRE_FENCE_POSITIVE_TCP_CONTROLS_PASS
R23D2B1L_KERNEL_DEFAULT_DENY_BEFORE_GUARDIAN_PASS
R23D2B1L_TEMPORARY_AUTHORIZED_RELAY_PASS
R23D2B1L_SIGKILL_KERNEL_LEASE_EXPIRY_FAIL_CLOSED_PASS
R23D2B1L_SECONDARY_GUARD_TABLE_LOSS_FENCE_REMAINS_PASS
R23D2B1L_SCOPED_ROLLBACK_RESTORES_TCP_PASS
R23D2B1L_OWNED_NAMESPACES_CLEANUP_PASS
```

The final commit's workflow must also show both the rootless standard CI unit test job and the dedicated kernel job SUCCESS.

## Important remaining safety blockers

- **A four-second allowed window remains after SIGKILL.** This validates bounded, eventually denied access, *not instantaneous emergency kill*. Production must establish a tighter documented bound or kernel-enforced real-time fencing before treating guardian failure as controlled.
- This does **NOT** prove host boot safety. The fence was installed intentionally after lab namespace setup; there was no Linux host reboot, no Docker daemon auto-restart, no systemd unit ordering, and no proof that the fence survives a crash of its own installer or is restored before any external canary can start.
- Flushing both nft tables would remove the independent fence. The experiment only removed the secondary reviewed table. Root compromise, global nft flush or reboot require separate fail-closed architectural controls.
- The synthetic issuer does not watch real Docker/nft state and cannot terminate a real container. The test does not exercise Docker bridge traffic, nft root namespace, nginx, HomeDocker, real AIO/Jellyfin/Trakt credentials or user history.
- nft `accept` in one base chain may still be dropped by another later base chain; a `drop` verdict is final. This is why the independent deny chain can coexist with the reviewed R23D guard. No generic production conclusion should be drawn about all combinations of Docker NAT/conntrack and other base chains.

## Next review gate

Develop and test the actual independent boot-time fence deployment order, a crash-tolerant guard supervisor and **recovery from total nft ruleset loss and daemon restart** in disposable VM(s), not HomeDocker. Only after real resource ownership, packet controls, fail-safe emergency kill, independent watchdog and protected baseline are validated should a carefully scoped synthetic Docker canary be considered. Authenticated AIO GET relay and v1.4 production cutover stay **BLOCKED**.

**PR status:** Draft #95, unmerged. All privileged CI namespace commands must remain off HomeDocker.
