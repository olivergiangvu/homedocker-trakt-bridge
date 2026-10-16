# R23D-2B1K — Real nft INPUT/FORWARD/IPv6 kernel enforcement (ephemeral CI only)

**Prerequisite:** HomeDocker R23D-2B1J 16/16 PASS and `ABSENT_CONFIRMED_BOOT_FENCING_HOLD`, confirming no R23D container, Docker network, Linux bridge, or nft table exists on HomeDocker. This checkpoint does **not** run privileged network commands there.

## Test environment and safety boundary

The new `r23d-kernel-namespace-lab` job in `.github/workflows/ci.yml` runs only on a **disposable GitHub-hosted Ubuntu 24.04 runner**. Its wrapper explicitly requires `--ci-ephemeral-only`, GitHub CI environment and root privileges; this is a deployment fence **for operations hygiene**, not a cryptographic host attestation. **DO NOT run this shell script on HomeDocker.**

The CI lab creates three uniquely named Linux network namespaces: a server namespace with an isolated bridge named `br-r23dgate`, a fake client peer namespace, and a fake external destination namespace. The bridge IPv4 gateway/client mirror the planned RFC1918 synthetic scope and use only synthetic TCP ports 17420 and 17421. Separate fake listeners are started inside these namespaces. The only nft policy change is `nft --file` run with `ip netns exec <server-namespace>` after rendering the **existing reviewed** `render-v140-r23d-nft-policy-offline.py` rule set. There are NO Docker containers, tokens, production network endpoints, real AIO/Trakt data or modifications to the runner's root netns firewall.

The script uses positive controls **before installing the guard**: both host TCP ports, IPv6 forbidden port, and forwarded external TCP destination must all be reachable. This distinguishes blocked firewall traffic from a missing TCP listener, misconfigured route or unrelated connectivity problem.

It then checks live nft JSON via the pre-existing `attest-v140-r23d-persistent-guard.py.check_guard()`, and proves packet behavior **after installation**: exact allowed fake host relay is reachable; forbidden host INPUT TCP is blocked; external FORWARD TCP is blocked; forbidden IPv6 host INPUT TCP is blocked. The lab deletes only its own nft table **inside the namespace** and verifies previously forbidden synthetic routes become reachable again. It terminates owned fake listeners and deletes only the three namespace resources created during the job, verifying namespace names are absent at exit.

## Actual evidence on GitHub-hosted runner

First kernel CI job on Ubuntu 24.04 successfully emitted:

```
R23D2B1K_PREGUARD_TCP_CONNECTIVITY_PASS
R23D2B1K_KERNEL_INPUT_FORWARD_IPV6_TCP_CONTROLS_PASS
R23D2B1K_GITHUB_EPHEMERAL_NAMESPACE_LAB_PASS_NO_HOMEDOCKER_APPLY
```

Revised CI additionally requires `R23D2B1K_OWNED_GUARD_ROLLBACK_TCP_RESTORED` and `R23D2B1K_OWNED_NAMESPACE_CLEANUP_PASS` before qualifying the final checkpoint. **The precise final commit and workflow run must be used as evidence**, not a claim inherited from the first passing commit.

## Explicit limits — NOT production/boot qualification

- Linux bridge + veth namespace traffic was tested: a real nft kernel INPUT/FORWARD/IPv6 ruleset **works inside that disposable namespace**. It is **not a Docker bridge integration test**; Docker daemon port publishing, DOCKER-USER, conntrack, or NAT on the production host may alter packet paths.
- Rule structural attestation and bounded packet tests are not a persistent independent watchdog; service crash, daemon restart, nft flush, host reboot or boot ordering are not proven.
- The temporary GitHub-hosted job has no HomeDocker R23D image, credentials, protected service identities or real production network routes.
- A successful CI job must never set `real_canary_start_authorized=true`, install systemd services on HomeDocker, or enable authenticated Jellyfin/AIO relay.
- PR #95 stays DRAFT; RC6, R23B, R23A, nginx and Trakt history remain untouched.

**Next meaningful gate:** security-review an independently installed deny/fence lifecycle and separate watchdog in a disposable VM/lab, including teardown and interrupted-process scenarios. Only after independent host integration evidence should any controlled synthetic Docker canary on HomeDocker even be considered.
