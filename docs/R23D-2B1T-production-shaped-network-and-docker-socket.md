# R23D-2B1T — Production-shaped Docker networking and Docker-socket privilege boundary

**Scope:** dedicated disposable GitHub-hosted Linux runner ONLY. **No HomeDocker host apply. No authenticated media traffic.** A passing B1T lab qualifies real **non-internal Docker networking** and one unprivileged socket boundary, **not production admission security**.

## Real kernel/Docker experiment

An otherwise empty GitHub runner creates:
- A labeled `bridge` Docker network with `Internal=false`, masquerading enabled, subnet `172.31.250.0/28`.
- A synthetic Python gateway initially `--network none`, `--restart no`, read-only, unprivileged, no credentials/volumes. Only synthetic TCP `17420` is host-published, and **only to `127.0.0.1` with an ephemeral port**; `17421` is never published.
- A same-bridge Python client and a completely local but **non-Docker external Linux network namespace** `172.31.249.2/30` containing a fake TCP source-IP echo server. The external namespace has no route to the internet.
- An exact loopback host TCP probe for the fake allowed server, an in-container probe for the fake untrusted port, and an external-namespace echo showing the Docker client source address was SNATed to the disposable host-side veth address `172.31.249.1`.

The lab refuses to operate on a self-hosted runner, a prepopulated Docker runner, or an unexpected existing bridge/namespace. Its actual network resources are fixed local addresses, zero production credentials and no WAN dependencies. It checks the effective Docker socket mode and uses a process with UID/GID 65534 and **empty supplementary groups** to verify that a normal unprivileged caller cannot access `/var/run/docker.sock`. Root access is deliberately used only by CI to perform a scoped controlled admission and teardown.

## Fail-safe/negative controls

1. Unexpected start of the fake server while `--network none` cannot expose the fake protocol.
2. Root can administer the isolated fake Docker network and container; unprivileged UID 65534 cannot access the daemon socket. **This does not prove protection from root or users in the docker group.** They have root-equivalent Docker capabilities.
3. A root-approved test-only admission attaches the fake server and publishes one loopback-only port. Verify `127.0.0.1:ephemeral` actually speaks the fake TCP protocol. Verify the forbidden fake port has no host port binding.
4. The fake external namespace must observe host-veth source IPv4 after masquerade, **not** the original container IPv4; this proves real Docker egress SNAT using a controlled local simulated external network.
5. Exact `docker network disconnect` of the fake gateway must make that host-published TCP protocol unreachable, without touching another container/network. The observed forwarding/cleanup behavior may vary by Docker/kernel backend; CI records reality rather than assuming it.
6. Exact cleanup: only two named fake containers, one named Docker bridge, one synthetic network namespace and its veth pair. Never prune images, volumes, nft rulesets, Docker system services or a real HomeDocker service.

## Critical remaining release blockers

- This lab **does not** include an independently enforced nft/tc boot barrier on its non-internal bridge; B1Q/R proved only scoped INTERNAL bridge patterns. Network shape alone does not certify v1.4 production admission.
- Loopback binding and effective inspect metadata do not prove all L2/LAN paths are unreachable under every Docker release/kernel configuration; no separate LAN attacker host is used.
- Root and members of the docker group can bypass Docker API admission checks; the negative case proven in B1S remains unresolved without strict authorization and an enforcement domain outside unrestricted Docker API access.
- No published-port DNAT/conntrack churn across a true VM kernel reboot, no full nft policy loss across production-shaped Docker, no authenticated AIOStreams/Jellyfin/Trakt Gateway use, no stable image, no rollback verification.
- Even eventual systemd polling disconnection after nft loss is **not atomic** and cannot be described as zero-leak.

**Stable decision:** maintain `V140_STABLE_RELEASE_HOLD`; Draft PR #95 remains unmerged. Subsequent qualifications must verify production-shaped ingress, root/docker-group security assumptions, and a genuine AIO/Jellyfin/Trakt authorized client before any HomeDocker route cutover.
