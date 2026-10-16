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

## Next owner checkpoint

Maintain Draft PR #95 and integrate with the updated main baseline. Qualify the unresolved independent boot-fence/total-loss scenario in a disposable VM, not on HomeDocker; then prepare version bump and formal candidate review. An actual production deploy needs operator-visible SSH results and explicit scoped backup/rollback checks.
