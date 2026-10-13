# RC7 Phase C — staged AIOStreams NextUp fix (upstream-ready; NOT deployed)

**Status:** canary candidate / proposal. This is NOT a Trakt Bridge runtime change.

Issue #82 and diagnostic draft PR #83 record the HomeDocker evidence. Only the series **Ok! Let's Get Divorced** is affected. AIO local watch-state for the same connected persona has two source timelines:
- Trakt IMDb `tt44051354`: S01E01–09 watched (import), S01E10 local resume `346626 ms`.
- AIO IMDb `tt44094505` with episode keys `tmdb:276470:1:n`: S01E01–08 watched locally; S01E09 appears as its stale `NextUp`.
- Both series groups have NULL `match_key`; therefore AIO NextUp currently returns S01E10 plus stale S01E09 as two different Jellyfin Series IDs.
- The stable show anchors in the Bridge fixture are Trakt show 263102, TMDB 276470 and TVDB 480791.

**Important**: Generic overlap between `Resume` and `NextUp` is normal and **not** the root cause. We are treating **one verified IMDb alias group** in the **NextUp response**, without changing any persisted watch data.

## Candidate approach and limitations

The portable policy `contrib/aiostreams/src/nextup-identity.ts` is a pure, read-only, fail-open function intended to be compiled into AIOStreams' Jellyfin NextUp read-model.

It reads a **deliberate operator-configured** exact IMDb alias set via:

```env
AIO_NEXTUP_IDENTITY_ALIASES=tt44051354,tt44094505
```

The AIOStreams upstream repository does **not currently advertise** that setting. It only works in a **development build which includes this patch**. Do not add it to production Compose and expect the unpatched AIO binary to honor it. The default empty value is a complete no-op.

When at least two *different* known aliases occur in one NextUp response, the policy suppresses other candidates **only if exactly one of the group has a positive resumable position** and is not played. Thus the verified Trakt S01E10 remains, the stale AIO S01E09 disappears, and Resume S01E10 remains unchanged. Ambiguities (both resumable, neither resumable, no alias mapping, invalid config) **fail open and retain the original items**. No fuzzy matching or universal merging. Other clients, libraries and shows are unchanged.

**Caveat:** This is a narrow display/read-model workaround, not a full repair of AIO's history identity model. The two historical SQLite timelines remain separate; duplicate cards can recur in other pagination windows/clients/other API paths. In particular, the proposal does not yet address all NextUp paging/limit semantics; if the stale card consumes one of the first `Limit` slots, removing it late can return one fewer result. This requires upstream review before production.

## Reproducible upstream integration

GitHub connection can push to `olivergiangvu/homedocker-trakt-bridge` but **cannot push to `Viren070/AIOStreams`**. We therefore stage a guarded patcher here instead of pretending an upstream PR/build exists.

Upstream source reviewed and pinned:

```text
Viren070/AIOStreams commit:
bd1de5bc9720a9fbadfa0f990de7113db3decf7f

packages/server/src/routes/jellyfin/library.ts blob:
4d8acdbd5f48013a9fab2ce712156df1447121d2
```

Patcher: `contrib/aiostreams/apply-nextup-canary.py`.

By default it **previews** the changes. With `--apply` it refuses to run unless HEAD matches exactly that upstream commit, the source blob matches, the checkout is clean, and the new helper does not already exist.

The patcher makes four narrowly scoped changes to the AIO `/Shows/NextUp` handler:
1. Imports the new policy.
2. Holds a transient lookup `JellyfinItem.Id -> original source baseId` while building candidates.
3. Records the source base ID from the already-available `recentSeries()` read model; no new requests and no DB writes.
4. Applies the verified alias filter **after** the existing NextUp candidates are built. It does not change the `Resume`, `WatchStateRepository`, `Trakt Bridge`, `NextUp EnableResumable` default, schema or any playlist.

**Developer-only procedure** (on a separate checkout, not inside production stack):

```bash
git clone https://github.com/Viren070/AIOStreams.git ./AIOStreams-rc7-dev
git -C ./AIOStreams-rc7-dev checkout bd1de5bc9720a9fbadfa0f990de7113db3decf7f

# From an independent homedocker-trakt-bridge checkout on this RC7 branch:
python3 contrib/aiostreams/apply-nextup-canary.py ./AIOStreams-rc7-dev

# Only after reviewing the dry-run diff:
python3 contrib/aiostreams/apply-nextup-canary.py ./AIOStreams-rc7-dev --apply

# Build & type-check against the upstream workspace toolchain BEFORE creating a canary image.
# First consult that exact revision's README, lockfile and CI scripts.
```

The patcher never launches a container and never modifies production `/opt/homedocker/stacks/aiostreams` or database volumes.

## Deployment guardrails

Production HomeDocker baseline:
- Trakt Bridge RC6 pinned digest `sha256:d89865742b23a54361bff48637512caa37e4ba1b55f1d2ce4510dc737fd70ab2`.
- AIO current digest `sha256:83893d1c37eaaaf9ad1e9286bd857220a6b8f042a4f1eac3fa7356017aa2b184`.
- AIO current container was built **before** the pinned upstream source commit above; a rebuilt canary is **not bit-for-bit the current production build**. Do not silently cut over or assume differences come only from this patch.
- No use of the production 3240 binding in a canary. Use a separate localhost-only port and isolated config/DB snapshot. Avoid external sync/writer traffic from a canary. The isolation has not been implemented or confirmed yet.
- Do not `docker compose pull/up -d`, restart AIO, edit nginx, change aliases in production env, or rewrite the 18 SQLite rows during this stage.
- Next phase: compile/test the patched AIO fork, then run a **network-isolated canary** against a safe cloned database and assert one NextUp S01E10, one Resume S01E10, and unchanged watched S01E01–09. Only after passing those gates consider whether to propose an upstream PR or ship a pinned patched image.

## Tests

- `test/nextup-identity-candidate.test.mjs`: positive S01E10 case, reverse order, explicit opt-in, multi-group isolation, fuzzy-title false positives, conflicting rewatches and invalid alias syntax.
- `test/test_upstream_nextup_patcher.py`: pinned anchors, preview mode, fail-closed dirty checkout/multiple inserts, explicit apply only.

**This code does not yet change the running HomeDocker AIOStreams image.**
