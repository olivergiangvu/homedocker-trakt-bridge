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

## HomeDocker RC3-D/E findings and next source-independent path

- R4-D: 50/50 episode cards had 50 valid IMDb metadata lookups, yet only six cards were mapped to the verified Bridge graph. All six belonged to **different single-card groups**. Therefore a 50-window filter has no removable verified alias pair today; it is **not** a metadata outage or two-resume decision block.
- RC3-E: Bridge graph has 17 verified shows and only one verified canonical/alias group. AIO's exact configured sink has **11 canonical episode rows + 8 alias episode rows** for that group; both timelines exist but no dual active resumes were observed.
- This is an upstream watch-state split with incomplete **NextUp visibility evidence**. Do not infer these are 19 distinct episodes; do not infer they are duplicated on the current screen. The relevant questions are precise season/episode overlap, conflicting played state, and whether one spelling's show appears outside AIO's fixed 50-card window.
- RC3-F is the next read-only SQLite-only diagnostic: `scripts/audit-v140-rc3-alias-episode-overlap-homedocker.py`. It reuses verified graph and mapped AIO sink from RC3-E; reports only numeric counts of canonical-only/alias-only/overlapping season-episode coordinates, played disagreements and resumed overlap. It never changes history or routes clients.

### Backend-independent architectural separation

**Identity plane:** Verified Trakt show identities and exact IMDb aliases, with conflicts quarantined; do not rely on title similarity or backend string. 

**State plane:** For AIO on the same host, optionally read only mapped user/persona's watch_state SQLite; for native Jellyfin/Remux, use their own authenticated per-user API or supported read-only state export. Never open a different backend's DB using AIO schema assumptions.

**Catalog plane:** A source-specific resolver must supply a genuine playable episode Jellyfin ID and metadata. A raw watch-state row or an IMDb show ID is **not** a playable NextUp card. Do not invent a missing episode or fabricate SeriesId from IMDb.

**Presentation plane:** A user/query-scoped, internally consistent shelf is needed before rewriting any client first-page or later-page response. An upstream 50/50 prefix and its TotalRecordCount are NOT sufficient proof of a full shelf. Use byte-for-byte passthrough on uncertainty.

### RC3-F evidence (HomeDocker, 19 Oct 2026)

- One verified IMDb alias group has 11 canonical episode coordinates and eight alias episode coordinates. All **eight alias episodes overlap the canonical coordinates**, with three canonical-only episodes and zero alias-only. It is a strict subset in the *observed* 19 watch-state rows.
- Zero `played` disagreement on the eight common coordinates; zero overlapping active resumes; no invalid or duplicate coordinate rows. This is strong support for an **immutable canonical progress read-model** (logical union=11), NOT a destructive merge, not proof of duplicated NextUp cards or proof of playable Jellyfin items.
- Do not interpret 19 physical rows as 19 watched episodes. Keep local AIO watch_state and Trakt history unchanged. If future data introduces disagreements, multiple simultaneous resumes or ambiguous coordinates, quarantine that group rather than silently selecting a winner.

### RC3-G — last source candidate-routing diagnostic before implementing canonical read model

- AIO's official `recentSeries()` candidate ranking uses `(played = 1 OR position_ms > 0)`, groups by `series_key`, and sorts by `MAX(sort_at) DESC` plus `series_key`. Its HTTP NextUp handler scans up to 200 candidates but may skip shows due to drop, caught-up status, missing metadata, and de-duplicates by `SeriesId`.
- `scripts/audit-v140-rc3-nextup-candidate-rank-homedocker.py` reads the mapped sink and graph with SQLite query_only=ON. It reports only aggregate canonical vs alias **candidate-rank bands** (1–50, 51–200, beyond 200, absent), any effective series drop flags, and whether both spellings have *watched* candidate rows.
- The rank bands are **not actual UI positions**, since metadata resolution and match-key/drop handling can suppress candidates. No raw IDs or dates are printed; no tokens, HTTP calls or writes. AIO/Remux/native Jellyfin adapters will eventually supply independently verified state data to the same pure canonicalization engine.

### RC3-P3 pure Canonical Progress View now implemented (offline only)

- `src/nextup-canonical-progress-view.mjs` is a pure backend-agnostic logical state projector. The first AIO fixture is the proven RC3-F cohort: **11 canonical episode coordinates + 8 alias coordinates = 11 logical episodes**, with 8 overlapping coordinates. It cannot output a playable Jellyfin Item, a fabricated `TotalRecordCount`, or any write.
- A backend-specific adapter must supply exact same-user episode rows `{imdb,season,episode,played,positionMs}` with independently verified Trakt→IMDb aliases; never derive mapping from title similarity. The module rejects unknown IMDb spellings, duplicate coordinates, conflicting `played` values, two simultaneous active resumes, or ambiguous graph ownership.
- The original playback positions remain in the source rows. Where one spelling has a valid active resume and its counterpart is inactive, the view can preserve that position *as evidence of its actual spelling*, without migrating it to the other spelling.
- RC3-G drop diagnostics now emit booleans `SINGLE_GROUP_CANONICAL_DIRECT_DROP_EFFECTIVE` and `SINGLE_GROUP_ALIAS_DIRECT_DROP_EFFECTIVE`. They mirror only direct `series_key` drop flags. AIO may additionally suppress using a matched `showOf(row)` key, so these flags are **not** final card-presence proof.
- Runtime integration intentionally remains missing. Required to proceed: per-backend state adapter, verified authenticated user scope, catalog/playback ID resolver, stable presentation shelf/pagination and exact-path canary. A successful pure model must not be deployed as NextUp JSON.

**Gate:** This branch remains **shadow only**. Neither RC3-E nor RC3-F constitutes permission to alter Trakt history, AIO source, Gateway production Filter or Odin's route.

## RC3-H conclusive HomeDocker API observation (2026-10-19)

- RC3-H used the same mapped AIO Jellyfin user, two stable 50-item NextUp GETs and 50 per-series IMDb lookups. **Canonical cards=1; alias cards=0; groups both spellings in the window=0; canonical-only groups=1**. The Gateway Filter stayed OFF; no source or client change.
- RC3-G2 separately proved both spelling timelines were eligible for watch-state candidate ranking in 1..50, but only **alias** had an effective direct series-key drop flag. That is consistent with AIO suppressing the alias in this NextUp snapshot, yet is not an exact trace through match-key and metadata/caught-up filters.
- The old RC3-H output `UNVERIFIED_GRAPH_CARDS=49` was imprecisely named. The corrected metric is **`OUTSIDE_VERIFIED_ALIAS_GROUP_CARDS=49`**. These cards are outside the single *verified multi-spelling alias group*, not necessarily outside the entire Bridge graph. RC3-D previously counted six cards in the broader 17-show verified graph. All 50 per-series IMDb metadata lookups were valid.
- **Disposition for this mapped AIO account/window:** no current alias duplicate requiring NextUp card suppression. Do NOT enable filter merely to remove a card that isn't there. Source paging remains broken (StartIndex ignored; Limit cap 50); that limitation is independent of the alias that is presently hidden.

## RC3-P4 — source-independent history adapters (pure offline contracts, not connected to client)

`src/nextup-progress-source-adapters.mjs` now supplies normalized rows for the existing `buildCanonicalProgressView()` engine, with explicit contracts:

| Adapter | Required provenance | Conversion | Fail-closed cases |
| --- | --- | --- | --- |
| AIO watch_state SQLite | Exact, independently checked active watch_sink uuid+persona and connected Bridge profile; caller uses read-only DB connection | Strict `kind='episode'`, `series_key='s|tt...'`, season/episode, played=0/1 and nonnegative integer position_ms | Missing scope, invalid key/row, unverified IDs, over budget |
| Native Jellyfin episode *history*, NOT NextUp | Same authenticated user; independently established complete episode-history enumeration; same-user SeriesId→Series ProviderIds.Imdb verified | `UserData.Played` and exact Jellyfin tick to millisecond conversion; preserves source episode coordinates | Partial history, missing Series metadata, malformed response, repeated Ids, sub-millisecond timebase ambiguity |
| Remux Jellyfin-compatible history | All native checks **plus independent Remux contract validation**; do not assume its API merely because it resembles Jellyfin | Same strict mapping only after provenance test | Missing native contract proof; differences in Remux index/ID semantics |

All three are **pure**, bounded, shadow-only: no production import, no Trakt writes, no synthetic playable item IDs, no Client NextUp pagination count and no routing. Synthetic regression tests verify AIO 11+8→11 logical episodes, equivalent Jellyfin history, Remux contract guard, active-resume conflicts and byte-for-byte untouched input.

**Next engineering milestone:** separately qualify a backend-provided *complete, user-scoped history source* and a *playable catalog resolver*, then construct a consistent shelf/cursor and exact one-client opt-in test. Nothing in RC3-H or the pure adapters proves user-wide Jellyfin paging or authorizes exposing port 7101.

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
