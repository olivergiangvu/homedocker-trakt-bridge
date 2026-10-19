# RC3-P2 — AIOStreams NextUp offset contract: source-verified root cause and remediation gate

**Disposition:** Upstream API contract defect confirmed by read-only HomeDocker test AND upstream AIOStreams source on 2026-10-19. Do not activate RC3 client filters, move Odin, patch live AIO, publish port 7101, replace Bridge RC2, or loosen filtering safety.

## Evidence on the mapped HomeDocker Jellyfin account

R4-C diagnostic (three NextUp GETs; filter OFF; no client ingress):

| Probe | Observed |
| --- | --- |
| GET /Shows/NextUp?StartIndex=0&Limit=20 | 20 cards, response StartIndex matches 0 |
| GET /Shows/NextUp?StartIndex=20&Limit=20 | 20 cards, response StartIndex **does not** match 20 |
| GET /Shows/NextUp?StartIndex=0&Limit=20 (replay) | matches initial response |
| Page 0 vs page 20 | **same episode sequence**; **20/20 item ID overlap** |
| Gateway and session | User identity matched; Gateway filter OFF throughout; production untouched |

These observations show that **the currently deployed AIO NextUp API disregards the requested offset for this call**. No identifying values were printed. The test is not a proof of a universal behavior for all deployments or versions, but source inspection corroborates it.

## Primary evidence from official upstream repository

- Source: [Viren070/AIOStreams — packages/server/src/routes/jellyfin/library.ts](https://github.com/Viren070/AIOStreams/blob/6ca32ed21e3036d87c35c49b24f2cf507d065ed1/packages/server/src/routes/jellyfin/library.ts#L894-L972), upstream main commit 6ca32ed (18 Oct 2026), NextUp around lines 894–972.
- Route at /Shows/NextUp caps Limit to 50.
- With no SeriesId/ParentId, it creates a recent series generator, collects up to limit items and calls send(req,res,items,items.length,0). It never parses StartIndex. Thus StartIndex=20 or any later offset still returns page zero.
- Contrast: the /UserItems/Resume route just above it explicitly parses StartIndex, fetches startIndex + limit rows, slices and passes startIndex to send. That API has a different contract; Resume is not a NextUp equivalent.
- Underlying [watch-state.ts recentSeries()](https://github.com/Viren070/AIOStreams/blob/6ca32ed21e3036d87c35c49b24f2cf507d065ed1/packages/core/src/db/repositories/watch-state.ts#L527-L609) already pages watch-state candidate rows internally using SQL LIMIT/OFFSET and a stable order within a query, but the NextUp public endpoint never consumes an HTTP offset.

**Root-cause boundary:** Source evidence is against upstream main at the pinned commit, consistent with the HomeDocker live result. It is not a binary/image provenance attestation of the running AIO release. Avoid assuming every version or a future AIO fix behaves identically.

## Recommended upstream change (AIOStreams, not Bridge)

Implement a true user-scoped Jellyfin NextUp page contract:

1. Parse StartIndex as nonnegative integer (default 0); retain bounded Limit 1..50, enforce query validation. Maintain original sorting/filtering and SeriesId case semantics.
2. Build a deterministic, ordered **qualified NextUp list** from the existing per-user watch-state iterator and episode resolver, not SQL row offset alone. A candidate row can fail to produce a NextUp item; therefore slicing before qualification skips records and is not correct.
3. Return Items = qualifiedRows.slice(StartIndex, StartIndex+Limit), and response StartIndex = requested StartIndex. Two pages for the same unchanged user state must not overlap or repeat. Keep JSON type and episode IDs unchanged.
4. The existing NEXT_UP_SHOWS scan ceiling is 200 candidate rows, not a proof of global total. Do not claim TotalRecordCount is exact when additional candidates may remain outside a bounded scan. To guarantee a total, exhaust the iterator or adopt a documented consistent, bounded completion contract; if it cannot be established, signal an explicit unqualified state instead of advertising fetched-prefix total as global truth.
5. A stable response requires consistent user watch-state and metadata across the scan. An incremental API cannot prove transactionally stable lists across unrelated client requests without a snapshot/cursor; document this and consider a short-lived user/query-scoped snapshot or cursor for the strongest guarantee.
6. Keep AIO users' playback, Trakt and watch-state updates unchanged. No new privileged credential path. Avoid broad API changes to /UserItems/Resume.

## Acceptance and rollout gates

- Synthetic upstream tests: unchanged 0/20 and 20/20 queries return separate page slices; default start=0; test exactly 20/20 and 50/50, multi-page sparse candidates, source ID dedup, unplayed/caught-up skips, later empty page, unknown counts, user scoped access and stable ordering.
- Contract test of AIO container in isolation. Existing AIO NextUp first-page shape and client playback regressions must remain green.
- HomeDocker read-only qualification after a normal upstream AIO latest image update (do not force install): mapped user's /Users/Me, first-page byte capture, offset0 vs offset20 non-overlap and correct StartIndex/TotalRecordCount semantics, bounded double sweep if possible.
- Only after upstream API is qualified should Bridge RC3 consider a coherent normalized shelf/snapshot approach with scope=authenticated user, TTL, invalidation, predictable slice boundaries and strict fail-open on gaps. DO NOT implement a lookahead gateway over today's offset-ignoring AIO.
- R5 pilot remains HOLD. Retain RC2 Gateway OFF; no Odin/nginx changes.

## Alternative if upstream does not support public paging

Design a separate **complete bounded shelf** exposed through a documented AIO API or a user-scoped read-only data source. A Gateway-only proxy has no way to retrieve cards 51+ from the current 50-cap NextUp HTTP endpoint and cannot reconstruct a correct global total simply by inventing offsets. A limited 50-card preview could be made only as an explicitly separate, non-Jellyfin pagination feature; it must NOT masquerade as all NextUp pages.

## Suggested upstream issue scope (not filed automatically)

**Title:** Jellyfin /Shows/NextUp ignores StartIndex while reporting StartIndex=0 and prefix-only TotalRecordCount

**Reproduction:** With a Jellyfin user with >=40 eligible NextUp items, perform authenticated GET requests for StartIndex=0&Limit=20 and StartIndex=20&Limit=20. Both return the same first 20 items, 20/20 IDs overlapping, and the second response echoes StartIndex=0. User playback and watch-state remain untouched.

**Code pointer:** packages/server/src/routes/jellyfin/library.ts NextUp handler caps Limit 50 and calls send with startIndex=0, unlike /UserItems/Resume. The underlying recentSeries generator already iterates watch-state rows in SQL pages. Please implement stable NextUp paging with honest counts (including when the candidate scan is truncated).

No third-party issue or PR is created by this HomeDocker research without separate user approval.
