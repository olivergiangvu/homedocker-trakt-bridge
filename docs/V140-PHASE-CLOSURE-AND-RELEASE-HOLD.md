# v1.4.0 Unified Gateway — phase closure register and release hold

**Date:** 2026-10-16
**Release status:** `V140_STABLE_RELEASE_HOLD`
**Latest GitHub stable:** `v1.3.0` published 2026-10-14; immutable GHCR digest `sha256:b15e31895eef825f55f33ada6fa3066747a6f954fbdf4acedb7a083615126f37`
**Development PR:** [#95](https://github.com/olivergiangvu/homedocker-trakt-bridge/pull/95) — still Draft, NOT merged or published.
**Latest host baseline observed:** original pinned `v1.3.0-rc.6` Bridge, untouched AIOStreams, protected R23A/R23B. Never infer a stable host deployment from GitHub publishing.

## Closed development / laboratory checkpoints

These milestones are closed **only for their indicated test scope**. They do not independently grant release, production routing, firewall apply or history write authorization.

| Checkpoint | Qualified scope | Evidence |
| --- | --- | --- |
| R23D-2B1C | Existing source-pinned synthetic Gateway image/entrypoint | HomeDocker original immutable image and protected baseline attested |
| R23D-2B1D | Offline lifecycle and supervisor model | 28/28 on HomeDocker |
| R23D-2B1E | Host read-only systemd/boot-fence preflight | 12/12; `BOOT_FENCING_HOLD` |
| R23D-2B1F | Abstract systemd ordering and restart model | 22/22 on HomeDocker |
| R23D-2B1G | Real independent Python subprocess LAB, synthetic evidence | 19/19 plus SIGTERM 5/5 on HomeDocker |
| R23D-2B1H | Real host read-only Docker/interface/nft resource absence inventory | 13/13; no R23D resources |
| R23D-2B1I | Synthetic joint Docker image/peer + nft rule structural attestation | 18/18 on HomeDocker |
| R23D-2B1J | Actual read-only Docker and nft collection, fail-closed absence | 16/16; `ABSENT_CONFIRMED_BOOT_FENCING_HOLD` |
| R23D-2B1K | **Real nft kernel packet-path** INPUT/FORWARD/IPv6, scoped teardown | PASS on ephemeral GitHub-hosted Linux network namespaces only |
| R23D-2B1L | **Real nft kernel 4-second lease expiry** after synthetic issuer SIGKILL, secondary guard loss, scoped teardown | PASS on ephemeral GitHub-hosted Linux network namespaces only |
| R23D-2B1M | **Real independent `tc clsact` boot/link barrier and full private-namespace nft loss**; negative control verifies removal of tc restores prohibited traffic | PASS on disposable GitHub-hosted Linux network namespaces, [CI 38866504310](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38866504310); no Docker host reboot |
| R23D-2B1N | **Real Docker internal bridge/veth and systemd Docker daemon restart**; both restart=no fake peers remain stopped; recreated veth loses tc and must be re-armed before fake gateway start; positive/negative TCP controls + cleanup | PASS on dedicated disposable GitHub-hosted Ubuntu runner, [CI 38868129308](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38868129308) all three jobs; no real host reboot or non-internal production bridge |

Detailed experimental evidence remains in separate Draft PR #95 documents `docs/R23D-2B1[D-M]*`. **Do not copy these lab-specific procedures into a HomeDocker live install runbook or claim these are production-proven.**

## Still OPEN / blocking a production release

1. **Automated boot-time fence:** 2B1N demonstrated an intentional *stop-before-rearm* sequence on a real, disposable Docker daemon restart. Still OPEN: independently enforced admission/guardian ordering **before** Docker restores/launches any actual R23D peer, across host reboot, unexpected external starts, and daemon/network failures. A manual CI sequence is not boot-time protection.
2. **Guardian and kill path:** actual continuously supervised independent process, fresh authenticated Docker and nft evidence, bounded traffic kill when guard fails (not simply a one-shot timed lease with up-to-4-second exposure after SIGKILL).
3. **Independent fence after actual Docker networking recovery:** R23D-2B1M performed a **total nft ruleset flush only inside a disposable private network namespace with an unchanged veth**, proving a separate `tc` filter remained active there. R23D-2B1N then confirmed that **actual Docker daemon restart and veth recreation remove the old `tc` enforcement from the new interface**; on that dedicated runner the fake gateway remained stopped until tc was re-armed. Still OPEN: automatically fail closed across non-internal bridge reconstruction, unexpected container restarts, full host firewall loss, host reboot and both guards missing without trusting a manual start order.
4. **Production-shaped Docker integration:** 2B1N proves real **internal** Docker bridge, owned veth, exact no-published-port fake peers, IPv4 allow/deny and daemon restart. Still OPEN: **non-internal** production-shaped Docker bridge with published ports, NAT/conntrack, IPv6, one allowed peer and independent enforcement over INPUT/FORWARD paths; prove automatic network ownership and no unguarded launch on a disposable VM.
5. **Unified Gateway functional acceptance:** integrate latest reviewed `main` with Draft PR #95 and preserve single-Bridge architecture. Qualify read-only NextUp alias canonicalization, bounded upstream behavior, identity graph, Resume behavior, no duplicate history writer, no Trakt/Jellyfin cross-user leakage.
6. **Release engineering:** build a coherent `1.4.0-rc` candidate after blockers, qualification/soak, SQLite online backup and one-service rollback rehearsal. Only after observed host protection and exact digest checks can `v1.4.0` stable tag be approved.

**Dangerous false shortcut:** passing GitHub CI on an isolated namespace does not prove HomeDocker boot/daemon restart safety. Tagging `v1.4.0` would trigger the repository's release workflow, publish a stable GHCR image and potentially move `:latest`. A CI fail-fast gate has therefore been added to the release workflow for `v1.4*` tags until a separate reviewed change explicitly lifts it after complete qualification. This gate is a workflow safeguard **only for tagged commits containing the updated workflow**; GitHub tag protections or rulesets are still recommended to prevent creating tags from older unguarded commits.

**R23D-2B1N real Docker recovery update (2026-10-16):** Dedicated GitHub-hosted Ubuntu runner CI [38868129308](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38868129308) reports **three jobs SUCCESS**, including actual `systemctl restart docker` under an empty-runner precondition. Both no-restart fake containers stayed stopped. Their recreated Docker veth lost the original `tc` fence; a deliberately scoped procedure held the fake gateway down until the new veth was authenticated and re-armed. Positive allow and forbidden TCP, last-fence removal negative control, reinstall and deletion of owned Docker resources PASS. Initial run `38867975197` failed veth-identity mapping and was fixed by using `eth0@ifindex` through namespaced `ip`. **No real system reboot, no general Docker external traffic/iptables-NAT certification, no automated independent supervisor or HomeDocker host apply.**

**R23D-2B1M kernel update (2026-10-16):** GitHub runner CI `38866504310` both jobs SUCCESS. Private veth remained DOWN until `tc` was attached; the exact fake relay survived a full nft flush in that namespace, forbidden IPv4 TCP forwarding/INPUT and IPv6 remained blocked, and removing tc caused forbidden positive-control connections to succeed. Initial allow-path CI failure `38866103916` was fixed by allowing ARP and jumping to an explicit tc accept chain. This is **not** evidence that tc survives Docker network recreation or system boot. Full release HOLD remains.

## GitHub documentation cleanup completed

- User-facing setup, configuration, operations and release notes remain canonical under `docs/`; keep public v1.3.0 release, tag and old notes immutable.
- Current-v1.4 status is one short **closure register** in main. Deep tests/prototype docs remain attached to Draft PR #95 until reviewed for merge. Do not delete source evidence, snapshots or safety tests simply to shorten the repository.
- No repo-scope `git push --force`, branch/tag deletion, or release rewrite is part of this cleanup. Do not delete active PR #95's branch, archived release tags or protected RC6/R23A/R23B rollback assets.

## HomeDocker cleanup and deploy disposition

**No remote host access in this GitHub-only workflow.** This register never claims a successful HomeDocker cleanup or Gateway deployment.

Only run the existing **read-only** inventory `scripts/audit-homedocker-docker-cleanup.sh` in a pinned checked-out main commit. Review dangling images/volumes, active containers, network ownership and old rollback needs before approving **exactly named** deletions. Do not run generic `docker system prune -a --volumes`, `docker volume prune`, Portainer bulk redeploy or AIO/Trakt history cleanup.

**Explicit release checkpoint:** `v1.4.0` publication **NO-GO**, Docker/nft/systemd host mutation **NO-GO**, authenticated Gateway relay **NO-GO**, host cleanup mutations **NO-GO until exact candidates and rollback evidence**. The user requested release/deploy/cleanup, but without these proofs it cannot be represented as completed.

## Operator-confirmed exact Docker image cleanup — 2026-10-16

HomeDocker operator executed a previously reviewed exact-image-ID cleanup script and pasted the full successful terminal output. All pre-delete checks passed: Netdata and CrossWatch healthy, preserved rollback images existed, three removed images were unreferenced by any Docker container, and repo digests matched. Deletions reported:

| Removed image | Repository | Disposition |
| --- | --- | --- |
| `sha256:14bff67c4793...` | obsolete Netdata | exact old digest removed |
| `sha256:6625e40fa9f3...` | obsolete CrossWatch | exact old digest removed |
| `sha256:85e4ca3ada3d...` | obsolete CrossWatch | exact old digest removed |

**Verified operator output:** Docker image logical totals `9.660 GB -> 8.512 GB` (**1.148 GB less**); reclaimable `3.366 GB -> 2.218 GB`; image count `35 -> 32`; container count **19 unchanged**; **0 containers and 0 volumes deleted**; active volumes **13 of 13**. No blanket prune. These are the user's observed Docker summary values, not filesystem block-level reconciliation.

**Protected evidence at image-audit time:** production RC6 running image `sha256:74ef797c7d8556f27f57571023cc2a9371afc81e13766b81295490df745e2116` remained in use; AIOStreams was unchanged; old R23A integrated canary was stopped with restart=no and no mounts, and shared image `sha256:6f239a2f...` with **running R23B**. Resume contract canary runs on `media_core_net` with a read-only identity-proof bind mount and must not be disrupted. At image-audit time, private R23A network `homedocker_v140_canary_internal` had no active endpoint, but the stopped container still referenced it.

The bounded `scripts/retire-v140-r23a-stopped-canary.py` helper is **audit-only by default**; it can retire **only** that stopped, no-mount R23A container and its empty exact-name private network after confirming immutable image and protected RC6/R23B/Resume identities. Its `--apply` needs an exact opt-in phrase and is for the operator to execute through SSH after auditing. The helper does **not** delete a single image, volume, active canary, route, nft rule or systemd service. No HomeDocker execution is inferred from merging GitHub code.

## R23A exact container/network retirement — CLOSED on HomeDocker (operator evidence, 2026-10-16)

Operator ran the immutable main-source checkout at `f72a51381b3dd3a8880f35e263b4efefd3ce2766`. The exact-target `scripts/retire-v140-r23a-stopped-canary.py` regression suite returned **10/10 PASS**, its default ownership preflight returned `ELIGIBLE_DRY_RUN_NO_DELETION`, and its separately confirmed `--apply` returned:

```text
"action": "R23A_CONTAINER_AND_EMPTY_NETWORK_REMOVED"
"only_target_container": "homedocker-v140-integrated-canary"
"only_target_network": "homedocker_v140_canary_internal"
"images_deleted": 0
"volumes_deleted": 0
"trakt_or_aio_history_modified": false
V140_TARGETED_RETIREMENT_COMPLETE
```

Post-cleanup **operator-observed** inventory: **18/18 containers running**; Trakt Bridge RC6, R23B and Resume contract canaries, AIOStreams, and other production services remained running; **32 images / 18 active / 8.512 GB**, **13/13 volumes active (~2.53 GB)**. Images, volumes and Trakt/AIO history were not targets of this operation. No new disk reduction is attributed to container/network retirement; the earlier **1.148 GB image reduction** belongs exclusively to the separate old Netdata/CrossWatch removal.

**Phase verdict:** `R23A_RESOURCE_RETIREMENT_COMPLETE` for the one stopped container and zero-endpoint private network; do **not** rerun `--apply` after the resources are absent. This is **not** a statement that the two remaining active canaries are retired, or that the v1.4 production security/release gates are satisfied.

**Unchanged restrictions:** retain the running R23B canary `homedocker-v140-r23b-canary` and the Resume contract canary `homedocker-resume-contract-canary` pending route/dependency verification, preserve original RC6 image and rollback history, and do not delete Docker volumes, media networks, nginx routes or source evidence.

## Next owner checkpoint

Maintain Draft PR #95 and integrate with the updated main baseline. Design and prove **automatic independent admission/boot fencing** around the already-qualified disposable real-Docker daemon restart; then test non-internal production-shaped bridge/NAT and true host reboot in a disposable VM, not on HomeDocker. Only after these release gates pass may a v1.4 candidate/version bump be considered. An actual production deploy needs operator-visible SSH results and explicit scoped backup/rollback checks.
