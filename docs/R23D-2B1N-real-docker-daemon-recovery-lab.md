# R23D-2B1N — Real Docker bridge/veth and daemon-restart recovery lab

**Status:** CI verification pending until the specific job reports completed success. **Safety scope:** a wholly dedicated, disposable GitHub-hosted Ubuntu runner; NOT HomeDocker. Unlike 2B1M, this lab really restarts the runner's Docker daemon. Do **not** run its privileged script manually on a protected host or self-hosted CI.

## Experiment

The lab runs `scripts/probe-v140-r23d2b1n-real-docker-daemon-ci.sh --ci-ephemeral-only`, guarded by explicit CI/runner conditions and root. The declared runner checks are **not cryptographic attestation**. It fails closed unless the runner's Docker daemon is systemd-managed, `LiveRestoreEnabled=false`, and no Docker containers existed at entry.

It creates a single exact-owned **Docker --internal bridge** on `172.31.252.0/28`, with no host-published ports and disabled IP masquerade. Two disposable `python:3.12-alpine` containers use `--restart no`, read-only rootfs, all capabilities dropped, no-new-privileges, no Trakt/AIO credentials, and a read-only mount of the synthetic TCP helper. A fake gateway serves only ports `17420` and `17421`; a same-network fake peer tries to reach both.

After both TCP ports are positively shown reachable, the lab derives the host-side veth from the peer PID's netns `eth0/iflink` and checks the host veth is enslaved to the exact private Docker bridge. It attaches a `tc clsact` ingress filter to that veth: permit ARP and **only** `172.31.252.2 -> 172.31.252.3:17420` TCP; drop everything else. Positive relay and negative forbidden-port controls must pass.

The runner's **actual Docker daemon is then restarted via systemd**, within a dedicated GitHub runner job independent from all other test jobs. The two `restart=no` disposable peers **must remain stopped**, even after Docker has returned. A running peer automatically restored after restart is an immediate FAIL. To re-admit traffic, the lab starts the fake client while its fake server is still stopped, checks ownership and discovers the **newly created host-side veth**. It proves the prior `tc` filter is absent on the recreated interface. The lab then re-arms `tc` on that exact new interface *before* allowing the fake server to start. Allow/deny connections are verified again.

Negative control: in the private bridge only, remove `tc` from the new veth while the fake listeners are running; the formerly forbidden port must immediately become reachable, proving that a direct Docker start without a guard can reopen traffic. Re-arm `tc` and prove forbidden traffic is blocked again. Exit cleanup removes only the two owned Docker containers and the one owned network and verifies absence, without `docker system prune`, Docker volumes, nginx, host nft rules, systemd units, HomeDocker containers or real history.

## Evidence checklist (must be observed in successful workflow)

```text
R23D2B1N_INTERNAL_DOCKER_BRIDGE_OWNERSHIP_PASS
R23D2B1N_UNFENCED_REAL_DOCKER_TCP_POSITIVE_CONTROL_PASS
R23D2B1N_REAL_DOCKER_VETH_TC_INGRESS_CONTROLS_PASS
R23D2B1N_ACTUAL_DOCKER_DAEMON_RESTART_PEERS_HELD_STOPPED_PASS
R23D2B1N_DOCKER_RECREATED_VETH_LOST_OLD_TC_PROVEN
R23D2B1N_TC_REARMED_BEFORE_FAKE_GATEWAY_START_PASS
R23D2B1N_REMOVING_TC_EXPOSES_INTERNAL_DOCKER_PEER_NEGATIVE_CONTROL
R23D2B1N_REARMED_FILTER_FAIL_CLOSED_PASS
R23D2B1N_EPHEMERAL_REAL_DOCKER_RECOVERY_LAB_PASS_NO_HOMEDOCKER_APPLY
R23D2B1N_OWNED_DOCKER_RESOURCES_CLEANUP_PASS
```

## Release gate: still OPEN despite any lab success

This is **not an automatic kernel-level boot guard**. When Docker creates a fresh veth, the old veth's `tc` policy is gone. The `restart=no` client/server can be re-admitted safely **only if a trusted independent launcher enforces the stop-first/re-arm-first sequence**. A privileged operator, daemon restart policy change, existing running peer or malicious process could bypass this procedural sequence. These are material release blockers.

This lab does not reboot the kernel/VM, exercise arbitrary power loss, validate custom systemd ordering in production, prove an automatic independently supervised guardian with a verified kill path, prove full nft+tc loss survival, or route authenticated AIO/Jellyfin/Trakt traffic. The private Docker bridge `--internal` additionally limits network reachability; success cannot be extrapolated to a non-internal Docker production bridge with iptables/nft NAT.

**Target v1.4.0 Stable remains HOLD.** PR #95 remains DRAFT, and the v1.4 tag release workflow must remain blocked. Any production host cutover requires independent fail-closed startup/recovery checks, real authorized client acceptance and immutable backup/rollback evidence.
