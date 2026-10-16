# R23D-2B1M — Independent tc fence through total nft loss (CI namespaces)

**Scope:** An isolated disposable GitHub-hosted Linux runner with three private namespaces; no HomeDocker commands, no Docker daemon lifecycle, no external AIO/Trakt credentials, no production bridge, no actual reboot. **Not a v1.4 release qualification.**

**Latest independently checked run:** [GitHub Actions 38866339961](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38866339961), both `r23d-kernel-namespace-lab` and `test` **completed SUCCESS** on commit `d026eb77f712059180be2b36343744c77780f8d9`. All eight 2B1M kernel markers below were observed directly in the job log. Earlier run `38866103916` correctly FAILED its positive allowed TCP path; review fixed ARP handling and explicit `tc goto chain 1` processing. That fail is preserved as diagnostic evidence, not counted as a PASS.

## Why this lab exists

R23D-2B1K demonstrated nft INPUT/FORWARD/IPv6 packet behavior; 2B1L showed expiry of a one-shot 4-second nft lease after userspace SIGKILL and survival after only a **secondary nft table** is removed. Neither shows safety when the **entire nft ruleset** disappears. The R23D-2B1M namespace test introduces a *separate Linux kernel enforcement mechanism*, ingress `tc clsact` on the client-facing veth, installed before its link is brought UP. After permitting required IPv4 ARP frames through an isolated classifier chain, only exact synthetic TCP IPv4 `172.31.254.2 -> 172.31.254.1:17420` may reach the lab fake relay; all other packets (including IPv6 and forwarding) are dropped by `tc`.

## Real kernel experiment and controls

Code: `scripts/probe-v140-r23d2b1m-tc-nft-total-loss-ci.sh`. Explicit `--ci-ephemeral-only` plus CI/GitHub-hosted guard is required. These checks **are not cryptographic host attestation**; *do not execute this shell script manually on HomeDocker*.

1. Create three newly owned private Linux network namespaces (server/client/external), fake bridge, veth connections and fake TCP listeners only on synthetic 17420/17421 ports. Keep server-facing client veth DOWN.
2. Verify client cannot reach the fake permitted listener **before** the barrier is installed.
3. Install independent ingress `tc clsact` on the server-side veth and verify its allow+terminal-drop filters are present. Only then bring veth UP; prove allowed relay reachable, forbidden host INPUT, forwarded TCP and IPv6 cannot pass.
4. Install exact previously reviewed synthetic nft policy **inside the server namespace**, verify permitted/forbidden traffic.
5. Invoke `ip netns exec $PRIVATE_SERVER nft flush ruleset` — the entire **private server namespace** nft ruleset, **never** the runner root namespace or HomeDocker. Verify no nft rules remain, but exact permitted TCP still works and all forbidden TCP/IPv6 remains blocked, solely by `tc`.
6. Replace the fake TCP relay listener process and verify `tc` remains effective; no Docker daemon, kernel reboot or systemd service restart is performed.
7. **Negative control:** deliberately delete only this lab's `tc clsact` while nft is empty; prove *all previously blocked live fake servers* become reachable. Reinstall `tc`, prove they are blocked again. This proves negative results were genuine packet enforcement, not listeners that failed to start.
8. Kill owned fake listeners and remove only three owned namespaces, checking no names remain. No host-global nft flush, interface deletion or port modifications.

## Verification

Each GitHub CI run for this checkpoint must show the dedicated kernel namespace job and standard test job SUCCESS, with markers:

```
R23D2B1M_LINK_DOWN_BEFORE_FENCE_PASS
R23D2B1M_TC_BOOT_BARRIER_AND_PACKET_CONTROLS_PASS
R23D2B1M_NFT_PLUS_TC_COMBINED_PASS
R23D2B1M_TOTAL_NFT_FLUSH_TC_INDEPENDENT_DENY_PASS
R23D2B1M_SYNTHETIC_LISTENER_RESTART_GUARD_PERSISTS_PASS
R23D2B1M_REMOVING_LAST_FENCE_EXPOSES_FORBIDDEN_TCP_POSITIVE_CONTROL
R23D2B1M_TC_FENCE_RESTORED_PASS
R23D2B1M_OWNED_NAMESPACES_CLEANUP_PASS
```

All listed markers were observed in the successful CI job referenced above; any later code changes require a fresh run.

## Safety implications and residual blockers

- This adds a second independent Linux kernel **tc** path that can survive nft loss on an **existing, unchanged veth**. It does **not** prove the tc qdisc survives Docker bridge/veth recreation, kernel reboot, root compromise, or host network reset.
- The 'boot barrier' is merely deliberate sequencing before a test veth is brought UP. Real HomeDocker systemd boot-order, Docker restart and unexpected peers may bypass it without independently enforced container launch restriction.
- A fake listener process restart is **not a Docker daemon restart** and does not validate Docker publishing, NAT/conntrack or real packets. The next phase must use a disposable VM with a real Docker bridge and daemon lifecycle.
- This lab intentionally permits only one synthetic TCP destination and drops everything else. This policy must not be copied into a live media network; it is not a general production firewall design.
- No auth AIO relay, NextUp routing, history writes, nginx changes, systemd installs, Docker permissions or production changes. **PR #95 remains Draft and `V140_STABLE_RELEASE_HOLD` remains mandatory.**
