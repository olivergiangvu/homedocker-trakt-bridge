# R23D-2B1S — Real QEMU Ubuntu guest reboot and privileged Docker bypass counterexample

**STATUS: PENDING FINAL CI EVIDENCE.** Both experiments are *isolated* disposable GitHub-hosted CI; no HomeDocker host actions. A real **QEMU guest** kernel reboot is materially different from a restart of the GitHub runner or the host's Docker daemon. A successful QEMU guest smoke is not production admission qualification.

## Experiment A — privileged Docker API bypass (negative control)

The disposable B1R private Docker/systemd monitoring lab runs with its exact test-owned systemd monitor and fake Docker peers only. After successful baseline B1R controls, the lab removes the **owned nft table**, confirms the monitor disconnected and stopped the fake gateway, pauses the monitor's exact `MainPID`, then — using test-runner root/Docker privileges — directly attaches the fake gateway to the owned Docker bridge and starts it **without going through systemd admission**. The synthetic allowed TCP `:17420` must become reachable, despite absent nft policy. After unpausing, the monitor must eventually disconnect/stop the bypassed fake container, and traffic must cease. This is a **confirmed vulnerability only if CI demonstrates the positive live TCP path**, not just a unit-test assertion. It is not a production mitigation. `systemd Restart=always` and a polling monitor cannot forbid a separate root-level Docker API caller from creating an external bridge endpoint.

Guardrails: clean GitHub runner, `--ci-ephemeral-only`, exact fake named/labeled containers, private Docker `--internal` bridge, no host-published fake media ports, no secrets or volumes. It installs/removes only a test systemd unit, lab Docker resources, and scoped lab nft table. A paused monitor is always resumed during cleanup. Host-global rules and real services are never modified.

## Experiment B — true VM/kernel reboot with guest Docker + systemd ordering

A *separate GitHub-hosted Ubuntu runner* uses a **KVM-backed QEMU Ubuntu Server 24.04 guest**. The guest image is downloaded from Ubuntu's official cloud image release directory, validated against its accompanying SHA256SUMS; an ephemeral SSH key is generated only for this guest. It installs **Docker inside the guest** via cloud-init, creates an exact fake image and one stopped `--restart no --network none` container **inside the guest**, then installs an actual guest systemd `Requires=docker.service`, `After=docker.service` one-shot boot-check unit.

Evidence must include:
- guest Docker and systemd available on initial boot, first `/proc/sys/kernel/random/boot_id`;
- real **`sudo systemctl reboot` inside QEMU guest only**;
- a *different* guest kernel `boot_id` after the new SSH session;
- the boot-check unit running automatically on the new guest boot and recording that same *new* kernel boot ID;
- Docker available on the new boot; fake container remains STOPPED and `network none` with restart policy `no`;
- cleanup kills the exact QEMU process and removes only the newly created transient guest disk, seed, and key from the CI runner.

If the runner lacks usable `/dev/kvm`, the job must be BLOCKED/FAIL, not silently skip or claim a successful reboot. The guest is reachable on localhost-forwarded SSH only. This is a *reboot admission HOLD smoke*: it does **not install the B1R monitor inside the guest**, or exercise real Docker veth/tc/nft lease restoration across the guest cold boot. It does not exercise a non-internal production media network.

## Production release blockers (unchanged)

Even if the B1S QEMU job and adversarial Docker job both PASS, the actual release requires the B1R controller and boot-time fences to be installed/tested **together in one rebooting disposable VM**, with a zero-bypass network admission mechanism independent of the root-privileged Docker API, a fail-closed response to total nft deletion with no unsafe exposure, real non-internal bridge/port-published NAT/IPv6, authenticated Jellyfin/AIO/Trakt resume/history tests, and immutable backups with a verified host rollback.

**`V140_STABLE_RELEASE_HOLD` remains mandatory; Draft PR #95 must not be merged/tagged as Stable. No HomeDocker changes.**
