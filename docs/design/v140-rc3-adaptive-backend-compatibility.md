# RC3-P2 — HomeDocker-owned backend-adaptive NextUp compatibility (without upstream patches)

**Status:** Prototype engine + synthetic CI acceptance. NOT deployed, NOT used by the live Gateway. No upstream fork, Trakt write, container change, nginx change or Odin ingress. RC2 remains running with Identity Filter OFF.

## Why this branch exists

On 2026-10-19 the HomeDocker mapped-user AIO NextUp test returned 20/20 on both StartIndex=0 and StartIndex=20; both responses carried StartIndex=0, and the 20 episode IDs matched exactly. The official AIO source at commit 6ca32ed reads Limit but not StartIndex in its /Shows/NextUp handler and always sends StartIndex=0. The source caps Limit at 50 and returns fetched-prefix TotalRecordCount. Native Jellyfin and Remux may obey entirely different NextUp contracts.

Waiting indefinitely for upstream changes is not acceptable. The Gateway must own **capability assessment**, **bounded first-page alias evidence** and **safe degradation**, rather than hard-code AIO assumptions into all backends.

## Three backend capability paths

| Observed backend | Adaptation path | Coverage | Unsupported/unsafe |
| --- | --- | --- | --- |
| Jellyfin-compatible source with disjoint page 0/20 and matching StartIndex echoes | OFFSET_HONORED_OBSERVED -> possible full-list cursor/snapshot qualification, separate later milestone | Back-end request-level pagination; still need stable snapshot, user scope and true counts | Treating one offset test as transactionally stable |
| AIO-like source ignoring StartIndex and capping Limit at 50 | OFFSET_IGNORED_OBSERVED -> bounded-window approach from one 50-card response, double-read and verify exact prefix against original client page | Opportunity to de-duplicate/refill the **visible first 20** without requesting an impossible second offset | Claiming globally correct TotalRecordCount, synthesizing cards beyond 50, pagination >50 |
| Backend with absent StartIndex, inaccurate counts, drifting pages or API/auth failure | OFFSET_PLAUSIBLE_NO_ECHO / UNQUALIFIED -> preserve original upstream response exactly | Current NextUp UI remains functional as provided by backend | Suppressing progress or metadata based on titles/weak IDs |

All paths require same-user /Users/Me proof, exact verified Trakt show->IMDb aliases, a bounded authenticated series-detail API and the existing two-active-resume guard. Never treat movie/Resume items as NextUp candidates. All metadata reads use the same Jellyfin session token and never Bridge OAuth credentials.

## Implemented in this prototype

src/nextup-adaptive-window.mjs exports:

1. classifyNextUpPagination({first,second,limit}): shape-checked, non-mutating observational classifier. It distinguishes repeated first page (AIO), disjoint pages with matching offsets (Jellyfin), missing offset echo and unsupported/overlapping pages. It **does not** claim cross-request snapshot consistency.
2. assessAdaptiveFirstPage({original,windowA,windowB,clientLimit,windowLimit,identityVerified,verifiedGraph,resolveSeriesImdb}):
   - Supports a client first page <=49, and backend window <=50, requested with **same mapped user**.
   - Requires two identical backend window responses, exact byte-object prefix correspondence between the 50-page and the original page 20, valid items, unique episode IDs and sensible count types.
   - Runs existing verified-IMDb and playback-state normalization on the *window* in memory (not only the first 20), checking ability to refill to 20 after a verified suppression.
   - Reports **only aggregate, privacy-safe numeric candidate evidence**; never outputs a replacement Jellyfin JSON or changes a live response.
   - A 50/50 full window is classified BOUNDED_PREFIX_ONLY and may show a **first-page candidate**, but neither a complete list nor a trustworthy total; it always reports clientResponseChanged=false and globalPaginationQualified=false.
   - Under wrong user, missing metadata, ambiguous playback state, window drift, duplicate item IDs or insufficient coverage, falls back without suppression.

## What would make it a safe *usable* feature

**Gate A (implemented, CI):** Pure source-agnostic adapter and synthetic tests for 20/20, 50/50, AIO offset-ignored, native-offset, wrong users, partial metadata, two active resumes, replay drift and no injected watch writes. It is deliberately not linked into the Gateway.

**Gate B (next, HomeDocker authenticated shadow test):** Using the current mapped Jellyfin user, bounded GET /Users/Me, NextUp Limit20 and two NextUp Limit50 snapshots with EXACT SAME auth headers. Fetch up to 50 Series details via GET /Items/{SeriesId} only if the exact window is unchanged. Consult Bridge's verified identity graph via truly SQLite read-only access. Report counts and candidate removes, no IDs or secrets. With no matching verified alias in the first 50, no transformation should be enabled.

**Gate C (separate runtime PR and operator authorization):**
- Mode default OFF; explicit single-client **first-page-only** canary opt-in under exact StartIndex=0&Limit=20 ingress, never a blanket Jellyfin proxy.
- Maintain one Bridge/one writer, filter ON separately; first-page candidate only with a stable 50 window, matching user and verified playback evidence.
- Explicit policy for TotalRecordCount and later pages. Do not fabricate global total. For a multi-page client a stable user+query-scoped, expiring snapshot (and real source of cards after the bound) is required; unknown backend capabilities fail open.
- Never replace full-library pagination with a fixed 50-card fake total, nor silently return empty later pages.
- Bound extra upstream reads, metadata concurrency, per-token caching, latencies, memory and network failure responses. Preserve byte-perfect OFF parity and keep Resume/playback routes untouched.
- Test with one controlled Odin client, verify first-page render and no lost item, then evaluate other Jellyfin clients independently. Rollback by turning Filter OFF and reverting exact ingress.

**Absolute information boundary:** If a backend refuses to reveal any items beyond card 50 (and has no alternative authenticated index), no Gateway can reconstruct accurate item 51+ or guarantee full-library pagination. A true generic solution must use a capability-specific second source: correct native paging API, authenticated watch-state + catalog details, or an explicit user-scoped read-only local source adapter. Any such adapter must separately validate identities and avoid writing AIO/Trakt.

## Release separation

RC3-P2 engine only. Do not merge into RC2 stable/tag, upgrade the running Docker image, set Gateway ON, change nginx, route Odin or expose 7101 on the WAN because synthetic CI passes. Existing R5 and stable release HOLD remain active.
