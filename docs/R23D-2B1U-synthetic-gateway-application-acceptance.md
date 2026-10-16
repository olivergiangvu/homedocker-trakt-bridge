# R23D-2B1U — Read-only Unified Gateway Application Contract Acceptance

**Synthetic HTTP contract: PASS.** [GitHub Actions 38882412892](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38882412892), dedicated `r23d-gateway-contract-acceptance` job SUCCESS: **all six B1U tests passed** and the general `test` job also SUCCESS. **Scope:** Node 24 synthetic local HTTP tests only. **No real AIOStreams/Jellyfin/Remux/Trellis clients, no production Trakt login, no HomeDocker apply, no database write, no version/tag/release approval.**

## What is being qualified

The existing v1.4 opt-in `createJellyfinGateway()` is an API view module of **one Trakt Bridge product**, not a replacement source of playback or a second Trakt writer. It can modify exactly one limited **read-only NextUp** view for the configured Jellyfin account and verified same-profile IMDb alias pair; it otherwise preserves the AIOStreams response. An independently gated Resume feature is only exact GET upstream passthrough (not a deduplicator). AIOStreams' own Jellyfin API must authenticate/authorize all requests, particularly legacy user-id paths.

The B1U tests use a local fake AIO/Jellyfin HTTP server, two distinct **synthetic user IDs and access tokens**, and an in-memory readonly verified identity graph with a write-rejecting facade. No Trakt API route, OAuth or watch history is used.

## Acceptance matrix

1. **Canonical alias NextUp:** the configured authenticated user receives one active canonical card and preserved upstream item ID, `UserData`, remaining opaque fields, and appropriate collection count. Upstream source JSON is not modified.
2. **Cross-user separation:** another valid authenticated synthetic account receives *only its own unchanged upstream NextUp*; a different account must not gain the configured profile's canonicalization/metadata. A synthetic invalid token receives the upstream 401 unchanged. Each valid 2-card NextUp request has its own `/Users/Me` verification.
3. **Unknown graph:** a mismatch/missing profile graph must preserve the upstream NextUp bytes and not hide watched or resumed cards.
4. **Resume:** off by default (404 without AIO call); when enabled, modern `/UserItems/Resume` and legacy `/Users/{user}/Items/Resume` retain upstream bytes **including movie/episode rows, StartIndex and TotalRecordCount**. Upstream 403 is forwarded for cross-user legacy path and a second authorized user gets its own response. **The upstream mock implements the ACL; this does not independently prove a Gateway-enforced cross-user legacy ACL.**
5. **Zero secondary history-writer behavior:** any attempted `cacheSet`, `transaction` or `prepare` on the injected read-only DB throws and is counted. Gateway requests are GET-only; HTTP `POST` for playback progress, playing/stopped, sync/history and Trakt write paths returns 404 (or 405 on the narrowly permitted GET routes), without ever hitting upstream. No Trakt OAuth, AIO DB or Bridge watch history state is supplied.
6. **Request scope and data minimization:** pagination or SeriesId-filtered NextUp remains upstream-defined without changing the response; metrics are aggregated in-memory and contain no token/user/IMDb values; no public metrics endpoint exists.
7. **Existing safety regression:** ordinary npm test, bounded metadata, user verification, NextUp 8–20s bounded upstream and pure view projection suites continue to PASS.

## Important release distinction

**If the CI suite passes, it qualifies a synthetic HTTP contract only.** It does **not** establish a real client session, Trakt canonical history convergence, client live scrobble, Jellyfin/Remux/Trellis/Strand resume behavior or actual cross-user security of AIOStreams. In particular, Resume trusts upstream authorization; an upstream that accepts another user's legacy Resume path could leak data, so real acceptance must explicitly probe this with isolated read-only credentials before enabling Resume. No production activity should be claimed merely from local fixtures.

**Remaining hard gates:** authenticated same-user AIO/Jellyfin/Trakt proxy interactions on a controlled environment, no duplicate scrobbles/history writes across actual clients, privileged Docker API bypass NO-GO from B1S, non-internal bridge tc/nft admission, true full Gateway reboot and rollback. `V140_STABLE_RELEASE_HOLD` remains mandatory. Keep Draft PR #95 unmerged and do not create a `v1.4.0` tag or change HomeDocker routes.
