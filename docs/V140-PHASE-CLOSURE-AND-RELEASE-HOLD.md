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

Detailed experimental evidence remains in separate Draft PR #95 documents `docs/R23D-2B1[D-L]*`. **Do not copy these lab-specific procedures into a HomeDocker live install runbook or claim these are production-proven.**

## Still OPEN / blocking a production release

1. **Boot-time fence:** an independent effective traffic-deny mechanism must be active **before** Docker restores/launches the R23D peer; safely handle restart, daemon startup and unexpected external launches.
2. **Guardian and kill path:** actual continuously supervised independent process, fresh authenticated Docker and nft evidence, bounded traffic kill when guard fails (not simply a one-shot timed lease with up-to-4-second exposure after SIGKILL).
3. **Full ruleset loss:** safely handle **total nft ruleset flush**, not just deletion of a secondary table; demonstrate fail-safe deny independently of disappearing nft policy or prevent unguarded canary startup.
4. **Docker integration:** prove real Docker bridge, published port/NAT/conntrack, namespace ownership, IPv6 scope, exact image, one peer, firewall INPUT/FORWARD and positive/negative fake TCP paths on a disposable VM.
5. **Unified Gateway functional acceptance:** integrate latest reviewed `main` with Draft PR #95 and preserve single-Bridge architecture. Qualify read-only NextUp alias canonicalization, bounded upstream behavior, identity graph, Resume behavior, no duplicate history writer, no Trakt/Jellyfin cross-user leakage.
6. **Release engineering:** build a coherent `1.4.0-rc` candidate after blockers, qualification/soak, SQLite online backup and one-service rollback rehearsal. Only after observed host protection and exact digest checks can `v1.4.0` stable tag be approved.

**Dangerous false shortcut:** passing GitHub CI on an isolated namespace does not prove HomeDocker boot/daemon restart safety. Tagging `v1.4.0` would trigger the repository's release workflow, publish a stable GHCR image and potentially move `:latest`. A CI fail-fast gate has therefore been added to the release workflow for `v1.4*` tags until a separate reviewed change explicitly lifts it after complete qualification. This gate is a workflow safeguard **only for tagged commits containing the updated workflow**; GitHub tag protections or rulesets are still recommended to prevent creating tags from older unguarded commits.

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

**Protected evidence:** production RC6 running image `sha256:74ef797c7d8556f27f57571023cc2a9371afc81e13766b81295490df745e2116` remains used; AIOStreams unchanged in the audit; old R23A integrated canary is stopped with restart=no, no mounts, and shares image `sha256:6f239a2f...` with **running R23B**. Resume contract canary runs on `media_core_net` with a read-only identity-proof bind mount and must not be disrupted. Old private R23A network `homedocker_v140_canary_internal` has no active endpoint, but the stopped container still refers to it.

The bounded `scripts/retire-v140-r23a-stopped-canary.py` helper is **audit-only by default**; it can retire **only** that stopped, no-mount R23A container and its empty exact-name private network after confirming immutable image and protected RC6/R23B/Resume identities. Its `--apply` needs an exact opt-in phrase and is for the operator to execute through SSH after auditing. The helper does **not** delete a single image, volume, active canary, route, nft rule or systemd service. No HomeDocker execution is inferred from merging GitHub code.

## Next owner checkpoint

Maintain Draft PR #95 and integrate with the updated main baseline. Qualify the unresolved independent boot-fence/total-loss scenario in a disposable VM, not on HomeDocker; then prepare version bump and formal candidate review. An actual production deploy needs operator-visible SSH results and explicit scoped backup/rollback checks.
