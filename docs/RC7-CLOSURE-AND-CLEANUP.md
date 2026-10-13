# RC7 closure and cleanup register — 2026-10-13

## Acceptance

**RC7 monitoring / rolling compatibility phase COMPLETE:** HomeDocker operator ran rolling contract against production with status PASS_CAPTURED_CHECKS. Bridge v1.3.0-rc.6 health/readiness true; identity attention=0, observed mitigations=1; 50 Resume and 50 NextUp checked; dual-IMDb groups=0; known fixture present. Nine opaque series IDs and pagination beyond 50 are not fully covered. Operator separately confirmed all affected clients no longer duplicate Continue Watching.

**Not v1.3 Stable release.** Actual Bridge RC6 remains pinned to its qualified image; AIOStreams follows upstream latest independently. Compare-only remains on. No writeback or AIO code/image changes.

## Cleanup dispositions

| Record | Status | Rationale |
| --- | --- | --- |
| PR #86 | Merged as abca532 | RC7 read-only monitors and rolling compatibility tools |
| PR #83 | Closed without merge | One-time API/SQLite diagnosis; preserve PR diff |
| PR #85 | Closed without merge | One-time, exact-18-row privileged repair utility; preserve PR diff, not default release |
| PR #84 | Closed without merge | Upstream AIO source patch explicitly rejected |
| Issue #82 | Resolved by official AIO drop preference plus monitors | Archive evidence and close |
| Issue #70 | Open | Separate native-Trakt recovery writeback safety |
| Dependabot #41, #52 | Open for security/compatibility review | Not experimental runtime drafts |
| Releases/tags/main | Keep | Rollback / audit provenance |

Root cause: one show had two AIO watch-state series keys, Trakt IMDb tt44051354 (watched S01E01–09; S01E10 resume) and AIO IMDb tt44094505 (TMDB episode keys S01E01–08 watched). Official Jellyfin series dropped preference hid the stale AIO S01E09 NextUp. All 18 episode records preserved. Playback under the old alias could undrop it, so no unconditional reapply is allowed.

## GitHub branch cleanup

Scripts/cleanup-github-branches.sh is **dry-run by default** and requires an authenticated GitHub CLI. It only deletes local branches still at the exact head commit of a **merged** PR, with no open PR. A documented allowlist of now-closed RC7 experimental branches may be included after exact HEAD checks. The connected GitHub app does not expose branch ref deletion; no branch deletion is claimed before the owner runs the script.

Branch refs do **not** consume HomeDocker CPU/RAM.

## Docker cleanup

Scripts/audit-homedocker-docker-cleanup.sh inventories containers, images, stopped test leftovers, dangling image IDs, local volume names, build cache and RC7 scratch filenames without exposing secrets. Docker resources have **not** been removed remotely because there is no active SSH session from this assistant.

**Prohibited broad deletion:** docker system prune -a --volumes, docker volume prune, docker compose down -v, indiscriminate image prune, deleting /var/lib/docker contents. Stopped/dangling Docker artifacts may be rollback images, volumes, backup repositories or Portainer state. Identify exact candidates, verify not attached to running/stopped containers and check backup/rollback relevance before removal.

## Completion

- [x] Live HomeDocker contract and real clients verified.
- [x] PR #86 merged; monitor/compatibility CI green.
- [x] RC7 test-only PRs closed unmerged.
- [x] AIO latest and independent Bridge release policy preserved.
- [ ] Local authenticated GitHub branch sweep.
- [ ] HomeDocker inventory and deletion of verified orphaned test resources.
- [ ] Separate Bridge v1.3 Stable soak, backup/rollback and native-Trakt writer gates.
