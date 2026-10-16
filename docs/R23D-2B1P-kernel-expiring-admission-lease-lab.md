# R23D-2B1P — Real-Docker kernel-expiring admission lease

**Checkpoint:** R23D-2B1P. **Scope:** only a disposable, dedicated GitHub-hosted Linux VM. **No HomeDocker apply, no v1.4 release authorization.** CI outcome must be explicitly observed before marking this lab PASS.

## Addressing the observed B1O fail-open counterexample

R23D-2B1O proved that when guardian **and** watchdog are simultaneously SIGKILLed, an authorized fake TCP relay continues through a statically permissive tc rule. R23D-2B1P adds a **second kernel gate**, an actual `nftables bridge` family forward filter with a **3-second timed IPv4 source lease**. The set is empty by default. The existing B1O guardian may opt in to renew that exact lease only after fresh reciprocal process heartbeat and exact real Docker veth ownership checks. The B1O watchdog does **not** mint leases. Lease refresh uses one `nft -f` batch consisting of `flush set` + `add element ... timeout 3s`, keeping those two operations in one kernel ruleset transaction.

An empty/expired set means the kernel drops new fake relay TCP traffic, without requiring any living guardian/watchdog to revoke a rule. Both directions of the allowed fake relay are gated by the source lease, so an already established TCP socket should also stop delivering responses after expiry. The separate static `tc` filter remains as a defense-in-depth deny for all other fake TCP/IPv6.

## Real Linux kernel packet experiment

- A dedicated Github-hosted Ubuntu 24.04 runner must be empty of other Docker containers. Its one new Docker `--internal` bridge uses `172.31.252.0/28`, with no NAT masquerading or host-published ports.
- Real, zero-credential/no-volume, `--cap-drop ALL`, read-only, `--restart no` Docker peers start in STOPPED state. The fake CLIENT is started first, while the fake GATEWAY/SERVER stays down.
- Derive and verify the client's host veth via Docker PID's namespaced `eth0@ifindex`; scope all tc/nft rules to that exact interface. Install tc and the initially EMPTY kernel lease policy before either process can admit the fake gateway.
- Start B1O guardian + watchdog as separate real OS processes, with **B1P lease mode** on guardian. Show a permitted fake TCP destination `:17420` and a denied fake `:17421`; keep both processes alive for **longer than the nominal 3-second lease** to demonstrate that renewal is real.
- Open an ESTABLISHED TCP session and confirm it exists before killing both guardian and watchdog processes with SIGKILL. No userspace process revokes the nft set or stops the container after the kill. Verify a new TCP handshake gets denied by a bounded kernel lease timeout and a previously established TCP session stops receiving responses. Confirm fake gateway container remained RUNNING to rule out accidental application shutdown as explanation.
- **Negative control:** delete only the test-owned nft bridge table while leaving the known tc allow path active: the previously forbidden-to-access *authorized* fake TCP port must immediately become reachable again. Recreate the same EMPTY kernel nft table to prove denial returns without any process replenishing a lease.
- Cleanup only exact two owned Docker containers, private Docker bridge and the test-created nft bridge table; no global `nft flush ruleset`, host services, systemd units, real credentials, Trakt history or unscoped cleanup.

## Expected acceptance evidence (do not pre-mark PASS)

The private runner must report successful application tests, B1K–M kernel tests, B1N daemon recovery, B1O guardian, **and** the dedicated B1P kernel expiry job. Required B1P result markers:

```
R23D2B1P_REAL_DOCKER_BRIDGE_DEFAULT_DENY_BEFORE_ADMISSION_PASS
R23D2B1P_CONTINUOUS_MUTUAL_LIVENESS_KERNEL_LEASE_ADMISSION_PASS
R23D2B1P_ACTIVE_3S_KERNEL_LEASE_REFRESH_PASS
R23D2B1P_DUAL_SIGKILL_KERNEL_LEASE_FAIL_CLOSED_WITHOUT_USERS_PASSES
R23D2B1P_DUAL_SIGKILL_EXISTING_TCP_SESSION_CUTOFF_PASS
R23D2B1P_DELETING_LAST_KERNEL_LEASE_FENCE_REOPENS_TCP_CONTROL
R23D2B1P_EMPTY_KERNEL_LEASE_RECREATED_DENY_PASS
R23D2B1P_REAL_DOCKER_KERNEL_EXPIRING_ADMISSION_LAB_PASS_STABLE_HOLD
R23D2B1P_OWNED_DOCKER_AND_NFT_CLEANUP_PASS
```

## Hard limitations — production HOLD

1. **Bounded eventually fail-closed, not instantaneous:** the last valid kernel lease can remain usable for up to its remaining 3 seconds after both processes disappear. No guarantee of zero data transfer during that window.
2. **Total nft loss:** if the entire nft policy itself disappears (host-global ruleset flush), tc still allows the fake authorized 17420 path. The negative control deliberately demonstrates this. Another *independent* hard deny/boot interlock is necessary.
3. **Docker veth recreated:** an nft rule scoped to an old interface cannot be assumed to protect the new veth. B1N proved tc also vanishes on veth recreation. This test does not qualify boot, reboot, unexpected container starts, adversarial root access, or fail-safe admission across daemon/network recreation.
4. **Real client coverage missing:** this tests fake TCP only, **not** real Jellyfin/AIO/Remux/Trellis clients, canonical Trakt identity/history, any production route, TLS/nginx or user permissions.
5. **No production software installation:** B1P uses the existing development-only B1O guardian for an opt-in kernel-lab lease. It is not a production supervisor, installer, privileged service template or approval to create HomeDocker nft rules.

Keep PR #95 Draft and `V140_STABLE_RELEASE_HOLD` until independently verified boot-time admission and production-shaped Docker/NAT/reboot as well as authenticated application-client acceptance and rollback gates are satisfied.
