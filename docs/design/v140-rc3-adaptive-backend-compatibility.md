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

## RC3-P5 — independently usable paged episode catalog (offline prototype + bounded host proof)

Official AIO source at pinned upstream 6ca32ed has **different** paging implementations:
- `/Shows/NextUp`: Limit capped 50, ignores StartIndex, returns fetched-prefix total. Not safe as a global paginated shelf.
- `/Shows/:seriesId/Episodes`: reads StartIndex and Limit (capped at 2000), slices a catalog-derived episode array and reports `TotalRecordCount=eps.length`. This is a *per-series catalog*, not a watched-history feed, and its data may change between requests.
- `/Items/:itemId`: resolves the actual ID supplied by the backend, not a fabricated cross-IMDb ID. A successful item GET proves item resolution, **not actual media playback availability**.

Added isolated `src/nextup-episode-catalog-binding.mjs`:
1. Require same authenticated user, verified SeriesId→IMDb, real Episode rows for precisely that SeriesId, contiguous StartIndex and stable exact TotalRecordCount, bounded 1..6 pages * up to 100 items. Incomplete or offset-ignoring responses fail closed.
2. Bind already-qualified canonical read-model history to existing catalog coordinates (Season/Episode) and return **source-provided IDs**. Incomplete joins fail closed; do not create missing episodes, overwrite their UserData or infer global NextUp counts.
3. Presently pure, offline only; no calls to Gateway/Trakt/AIO Docker and no production import.

Added `scripts/audit-v140-rc3-series-catalog-homedocker.py` (RC3-I): optional authenticated, GET-only HomeDocker proof using **one** canonical SeriesId discovered in the real stable 50-item NextUp window and Bridge's verified multi-spelling identity graph. It checks Series detail, bounded double-read `/Shows/{SeriesId}/Episodes?StartIndex=...&Limit=100` (up to four pages/400 episodes) and resolves the **existing** NextUp episode by one authenticated `/Items/{EpisodeId}` GET. Only numeric counts and pass/stop codes printed; no IDs, titles, progress, credentials, streaming tests or writes. It rejects incomplete catalogs and reports `FULL_NEXTUP_QUALIFIED=NO` regardless of success.

**Important limitation:** Catalog enumeration may demonstrate that the backend can resolve an existing NextUp card without upstream NextUp pagination. It is not proof that *other watched series* can all be discovered from a NextUp=50 prefix, or that missing catalog IDs can be reconstructed. Subsequent work requires a stable **source series index** spanning all relevant user-history series, authenticated mapping to playable catalog IDs, and a coherent user-scoped shelf/snapshot before any client-facing response change. Avoid relying solely on AIO container file paths for arbitrary Jellyfin or Remux installations.

## RC3-I2 STOP: transport problem before catalog, not an episode pagination verdict

2026-10-19 HomeDocker pinned-source RC3-I2 returned:
- `FAILURE_PHASE=READ_DOUBLE_NEXTUP`, `FAILURE_REASON=AIO_GET_UNAVAILABLE`; Gateway Filter was independently rechecked OFF and source changes=0.
- The shared GET reader used **6 seconds per call** and collapses network timeouts, URL errors and invalid JSON into that same reason. The first/second NextUp GET could not be distinguished from that report. **No episode catalog GET was reached**; the result does NOT reject `/Shows/{SeriesId}/Episodes` or episode binding.
- Earlier RC3-H successfully read the mapped user's NextUp=50 twice, so avoid attributing the new failure to pagination without transport evidence.

RC3-J isolated, bounded transport diagnostic `scripts/diagnose-v140-rc3-nextup-get-homedocker.py`:
- Same root-only Jellyfin session, exact /Users/Me match and Filter OFF gate; no querying a different user.
- One NextUp 50 GET at 6s, **one optional retry at 12s ONLY on timeout**. Returns a fixed error classification: `TIMEOUT`, `CONNECTION_REFUSED`, `NETWORK_UNREACHABLE`, `AUTH_REJECTED`, `HTTP_OTHER`, `JSON_INVALID`, `BODY_TOO_LARGE`, `TRANSPORT_OTHER` or `OK`, plus aggregate elapsed time and item count when available. No raw exceptions, titles, URLs, IDs or tokens.
- At most 3 total GET calls including /Users/Me, no metadata fanout, no catalog, Trakt, SQLite writes, Docker changes, Odin ingress or AIO configuration change. Recheck OFF after success/failure.
- DO NOT increase RC3-I global timeout, disable fail-open, or reroute client based on a single unstable GET. Await RC3-J evidence first; on sustained latency optimize the call/caching budget with source-specific controls and verify no accidental data loss.

## RC3-J actual operator measurement and RC3-I3 selective audit timeout

Live HomeDocker 2026-10-19 authenticated GET-only RC3-J:
- `/Users/Me` mapped successfully in **271 ms**.
- NextUp `Limit=50` at 6s: `TIMEOUT` after 6009 ms. The *one allowed* 12s retry succeeded with all **50** items in **11,377 ms**; method GET-only; Filter OFF, no writes.
- Thus the earlier RC3-I2 `READ_DOUBLE_NEXTUP/AIO_GET_UNAVAILABLE` is **consistent with its hard 6s per-call timeout being too short** under live AIO response load. Do not claim a lasting AIO outage, and do not infer that series catalog paging failed (not reached).
- The 11.377s measured latency is high enough to threaten interactive first-page UX. It is not a reason to turn on the production Filter or globally increase Gateway timeouts without an independently approved performance/canary plan.

RC3-I3 modifies **only the standalone host-side read-only catalog audit**:
- Bounded NextUp GETs use 18s/call for exactly two double-read snapshots under a 150s whole-audit deadline. Same root-only token, same local AIO base, disabled proxy/redirect and 1 MiB response bound; source remains read-only.
- All other GETs (`/Users/Me`, series metadata, per-series episode catalog and one `/Items/{EpisodeId}`) keep the existing **6s per-call** budget. Metadata concurrency remains four. This is NOT a Gateway runtime timeout change.
- On timeout/network/invalid JSON, emit a **fixed allowlisted** reason and phase without leaking token, ID, HTTP body, item metadata or URL. On STOP, separately verify Filter OFF.
- CI regression fixtures check long-timeout isolation, immutable 6s core GET budget, timeout classification, no response-body disclosure and existing catalog fail-closed conditions. Actual HomeDocker catalog qualification awaits a subsequent operator GET-only audit.

## RC3-I3 actual GET_EXISTING_EPISODE stop and RC3-I4 evidence separation

Operator-run live RC3-I3 on 2026-10-20:
- `RESULT=STOP_CATALOG_UNQUALIFIED`, `FAILURE_PHASE=GET_EXISTING_EPISODE`, `FAILURE_REASON=AIO_GET_UNAVAILABLE`; Filter OFF, no writes. This reader used the same generic 6s timeout. As in RC3-J, its error code alone does **not** prove timeout rather than transport, JSON or other GET failure.
- The script only reaches that phase after successfully passing: mapped user, two identical NextUp=50 reads, 50-card metadata lookup, verified single canonical candidate, **two fully validated paginated catalog reads with identical page results**, and the exact existing NextUp Episode ID present in that catalog. This proves one *single-series catalog-to-NextUp item correspondence* in the examined window; it is not a broad Jellyfin catalog or actual playable stream proof.
- The failure is the optional final **`GET /Items/{EpisodeId}`** detail probe, not NextUp pagination or catalog paging. Re-running 50 metadata requests solely to rediscover that is unnecessary without a targeted next diagnostic.

RC3-I4 source-only correction:
- Emits independent `RC3I_CATALOG_BINDING_GATE=VERIFIED_SINGLE_SERIES` only after both catalog reads replay unchanged **and** an exact existing NextUp Episode ID appears within the catalog. On a stop before that condition it emits `NOT_YET_VERIFIED`. Explicitly refuses to label `MEDIA_PLAYBACK_PROVEN` or `GLOBAL_NEXTUP_QUALIFIED`.
- The one authenticated `GET /Items/{EpisodeId}` now has a **separate 18s deadline** (no retry; request ID must be discovered from that same live NextUp and catalog). Static redacted result classifications distinguish timeout, invalid JSON, non-200 status and other transport errors.
- All other GET calls keep their established budget (18s *only* for NextUp, 6s for metadata/catalog), no production Gateway timeout change, no replay/watched writes, no Trakt writes, no downstream client changes. CI synthetic tests cover the independent evidence gate and error budget.
- Even if RC3-I4 fully passes, complete per-user history enumeration, binding *all* watched series and streaming availability remain unqualified; R5 production ingress and Gateway Filter stay HOLD.

## RC3-I4 PASS and RC3-K scoped episode-coordinate audit

HomeDocker operator RC3-I4 PASS (2026-10-20):
- Identity: mapped Jellyfin user, verified canonical-only entry in stable 50-card NextUp, and **50** series metadata lookups.
- Canonical series catalog: **12** episode objects in **one** page; two identical fully checked reads, exact existing NextUp Episode ID found in catalog; GET /Items/{EpisodeId} returned the matched episode using the dedicated 18s timeout.
- No writes, Filter OFF, no production Gateway changes. This proves **one** authenticated real episode catalog and one separately resolved item. It does NOT prove media PlaybackInfo, global NextUp paging, or a user-wide catalog.

Earlier RC3-F physical watch-state evidence: 11 canonical coordinate rows + eight alias coordinate rows, eight overlaps = **11 logical coordinate positions**. RC3-I4's catalog has 12 episodes; **do not infer that the extra 12th is unwatched** until there is an explicit season/episode coordinate join.

RC3-K source-only GET/SQLite audit: scripts/audit-v140-rc3-history-catalog-coordinates-homedocker.py
- Uses fixed mapped-user Jellyfin credential (root-only /run), verified Bridge graph and unique homedocker-trakt-bridge AIO sink. Reopens both DB files with SQLite mode=ro and PRAGMA query_only=ON.
- Reads watch-state exact group coordinates (season, episode, played, position), rejecting duplicates per spelling, incompatible played flags and multiple active resumes. Re-reads after authenticated stable NextUp and exact canonical per-series catalog double-read to reject data drift.
- **Matches logical history to catalog by exact Season/Episode coordinates only**, never ordinal position, titles, string similarity, or episode IndexNumber alone. Returns only aggregate counts of history found/missing in the catalog and catalog coordinates without an AIO history row, without exposing raw IDs, episodes, timestamps or titles.
- Scope is strictly **one verified cross-IMDb alias group**. An unmatched history coordinate leads to SCOPED_HISTORY_COORDINATES_ALL_BOUND=NO. This neither fabricates an episode nor constructs NextUp, modifies watched history, checks media streaming or qualifies client ingress. **A catalog episode without a history row is not proof it was never watched on Trakt or another client.**
- GET budgets match RC3-I4; bounded to 4 pages / 400 episodes and a 150s audit deadline. No playback endpoint, Docker/container restart or Trakt calls.

## RC3-K Live PASS and RC3-L pure scoped AIO history-to-catalog ID model

**2026-10-20 HomeDocker operator RC3-K real source evidence (GET-only / SQLite read-only):**
- Exact mapped user; 1 independently verified Bridge alias group; double-read catalog identical; independent watch_state double-read identical; canonical NextUp card bound to one real series.
- Physical rows: 11 canonical + 8 alias, overlap=8, alias-only=0. Distinct logical history coordinate count=**11**, catalog count=**12**; exactly **11/11** history coordinates found in the canonical catalog, 0 missing, 1 catalog-only coordinate; active resumes=0.
- `SCOPED_HISTORY_COORDINATES_ALL_BOUND=YES` with no database writes, Filter OFF, no route/client response changes. **Only one series**, not a global NextUp view. The 12th catalog-only coordinate has no evidence in this AIO sink history and must not be called never-watched or unplayed elsewhere.

**RC3-L offline source composition:** `src/nextup-scoped-aio-catalog-read-model.mjs` combines the previously isolated AIO source adapter, verified canonical progress projection and strict catalog pagination/binding. It requires caller-attested, same-user/exact-sink/graph/NextUp/catalog **stable snapshot gates**, and a verified canonical IMDb matching the resolved Series metadata. Only if every gate holds, it produces the internal immutable read-model containing **actual source-provided Episode IDs**, their exact Season/Episode coordinates, historical played/resume state and a count of catalog-only coordinates. No in-band calls to SQLite, Trakt, Jellyfin, Remux or Gateway; no Item synthesis or mutation.
- Synthetic regression fixture reproduces the **19 physical watch rows → 11 logical episodes → 11 bound IDs from a 12-episode real-shaped catalog**, plus conflict, alias active resume, missing coordinates, wrong series, unverified identity and missing provenance gates. Fixture tests demonstrate algorithmic behavior, not new live-data validation.
- Internal `episodeBindings` are never printed by the HomeDocker diagnostic and are **not** a public Jellyfin response. The current repository does not yet have an independently verified user-wide watched series index or playable stream contract. Accordingly, it still reports `nextUpShelfQualified=false`, `globalNextUpQualified=false`, `mediaPlaybackProven=false`.
- **Next action after code CI:** no need to rerun expensive RC3-K. Instead design an isolated, authenticated one-user read-only ingestion path that reuses validated source semantics, bounds and independently revalidates freshness, with a full series discovery plan; do not turn on the filter based on synthetic tests.

## RC3-M — full source series-key discovery (offline + read-only host audit)

Upstream AIOStreams pinned SHA 6ca32ed21e3036d87c35c49b24f2cf507d065ed1:
- Jellyfin /Shows/NextUp caps Limit to 50, ignores StartIndex and sends TotalRecordCount=visible items. In the general no-SeriesId branch, it uses provider.recentSeries(scope, Limit*4), then stops checking after at most 200 source candidates (NEXT_UP_SHOWS=200), and filters via watch drop times / match_key, caughtUp, metadata and playable next-up item logic.
- Core packages/core/src/db/repositories/watch-state.ts has a **paged source recentSeries generator**: GROUP BY series_key, MAX(sort_at) descending with lexical key tie-break, WHERE uuid+persona, series_key not null, episode not null, and (played=1 OR position_ms>0). The generator subsequently uses per-series anchor rows, match_key-based consolidation and dropped_at filtering before exposing candidate rows.
- **Hence distinct raw source candidate keys != NextUp cards**. The full-source ranked query is an inventory/discovery source only and does NOT reproduce the final generator's match-key/drop or metadata/caught-up logic. Ranking beyond position 200 proves records are present in the exact watch-sink, not that an item beyond NextUp 50 is playable or can be ordered identically.

Pure shadow-only src/nextup-scoped-series-discovery.mjs:
- Requires complete user/profile/sink/source-query evidence and bounded 10,000 raw keys. Separately models **all series_key values with episode-coordinate history** and **watched/resume recentSeries candidate keys**; candidate keys must be a unique ordered subset of historical keys. Zero-progress rows are retained in the former but not promoted into the latter.
- Accepts only independently verified graph alias spelling groups to build *non-playable grouping hints*; unrelated/opaque/unverified keys stay distinct. No titles/IMDb fuzzy joins, fabricated Jellyfin IDs, synthetic NextUp TotalRecordCount, route changes or user history mutation.
- Returns the internal source series inventory and ranked candidate counts, with globalNextUpQualified=false, nextUpShelfQualified=false and playableCatalogQualified=false under every result.

Bounded read-only host script scripts/audit-v140-rc3-full-series-inventory-homedocker.py:
- Verifies same mapped Jellyfin user using one local authenticated GET /Users/Me and Gateway Filter OFF before/after. Requires a single Bridge connected profile, an active exact AIO watch-sink and fresh verified identity graph. Reads BOTH AIO SQLite and Bridge SQLite using fixed paths mode=ro + PRAGMA query_only=ON, in read transactions. Captures two full snapshots; FAILS CLOSED on drift, unexpected schema, missing scope, invalid ranks or >10,000 source keys.
- Prints *only aggregates*: count all distinct historical series keys, ranked watched/resume candidates, history-only source keys, candidate rank bands 1-50/51-200/beyond 200, graph-verified alias grouping reduction hints, and source keys with IMDb-like vs opaque syntax. No source keys, ID strings, episode coordinates, timestamps, progress, bearer, titles or raw JSON are emitted.
- **Does not call** AIO NextUp, any catalog/episode endpoint, Trakt or a playback API; no writes, no restart, no client responses modified. Source candidate rank beyond 200 is not a global NextUp paging guarantee.
- This source is specific to AIO SQLite. Jellyfin native and Remux require independently validated history enumeration/identity contracts rather than assuming their Jellyfin-like APIs produce the same key set.

Operational gate: after Push and PR CI are BOTH green on one immutable commit, run the pinned root-owned host-only RC3-M audit once. Evaluate exact counts and source drift before designing a **separate** authenticated catalog resolver for missing series and complete user-facing pagination. Do not enable Filter or route Odin based solely on an inventory.

## RC3-M1 live STOP — exact AIO persona validation (2026-10-20)

Operator installed pinned source 0fce4a49a02cfab8b756756e474f64fcab4c9dbf. The first snapshot STOP reported `WATCH_SINK_NOT_UNIQUE` (Filter OFF, 0 production changes). **No history/recent series queries were run**: this stop is not evidence of missing series or flawed pagination.

Root cause hypothesis is a stricter validation newly introduced in RC3-M:
- Previous RC3-K/RC3-E explicitly accepted the unique active watch-sink's exact `(uuid, persona)` as long as both fields were strings. This can include a legitimate **empty-string persona**.
- RC3-M mistakenly required both strings to be nonempty, then collapsed both actual count mismatches and scope format failures into one `WATCH_SINK_NOT_UNIQUE` code. The output alone did **not** distinguish 0/multiple matching sink rows from a single sink with an empty persona; do not claim the persona is proven empty.
- RC3-M2 restores **exact prior string-type check** (do not substitute a nonempty value, never coalesce persona), separates `WATCH_SINK_NOT_UNIQUE` strictly for count != 1 and `WATCH_SINK_SCOPE_INVALID` for invalid columns, and keeps all SQL queries bound to the precise observed uuid/persona tuple.
- Regression tests create a pair of SQLite databases in-memory, set `PRAGMA query_only=ON`, insert one active watch-sink with `persona=""` and scoped watch history, then prove the full snapshot and candidate query include that exact scope without writing.
- Production Gateway Filter, RC2 and client routing remain unchanged. Run one new pinned read-only audit after both workflows pass. If the new code still reports `WATCH_SINK_NOT_UNIQUE`, it is an actual active-sink count mismatch and must be diagnosed without guessing a sink UUID or modifying watch_sinks.

## RC3-M2 live PASS and RC3-N1 namespace feasibility

HomeDocker RC3-M2 real read-only snapshot on 2026-10-20:
- Exact mapped Jellyfin user, two identical scoped SQLite snapshots, Gateway Filter OFF, zero production/history/client changes.
- **906** distinct source series keys containing episode rows, **861** raw watched/resume candidate keys, **45** history-only keys.
- Candidate source positions 1–50=50, 51–200=150 and **beyond position 200=661**. These positions are based on a raw SQL GROUP BY MAX(sort_at) listing, **not** cards emitted by AIO NextUp after match_key/drop/caught-up/metadata checks.
- **756** episode-history source keys match canonical IMDb syntax; **150** do not. The raw candidate subset has **711** canonical IMDb-form keys and **150** other-syntax keys. Only one Bridge verified multi-spelling group (17 graph show records total), giving a one-key reduction **hint**, not 905 canonical/unique playable shows.

Upstream IdParser (pinned SHA 6ca32ed21e3036d87c35c49b24f2cf507d065ed1) accepts several NON-IMDb identifiers, including TVDB, TMDB, MAL, Kitsu, AniList and anime-specific provider namespaces. **The 150 non-IMDb-form source keys cannot be called corrupt or unrelated history.** Their actual namespace distribution is not known until classification against the raw fixed-scope index; syntactic recognition is NOT proof of ID equivalence or media availability.

RC3-N1 (source-only follow-up, no catalog storm):
- `scripts/audit-v140-rc3-source-namespaces-homedocker.py` reuses the same strictly mapped user/unique AIO sink/verified profile and double-read read-only SQLite transaction from RC3-M2. It prints aggregate counts by known upstream identifier syntax: canonical IMDb, alternate IMDb, TVDB, TMDB, MAL, Kitsu, AniList, AniDB, other recognized anime formats and opaque/unknown keys.
- Output breaks down history key count, raw recent-candidate key count and candidate count beyond position 200 **per syntax class**; no source key/ID, title, timestamp, episode coordinate or watch progress is printed.
- Only one existing authenticated local GET /Users/Me; **zero** Jellyfin NextUp, /Items, /Shows/Episodes, PlaybackInfo or Trakt calls. No DB write, no local service restarts or Route/Gateway changes.
- All counters are checked against the independently verified 906/861/661-like RC3-M source totals **as read now**, never hard-coded to the previous run. Raw source-key syntax is a **resolver-planning aid**, not a source of cross-ID canonical mappings, playable Item IDs, global pagination or proof that NextUp contains all candidates.
- RC3-N2 can be designed only after the above real syntax distribution, then use a bounded representative, authenticated contract probe of source-specific catalog mapping rather than 861 network calls at once.

## RC3-N1 Live PASS -> RC3-N2 bounded anchor / drop source diagnostics (2026-10-20)

HomeDocker live read-only RC3-N1 source namespace classification:
- Double-read complete matched-user AIO scope identical; source counts **906 history series keys, 861 watched/resume raw candidate keys, 661 raw candidate ranks beyond 200**, zero writes and Filter OFF.
- Source namespace history/candidate/beyond200:
  - Canonical IMDb: **756 / 711 / 511**
  - Kitsu: **113 / 113 / 113**
  - TMDB: **24 / 24 / 24**
  - TVDB: **9 / 9 / 9**
  - Other/opaque: **4 / 4 / 4**
  - Other known IdParser families: zero in that audited source snapshot.
- All **150** noncanonical-IMDb candidate keys currently rank beyond 200; this **does not prove 150 valid playable series are missing**. Likewise, 661 raw candidates are NOT yet 661 distinct canonical shows or qualified NextUp card results. Source rank is sorted by MAX(sort_at); upstream NextUp applies match_key/drop, caught-up and full episode/metadata checks after enumeration.

Pinned upstream AIOStreams ID behavior relevant to safe resolver design:
- `packages/core/src/watch-state/types.ts` uses `seriesKeyOfMatch` only if `match_key` parses as an **episode** address; the source series destination may differ from the row's raw series_key. `recentSeries` uses a per-series anchor and filters effective drops before yielding.
- `packages/core/src/jellyfin/ids.ts` generates 32-hex Jellyfin descriptor IDs; some non-packed descriptors use a hashed mapping cached/persisted by the live AIO process. **Do not fabricate /Shows/{SeriesId}/Episodes IDs by concatenating `s|kitsu`, `s|tvdb` or the underlying source baseId**.
- A successful `GET /Items/{ExistingId}` qualifies only a real backend item that was already resolved by a supported authenticated source. It does not establish any reliable external SeriesId for all 661 beyond-rank candidates.

RC3-N2: **strictly no new Jellyfin item requests yet**. New `scripts/audit-v140-rc3-source-anchor-drop-homedocker.py` examines AIO source watch-state with a **bounded one-query-per-snapshot anchor** and bounded dropped flags. It reuses RC3-M2 exact mapped-user, unique AIO watch-sink/profile/graph and two fully consistent source snapshots. It re-reads anchors/drops twice, rejecting drift. All DB reads are SQLite mode=ro + PRAGMA query_only; there is only one authenticated /Users/Me GET. No NextUp, catalog, Items, Trakt, PlaybackInfo or POST traffic.
- Report only aggregate **TOP_200 versus BEYOND_200** counts: anchors with no match_key, parseable episode match_key, unrecognized key; syntactic source-to-match redirects, destination presence in observed source candidate keys and **potential** effective-drop signals. The parser intentionally handles a conservative subset of IdParser namespaces. Other syntax is left unknown; never invent a show identity.
- Ranks are the raw source GROUP BY position; this does not reproduce the complete paged upstream recentSeries order in presence of drops and canonicalized show keys. Episode anchor ties may not select the identical upstream row, so metrics are **diagnostic hints**, not a source-matching or fully qualified NextUp guarantee.
- Current caps: 10,000 candidate anchors and 10,000 scope-local dropped rows; no credentials/source keys, metadata, watch timestamps or private episode positions are emitted. Fail closed on invalid scope, source drift, unsafe drop timestamps, or count mismatches.
- After RC3-N2 live aggregate results, choose a **tiny capped sample** for further API contract tests only if each sampled source series has an independently proven existing Jellyfin SeriesId. No synthetic API IDs, no bulk 861 metadata requests, no production Gateway integration without explicit further authorization.

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
