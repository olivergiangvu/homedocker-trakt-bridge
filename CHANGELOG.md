# Changelog

All notable changes to HomeDocker Trakt Bridge are documented here.

## [1.3.0-rc.3] - 2026-10-05

### Fixed
- Extends the opt-in AIO false-`unplayed` guard to a second reproduced Jellyfin UserData shape where `Played=false` is followed by `PlaybackPositionTicks=0` in the same request.
- This zero-position composite can temporarily leave AIO at `played=0, position_ms=0` while also stamping `last_played_at`; RC2 intentionally failed open because it only trusted a contemporaneous positive resume row.
- RC3 suppresses that zero-position echo only when the exact delivery is matched, the local row is contemporaneous, duration matches, and `last_played_at` was also stamped in the same tight window. A standalone explicit Mark Unwatched does not update `last_played_at` and therefore still fails open to the normal history-remove path.
- The existing RC2 positive-resume guard remains unchanged and still requires an equivalent delivered HomeDocker playback event.

### HomeDocker evidence
- Playback echo capture: `unplayed` at 12:52:20.893 left `position_ms=0`, `played=0`, `updated_at=12:52:20.910`, and `last_played_at=12:52:20.910`; positive playback resumed shortly afterward.
- Controlled explicit Mark Unwatched capture: local state changed to `position_ms=0, played=0` at 12:55:03.120 while `last_played_at` remained at the earlier 12:54:51.678 value.
- AIOStreams source confirms the distinction: explicit `unplayed` clears played/position without touching `lastPlayedAt`, while a `stop` write always stamps `lastPlayedAt`, including a zero-position stop.

### Safety
- `AIO_UNPLAYED_ECHO_GUARD=false` remains the public default.
- The guard still requires the exact HomeDocker sink and exact AIO delivery identity.
- Ambiguous/missing/stale evidence fails open to the existing Trakt history-remove path.
- AIO SQLite remains read-only; DB schema and compare-only reconciler semantics remain unchanged.

## [1.3.0-rc.2] - 2026-10-05

### Fixed
- Adds an opt-in, fail-open AIO false-`unplayed` guard for a reproduced Jellyfin UserData echo where the client reports `Played=false` while the same request leaves a positive resume position.
- Suppression requires the exact AIO delivery event, the configured HomeDocker sink, a contemporaneous local `played=0` positive resume row, and an equivalent **delivered** HomeDocker playback event.
- Explicit Mark Unwatched remains untouched because a real clear-history transition leaves no positive resume row; bulk marks are excluded.
- Guard failures or ambiguous AIO evidence preserve the existing Trakt `/sync/history/remove` path.

### Canary evidence
- Stable AIOStreams 2.35.9 and exact nightly `2026.10.04.1829-nightly` both reproduced the same false-`unplayed` pattern.
- Nightly canary item `e|tt14261112:2:6` retained local resume `398930/1740000` while AIO queued `unplayed positionMs=0 played=false`; the preceding HomeDocker stop at `398928ms` had already been delivered.
- AIO's Jellyfin UserData route processes `Played=false` before `PlaybackPositionTicks`, explaining why the outbound queue can contradict the final local row.

### Safety
- `AIO_UNPLAYED_ECHO_GUARD=false` remains the public default.
- AIO SQLite remains read-only and no database migration is added.
- v1.3 compare-only semantics remain unchanged; RC2 does not add Phase B recovery writes.

## [1.3.0-rc.1] - 2026-10-05

### Added
- Adds opt-in `AIO_RECONCILER_MODE=compare` as the first v1.3 recovery phase.
- Keeps the fully qualified v1.2 detect settlement path unchanged, then stages only settled missing-delivery candidates into a separate restart-safe Trakt comparison queue.
- Compares candidates against authenticated Trakt playback using GET endpoints only and classifies `trakt_same_or_newer`, `trakt_stale_candidate`, `trakt_playback_missing_candidate`, or fail-closed ambiguous outcomes.
- Gives newer native-Trakt timestamps precedence, while equivalent/ahead Trakt progress also suppresses recovery candidacy.
- Keeps Trakt read failures/rate limits retryable with bounded backoff instead of converting them into terminal recovery decisions.
- Adds persisted aggregate compare diagnostics to operational status.

### Safety
- Compare mode never writes Trakt and never writes AIOStreams.
- AIO SQLite remains read-only.
- Existing v1.2 sink matching, position-aware delivery coverage, quiet-window settlement, first-seen preservation, same-item coalescing, playback watermarks, history dedupe, and rate-limit lanes remain unchanged.
- Public default remains `AIO_RECONCILER_MODE=off`.
- Phase B guarded writeback is explicitly excluded from this RC.

### Qualification target
- RC1 is intended for HomeDocker compare-only canary observation.
- Production stable remains the exact v1.2.0 digest until RC1 compare decisions are observed and accepted.

## [1.2.0] - 2026-10-05

### Stable release
- Promotes the fully qualified `v1.2.0-rc.7` HomeDocker canary to stable without adding new production behavior after the accepted rc.7 logic.
- Keeps direct provider-ID playback/history transport, source playback watermarks, family-scoped ambiguous write cooldowns, v1.1 semantic history dedupe, and native-Trakt coexistence safeguards.
- Adds the optional read-only AIO local-state reconciler as a stable v1.2 capability while keeping `AIO_RECONCILER_MODE=off` as the public default.

### HomeDocker qualification
- The accepted rc.7 canary ran the exact immutable image `sha256:2fc1725aae1c57c86f428c94cd018d5767327628f52c03610600eeee563b18c3`, whose release workflow also passed exact-published-digest health/readiness smoke testing.
- Position-aware coverage, mismatched-position rejection, pending/error exclusion, 300-second settlement, restart-safe pending persistence, same-item coalescing, preserved `firstSeenAt`, exactly-once settlement and detect-only semantics all passed.
- The final restart qualification settled as `covered_by_homedocker_playback_delivery` with `candidate=false` and `writesTrakt=false`.
- `scripts/qualify-aio-restart-persistence.sh` codifies the restart/coalescing qualification and keeps coverage outcome independent from restart-persistence acceptance.

### Compatibility and safety
- DB schema remains `1`; no database migration or pull-cache purge is required from v1.1.0.
- The optional AIO reconciler never writes Trakt or AIOStreams in `detect` mode and remains disabled by default.
- Immediate rollback is the qualified rc.7 exact digest; `v1.1.0` remains the prior stable fallback line.

## [1.2.0-rc.7] - 2026-10-05

### Fixed
- Detect-only AIO coverage now correlates the final local resume row with **delivered** HomeDocker playback events by playback position instead of relying on a narrow +/-15 second timestamp window.
- A valid HomeDocker start/pause/stop can cover a later generic UserData row when its positive `positionMs` is within 2000 ms of the final AIO position and was delivered within the bounded 180-second lookback.
- Pending or errored delivery rows never count as coverage, and materially different playback positions remain missing.

### Canary evidence
- VidHub delivered a HomeDocker stop at 651281 ms for `tt36885662:1:3`; AIO later refreshed the local row to 651269 ms about 65.16 seconds later while also emitting a zero-position `unplayed`.
- rc.6 incorrectly classified that final row as missing because it searched only +/-15 seconds around the latest AIO timestamp.
- The captured stop and final local row differ by only 12 ms, providing a deterministic regression fixture for rc.7.

### Runtime qualification
- The annotated `v1.2.0-rc.7` tag resolves to commit `c77794c5e7cc9ef2d26db1b39212e758a87efd80`; the successful release workflow published and exact-digest smoke-tested `sha256:2fc1725aae1c57c86f428c94cd018d5767327628f52c03610600eeee563b18c3`, which is the same immutable image used by the HomeDocker canary.
- Restart-while-pending passed in the live canary: pending `firstSeenAt=1791142679441` existed before Bridge restart `1791142696673`, survived with the same first-seen watermark, and settled only after restart.
- A newer same-item local AIO row coalesced from `updatedAt=1791142684785` to `1791142976796` while preserving `firstSeenAt`; this is expected behavior, not qualification contamination.
- The chain settled exactly once at `1791143282000` as `covered_by_homedocker_playback_delivery`, with `candidate=false` and `writesTrakt=false`.
- Restart persistence and delivery coverage are treated as independent assertions. The reusable operator harness is `scripts/qualify-aio-restart-persistence.sh`.

### Safety
- rc.7 retains rc.6's 300-second per-item quiescence and exact HomeDocker sink matching.
- The reconciler remains detect-only and never calls/writes Trakt or writes AIOStreams.
- AIO access remains read-only and DB schema remains `1`.
- rc.4 transport/rate-limit behavior remains unchanged.

## [1.2.0-rc.6] - 2026-10-05

### Changed
- Detect-only AIO reconciliation now coalesces local resume updates per item and waits for a 300-second quiet window before emitting a settled classification.
- Playback-delivery coverage is now scoped to the configured HomeDocker AIO sink rather than any sink. The sink can be pinned with `AIO_RECONCILE_SINK_INSTANCE_ID`.
- Reconciler persistence advances to the `aio-reconcile:v2:*` namespace so the first rc.6 run establishes a fresh baseline instead of replaying rc.5 observations.

### Canary evidence
- rc.5 observed a Strand/UserData intermediate AIO row at 362934 ms (06:02) followed about 180.4 seconds later by a final row at 536771 ms (08:56).
- A direct Trakt read matched the final 536771 ms state, while AIO `watch_sessions` contained no session for the client and the HomeDocker sink had only `unplayed` deliveries with zero position.
- This proves a 30-second or 180-second age gate is insufficient to infer final playback state for this client path.

### Safety
- rc.6 remains observation-only: the reconciler never calls or writes Trakt and never writes the AIOStreams database.
- The AIO mount remains read-only.
- If the configured HomeDocker sink is missing or ambiguous, settlement is blocked rather than guessed.
- Pending candidates survive Bridge restart; newer same-item local state replaces older pending state and resets the quiet timer.
- DB schema remains `1`; rc.4 transport/rate-limit behavior remains unchanged.

## [1.2.0-rc.5] - 2026-10-05

### Added
- Optional Bridge-side AIO local-state reconciler in detect-only mode. It reads the AIOStreams SQLite database through a read-only connection and identifies local unfinished resume-state updates that lack a nearby AIO playback delivery.
- Restart-safe AIO detection cursor and per-row watermark stored in the existing Bridge `media_cache`; no database migration is required.
- `AIO_RECONCILER_MODE`, `AIO_DB_PATH`, `AIO_RECONCILE_INTERVAL_SECONDS`, `AIO_RECONCILE_GRACE_SECONDS`, and `AIO_RECONCILE_MAX_ROWS` configuration controls.

### Evidence
- Strand advanced AIO local state and native Trakt to 443904 ms (07:23), while the Bridge received only redundant `unplayed` deliveries and no `start`/`pause`/`stop` for that playback.
- VidHub then resumed at exactly 07:23, confirming native-Trakt coexistence works when the client commits its own Trakt state.
- AIOStreams 2.35.9 source audit showed the Jellyfin UserData path records `PlaybackPositionTicks` locally as a stop but does not dispatch that stop to watch-state addons.

### Safety
- The reconciler is disabled by default.
- Detect mode never writes the AIOStreams database, never calls Trakt, and never synthesizes playback.
- The first enabled run establishes a baseline instead of replaying historical AIO rows.
- Exactly one connected Bridge profile is required; ambiguous multi-profile setups fail safe.
- rc.4 direct transport, rate-limit lanes, semantic history dedupe, playback watermarks, pull behavior and native-Trakt coexistence are unchanged.
- DB schema remains `1`.


## [1.2.0-rc.4] - 2026-10-04

### Fixed
- Headerless or unnamed Trakt `429` responses are now isolated by authenticated write family instead of poisoning every POST. `/sync/history*`, `/scrobble/*`, and `/sync/watchlist*` keep independent cooldowns while authenticated reads remain separate.
- A headerless `/sync/history/remove` throttle can no longer block latency-sensitive playback `start`, `pause`, or `stop` scrobbles. This directly addresses the rc.3 HomeDocker canary where a redundant AIOStreams `unplayed` event armed the old global write cooldown and delayed the real 19.86% stop.
- Explicit Trakt `AUTHED_API_POST_LIMIT` metadata remains authoritative and still cools every authenticated write family. Unknown named/shared limits remain conservative shared cooldowns.
- Rate-limit persistence advances to `rate-limit:v3:*`. A still-active v2 write cooldown is conservatively honored once as an all-write legacy window, then new observations use family-specific v3 state.

### Canary evidence
- rc.3 removed public resolver requests from the playback hot path successfully: the control stop for `tt36885662:1:3` reached Trakt through `direct-provider-ids` and returned HTTP 201 with `action=pause`.
- The subsequent AIOStreams `unplayed` payload carried `Played=false`, position 0 and the same provider IDs, then `/sync/history/remove` returned headerless 429 with Retry-After. Under rc.3 this armed `LOCAL_WRITE_COOLDOWN`, causing later pause/start/stop events to be rejected locally.
- A newer stop watermark still prevented the older retried start from overtaking it, confirming the stale-playback guard remained effective during the failure.

### Safety
- The global authenticated write queue and 1100 ms pacing remain unchanged; only cooldown scope is split.
- Explicit Mark Unplayed is not heuristically suppressed. AIOStreams' generic UserData update and explicit PlayedItems-delete paths can produce indistinguishable `unplayed` payloads, so rc.4 preserves semantic correctness instead of guessing user intent.
- DB schema remains `1`; pull identity, pull cache, semantic history dedupe, direct provider-ID transport and AIOStreams scheduler settings are unchanged.
- Native Trakt remains enabled during qualification.

## [1.2.0-rc.3] - 2026-10-04

### Changed
- Playback, history, bulk-history and watchlist writes now use Trakt-compatible provider IDs directly on the normal hot path instead of blocking on public metadata resolution.
- Episode scrobbles follow the original Odin transport shape: parent show IDs plus season/episode numbering.
- Successful scrobble responses can teach canonical Trakt IDs and show aliases opportunistically without adding a pre-write lookup.
- Unfinished AIOStreams stops below Trakt's completion threshold use `/scrobble/stop`; the 80-89% gap remains mapped to pause when AIOStreams still reports `played=false`, preserving AIOStreams' 90% watched threshold.

### Fixed
- Removes the public `/search/*` and `/shows/*/seasons/*/episodes/*` resolver fan-out from normal playback writes, eliminating the rc.1/rc.2 resolver-429 critical path.
- Headerless/unnamed 429 responses now cool only the request lane that actually failed. A failed write no longer pre-emptively blocks recovery GETs, while an independently failing read still arms its own cooldown.
- Semantic history dedupe can use stable AIOStreams source identity when canonical Trakt IDs are not available before the write, retaining v1.1 duplicate suppression without reintroducing resolver calls.
- Accepted Trakt stop responses, including current `action=stop`, legacy `action=scrobble`, and accepted 409 duplicates, seed completion dedupe so the following explicit `played` echo does not add a second Bridge history row.

### Audit evidence
- Original Odin Trakt Bridge reproduced AIOStreams redundant `unplayed` events yet successfully resumed one VidHub -> Strand handoff at 544787 ms -> 544786 ms, showing the AIO noise alone is not the root cause.
- Repeated Odin testing still showed intermittent Strand -> VidHub drift and duplicate history, so rc.3 borrows Odin's lean transport while retaining HomeDocker stale-event and semantic-dedupe safeguards.
- AIOStreams 2.35.9 host audit confirmed active sinks are scheduled for background pull at ~1800 seconds by default; the bridge's manifest pull hint does not replace AIOStreams' own scheduler cadence.
- The same audit captured repeated `played` deliveries and redundant `unplayed` deliveries reaching Odin on first attempt, validating the need to retain HomeDocker semantic dedupe and to address external/native-writer freshness separately.

### Safety
- Source playback watermark remains first in the playback path, so an older queued retry cannot overtake a newer event even if the newer Trakt write fails.
- DB schema remains `1`; no migration or cache purge is required.
- `PULL_IDENTITY_MODE=trakt`, `HISTORY_DEDUPE_SECONDS=300`, and current HomeDocker pull cache settings remain unchanged for the rc.3-A transport canary.
- AIOStreams `WATCH_STATE_*` settings remain unchanged during rc.3-A qualification. Activity-aware pull scheduling is deferred until the lean transport canary passes.
- Native Trakt remains enabled on supported clients during qualification.

## [1.2.0-rc.2] - 2026-10-04

### Fixed
- A source-identity playback watermark is now recorded before Trakt media resolution, so a newer AIOStreams playback event that itself fails during metadata resolution still prevents an older retry from overtaking it.
- Public Trakt metadata lookups now honor a persisted local `Retry-After` cooldown after a 429 instead of repeatedly re-hitting the same resolver path during the throttle window.
- Resolver caches are normalized across provider-ID subsets. Show/movie results are indexed by provider aliases and episodes are keyed by canonical Trakt show + season + episode, preventing the same item from being re-resolved when one event carries full IMDb/TMDb/TVDb IDs and another carries only IMDb.
- Existing pre-v1.2 resolver cache rows are promoted lazily into the normalized cache on first use, avoiding a forced cache purge.

### Canary evidence
- rc.1 captured a real VidHub pause at 11.84% that reached AIOStreams correctly but failed in Bridge before scrobbling because the public episode resolver returned `429` on `/shows/285217/seasons/1/episodes/2` with `Retry-After: 65` and no `X-Ratelimit` metadata.
- Because rc.1's canonical watermark was recorded only after successful media resolution, that resolver failure left a stale-retry race open. rc.2 closes that gap before the resolver runs.

### Safety
- The existing canonical Trakt-item watermark remains in place after successful resolution.
- Event ordering still uses event time, never progress, so legitimate newer backwards seeks remain valid.
- The public metadata cooldown is separate from authenticated read/write cooldowns; a public lookup 429 does not unnecessarily freeze authenticated pull or mutation lanes.
- DB schema remains `1`; no migration is required.
- Pause-422 recovery remains deferred until a real 422 reason is captured in canary diagnostics.

## [1.2.0-rc.1] - 2026-10-04

### Added
- Persistent per-profile, per-canonical-Trakt-item playback watermarks for `start`, `pause`, and `stop` transitions.
- Structured, sanitized upstream Trakt diagnostics for 422/429 responses, including endpoint, `Retry-After`, parsed `X-Ratelimit` metadata, and bounded upstream error detail.
- Separate read, write, and shared/security rate-limit cooldown lanes.

### Fixed
- Older AIOStreams playback retries can no longer overtake a newer playback transition and rewind Trakt resume state after a 429/422 delay.
- Playback ordering is based on event time rather than progress, so legitimate newer backwards seeks remain valid.
- A confirmed Trakt `AUTHED_API_POST_LIMIT` cooldown no longer unnecessarily blocks GET-based pull convergence.

### Safety
- Unknown or headerless 429 responses remain conservative shared cooldowns that block both reads and writes.
- The playback watermark is persisted in the existing SQLite cache and is advanced before the upstream write, so restart or a failed newer write cannot reopen the stale-retry race.
- Equal event timestamps are allowed because AIOStreams timestamps have one-second resolution and legitimate transition edges may share a second.
- v1.1 semantic history dedupe and legitimate rewatch behavior are unchanged.
- DB schema remains `1`; no migration is required.
- Pause-422 recovery is intentionally not included in rc.1. The candidate first captures the real sanitized Trakt response so recovery can be designed from evidence rather than guesswork.

### HomeDocker canary
- Native Trakt remains enabled on supported clients such as VidHub and Strand.
- Acceptance focuses on stale-retry prevention, rate-limit lane isolation, diagnostic quality, and eventual state convergence under shared account-level Trakt pressure.

## [1.1.0] - 2026-10-04

### Stable release
- Promotes the accepted `v1.1.0-rc.1` duplicate-history canary to stable after HomeDocker production qualification.
- Live canary evidence confirmed restart-safe semantic dedupe for same-state events with different AIOStreams event IDs, while preserving legitimate later rewatches.
- `v1.0.0` remains the deterministic rollback line; DB schema stays at `1`.

### Canary qualification
- A repeated `unplayed` event for the same canonical episode arrived with a different event ID 118 seconds later and was correctly ignored as `recent_history_equivalent`.
- A normal `played` event produced one `history:add`.
- A real rewatch roughly 34 minutes later, outside the 300-second semantic window, produced a new `history:add` and Trakt history increased by exactly one play.
- Runtime remained ready with schema `1`, one connected profile, zero container restarts and no canary errors.
- The completed `scrobble/stop -> played` overlap is covered by automated regression tests; the HomeDocker client path used during qualification emitted explicit `played` without a completion `stop`.

### Fixed
- Adds canonical Trakt-ID semantic dedupe for repeated single-item `played` / `unplayed` events that arrive with different AIOStreams event IDs.
- Records a short-lived watched marker after Trakt confirms `/scrobble/stop` with `action=scrobble`, preventing a following explicit `played` event from adding the same viewing to `/sync/history` a second time.
- Keeps opposite state transitions valid, so `played -> unplayed -> played` is never collapsed into one state.

### Added
- `HISTORY_DEDUPE_SECONDS`, default `300`, backed by the existing restart-safe SQLite cache. Set it to `0` only for diagnostics.
- Regression tests for stop-to-played duplication, repeated played marks, opposite-state transitions, separate media and expiry outside the dedupe window.

### Safety
- Dedupe keys use the resolved canonical Trakt movie/episode ID rather than raw IMDb/TMDb spelling, so provider aliases do not create parallel dedupe identities.
- A Trakt stop is treated as watched only when Trakt itself returns `action=scrobble`; stops that Trakt classifies as `pause` do not suppress a later explicit played mark.
- No DB schema migration, pull-state representation change or pull-cache namespace change is required.
- This guard only deduplicates the Bridge write lane. A separate native Trakt client remains an independent writer and must be evaluated separately if duplicates remain during canary testing.

## [1.0.0] - 2026-10-04

### Stable release
- Promotes the accepted v0.9.2 production candidate to the first stable release line after a 45.78-hour HomeDocker burn-in.
- Keeps Trakt as the canonical watched/resume authority while AIOStreams remains the Jellyfin-compatible playback/state surface.
- Keeps `PULL_IDENTITY_MODE=trakt` as the recommended coexistence mode for clients that also use native Trakt.
- Keeps conservative public freshness defaults at `300s`; HomeDocker's measured `60s` profile remains deployment-specific tuning rather than a universal default.
- Keeps AIOStreams Watch State runtime settings at upstream defaults; no additional `WATCH_STATE_*` overrides are required for the v1.0 baseline.

### Reliability qualification
- Exact published v0.9.2 digest completed extended production burn-in with schema `1`, one connected profile, healthy OAuth state and no reconnect requirement.
- Final audit recorded 134 successful pulls, including 73 authoritative pulls, with watched/watchlist state still populated.
- AIOStreams → Bridge handoff finished with 477/477 retained deliveries in a final delivered state and no pending/non-final rows.
- Trakt `429` responses were contained by profile-wide cooldown, Retry-After handling and AIOStreams retry scheduling without a current backlog.
- Four historical `unplayed` errors initially flagged as unresolved were each superseded by a newer same-item `played` event and did not represent lost final state.
- The production dual-IMDb regression remained clean and the duplicate Continue Watching/identity symptom remained absent through burn-in.
- Long-run sampled runtime settled around `41 MiB` memory with `0%` idle CPU and no sustained growth in the sampled window.

### Known non-blocking observation
- Duplicate Trakt play-count/history entries can still occur when AIOStreams generates repeated semantic `played` marks for the same item at different timestamps.
- AIOMetadata 3.3.2 is not a Trakt watch-tracking writer in the qualified HomeDocker topology.
- This remains tracked in issue #40 for post-v1.0 investigation; v1.0 intentionally does not add heuristic Bridge-side dedupe that could suppress legitimate rewatches.

### Compatibility and safety
- DB schema remains `1`.
- AIOStreams pull representation remains `watch-state-v0.3.6`.
- Pull cache namespace remains `pull-state:v5:*`.
- No database migration or cache purge is required from v0.9.2.
- The accepted v0.9.2 immutable digest remains the deterministic rollback target for the v1.0.0 production canary.

### Upgrade
- Back up `bridge.db`, `.env`, Compose and reverse-proxy configuration.
- Pull the public GHCR `1.0.0` tag or, preferably, pin the immutable digest published by the release workflow.
- Recreate the container without deleting the data volume.
- Verify `/health`, `/readiness`, connected profile state, authoritative watched/watchlist sync and the running image digest.
- For the final HomeDocker v1.0 acceptance, verify rollback to the accepted v0.9.2 exact digest and return to the v1.0.0 exact digest.

## [0.9.2] - 2026-10-02

### Added
- Public GHCR release workflow with semver/latest/SHA tags, SBOM and provenance metadata.
- Exact-published-digest smoke testing for `/health`, `/readiness`, application version and DB schema before a release is accepted.
- Split Bridge freshness controls: `PULL_CACHE_TTL_SECONDS` and `PULL_HINT_SECONDS`, with legacy `PULL_TTL_SECONDS` fallback compatibility.
- Profile-wide Trakt rate-limit cooldown shared by reads and writes, persisted through the existing SQLite cache and honored across container restarts.
- Per-profile authenticated Trakt write pacing and improved operator diagnostics for upstream endpoint and `Retry-After` values.
- Public-facing documentation structure, contribution templates, issue forms, CI/release/license badges and pre-public Git-history audit tooling.

### Changed
- Production runtime image is reduced to a minimal Alpine runtime with a stripped Node executable and BusyBox healthcheck; the accepted image budget is `<= 122 MiB`.
- Successful playback pause invalidates the Bridge pull cache for faster convergence.
- Public freshness defaults are intentionally conservative at `300s`; HomeDocker accepted a measured optimized profile at `60s` without new upstream `429` during staged `300 -> 120 -> 60` canaries.
- AIOStreams Watch State runtime settings remain at upstream defaults for the v1.0 baseline; no extra `WATCH_STATE_*` environment overrides are required.
- Production guidance now prefers published GHCR release tags or immutable digests instead of local source builds.

### Safety
- DB schema remains `1`.
- AIOStreams pull representation remains `watch-state-v0.3.6` and pull cache namespace remains `pull-state:v5:*`.
- `PULL_IDENTITY_MODE=trakt` remains the recommended mode when native Trakt and AIOStreams are used in parallel.
- Any Trakt `429` protects both read and write lanes for the same profile until the Retry-After window expires.
- The exact published v0.9.2 digest passed release-workflow smoke testing and HomeDocker production cutover/restart acceptance with the dual-IMDb regression still clean.
- Duplicate Trakt play-count entries observed with overlapping playback writers are tracked as a non-blocking post-v1.0 investigation rather than addressed with unsafe dedupe heuristics in this release.

### Upgrade
- Back up `bridge.db`, `.env`, compose and reverse-proxy configuration.
- Pull the public GHCR image and preferably pin a release tag or immutable digest for production.
- No DB migration or cache purge is required from v0.5.0.
- Verify `/health`, `/readiness`, connected profile state, watched/resume behavior and the running image reference after recreation and one restart.
- For deterministic rollback, record the previous image tag/digest before upgrading.

## [0.5.0] - 2026-10-01

### Added
- Local HTTP integration harness that exercises the real Bridge, Trakt client and SQLite paths without external-network test dependencies.
- End-to-end coverage for Trakt `401 -> token refresh -> retry`, `429` retry metadata, pagination, duplicate delivery and restart-safe processed-event idempotency.
- Recovery-matrix coverage for OAuth `invalid_grant`, provider-ID `404` fallback, Trakt `420` / `422` classification, pagination safety caps, bounded stale fallback on `429` / `5xx`, stale-window expiry, concurrent pull coalescing and SQLite backup/restore.
- Authoritative-pull integration coverage for the production dual-IMDb regression fixture (`tt44051354` vs `tt44094505`) across playback, watched episodes, next-up and watchlist.
- Managed Trakt client behavior that clears unusable local OAuth credentials when Trakt rejects a refresh grant with `invalid_grant`.
- Auth state transitions in the existing event log using stable event ID `auth|state`.
- `/status` profile diagnostics `connectionState` and `reconnectRequired`.
- Dedicated v0.5.0 release-candidate / HomeDocker canary checklist.

### Changed
- `invalid_grant` is now a credential-state transition instead of a repeatedly retried connected state: tokens are cleared, readiness becomes `setup_required`, and the operator must reconnect Trakt.
- Successful token storage records the auth state back to `connected`; manual disconnect records `disconnected` separately from `reconnect_required`.
- CI smoke tests derive the expected application version and DB schema from source instead of hard-coding v0.4.0/schema 1.
- App/package/example image/User-Agent and operator documentation are synchronized to v0.5.0.

### Safety
- DB schema remains `1`; no migration is required from v0.4.0.
- AIOStreams pull representation remains `watch-state-v0.3.6` and persisted pull cache remains `pull-state:v5:*`.
- No AIOStreams or Bridge cache purge is required for v0.4.0 -> v0.5.0.
- `PULL_IDENTITY_MODE=trakt` remains the HomeDocker recommendation for native Trakt + AIOStreams coexistence.
- Authentication failures never use stale pull cache as a substitute for valid credentials.
- The production Trakt grant does not need to be intentionally revoked for canary validation; invalid-grant recovery is covered by integration tests.

### Upgrade
- Take a normal pre-upgrade backup of `bridge.db`, `.env`, compose and nginx configuration.
- Build/recreate the v0.5.0 container; no DB migration or cache cleanup is required.
- Verify `/health` reports `0.5.0`, `/readiness` is `ready=true`, DB schema remains `1`, and `/status` reports `profile.connectionState=connected`.
- Run the HomeDocker canary checklist in `docs/releases/v0.5.0.md` before tagging the release.
- Rollback to v0.4.0 can reuse schema-1 `bridge.db` because v0.5.0 introduces no DB schema change.

## [0.4.0] - 2026-10-01

### Added
- Explicit SQLite migration framework using `PRAGMA user_version`; v0.3.x databases migrate in place from schema `0` to schema `1` without deleting profiles, OAuth tokens, processed events, learned aliases, event history or pull cache.
- Fail-closed protection when the database schema is newer than the running binary.
- `GET /readiness` for operational readiness: database query, supported schema and at least one connected Trakt profile.
- Authenticated `GET /status?key=<ADMIN_KEY>` operator diagnostics.
- Operator dashboard with connection health, prominent AIOStreams manifest copy action, sync health, active-error status and concise recent-event summaries.
- Recent-event filters: All, Errors, Pull, Playback and Ignored, with raw payloads collapsed behind `Raw`.
- CI container build plus `/health` and `/readiness` smoke tests on every push/PR.
- Tag-based release workflow that publishes an amd64 GHCR image and creates a GitHub Release after checks/tests pass.
- Task-oriented documentation: Setup, Operations, Architecture, Integrations, Troubleshooting and Development.

### Changed
- App/package/example image/User-Agent are synchronized to v0.4.0.
- The manifest URL is promoted to a first-class dashboard action with a dedicated Copy button.
- The dashboard separates the latest poll from the last authoritative watched/watchlist sync.
- Operational health uses a recent active-error window; old historical errors remain available without keeping the dashboard in `Attention` indefinitely.
- Low-level identity-alias evidence is removed from the normal operator UI and remains available through `/status`, the database and developer documentation.
- Runtime startup logs include the active DB schema and applied migration summary.
- Graceful shutdown closes SQLite explicitly.
- Canonical HomeDocker authority model is documented as: Trakt history authority, AIOStreams Jellyfin state surface, AIOMetadata metadata + secondary-tracker write/fan-out.
- Recommended AIOMetadata Jellyfin `Trackers` setting is formally documented as `This server only` when Trakt Bridge is the canonical history source.

### Safety
- `/health` remains liveness-only and does not depend on external network state.
- `/readiness` checks token/connection state without making a live Trakt API call, avoiding rate-limit and latency coupling.
- `/status` never exposes OAuth tokens, bridge secrets or setup/addon credentials.
- v0.4.0 intentionally keeps the v0.3.6/v0.3.7 AIOStreams pull representation (`watch-state-v0.3.6`) and `pull-state:v5:*` cache namespace.
- No AIOStreams pull-cache purge is required for v0.3.7 -> v0.4.0.
- `PULL_IDENTITY_MODE=trakt` remains the HomeDocker production recommendation for native-Trakt + AIOStreams coexistence.

### Upgrade
- Take a pre-upgrade backup of `bridge.db`, `.env`, compose and nginx configuration.
- First v0.4.0 startup upgrades Bridge DB schema `0 -> 1` in place.
- After upgrade, `/readiness` should return HTTP `200` with `schemaVersion=1` and at least one connected profile.
- Rollback to v0.3.7 should restore the matching pre-v0.4.0 DB backup as well as the older source/image.

## [0.3.7] - 2026-10-01

### Fixed
- Prevents deterministic Trakt `422` retry loops when AIOStreams emits `start`, `pause`, or unfinished `stop` events below 1% playback progress.
- Sub-1% scrobble events are acknowledged locally and marked processed without calling Trakt.
- Explicit `played=true` stops below 1% fall back to Trakt history add so an explicit watched state is not discarded because of inconsistent progress metadata.
- Setup UI version/capability labels remain dynamic through `APP_VERSION` and display the active `PULL_IDENTITY_MODE`.

### Safety
- Exactly 1% progress remains eligible for normal scrobbling; only values below 1% are filtered.
- Unknown duration keeps the existing fail-safe behavior and is not treated as zero progress.
- Ignored sub-1% events cannot teach or change an IMDb alias because no upstream scrobble succeeded.
- The v0.3.6 pull identity behavior, `watch-state-v0.3.6` schema salt, pull cache namespace, OAuth/profile data, and learned aliases are unchanged.

### Upgrade
- No database migration or pull-cache purge is required when upgrading from v0.3.6.
- Recommended HomeDocker setting remains `PULL_IDENTITY_MODE=trakt` when native Trakt and AIOStreams are used in parallel.

## [0.3.6] - 2026-10-01

### Added
- `PULL_IDENTITY_MODE=trakt|aiostreams`.
- Default `trakt` mode preserves the IMDb spelling returned by Trakt on Trakt → AIOStreams pulls.
- Optional `aiostreams` mode retains the v0.3.5 learned-alias rewrite behavior.
- Regression coverage for the production dual-IMDb case: Trakt `tt44051354` vs AIOStreams-learned `tt44094505` for the same stable show.
- Pull identity mode participates in the watch-state version basis.

### Changed
- Pull-state schema advances to `watch-state-v0.3.6` so a representation-mode change forces a fresh authoritative state.
- Learned IMDb aliases remain persisted and continue to be learned from successful AIOStreams episode `stop` events in both modes.
- In `trakt` mode, learned aliases remain available for diagnostics/evidence but no longer rewrite Trakt pull rows.
- Package/image/User-Agent/example configuration are synchronized to v0.3.6.

### Fixed
- Restores compatibility with clients such as Strand that use native Trakt and AIOStreams watch-state simultaneously. v0.3.4/v0.3.5 could expose two valid IMDb spellings for the same title because native Trakt preserved Trakt IMDb while the bridge rewrote AIOStreams pull state to a learned AIOStreams IMDb.
- Restores v0.3.3-style Trakt pull identity behavior without rolling back v0.3.4/v0.3.5 alias learning, bulk dedupe, provider fallback, cache safety or unfinished-stop handling.

### Upgrade
- Production upgrades from v0.3.5 should remove persisted `pull-state:v5:*` rows once before the first v0.3.6 authoritative pull.
- Do **not** delete profile tokens, `identity-alias:v1:*`, event history or unrelated cache rows.
- Recommended HomeDocker setting: `PULL_IDENTITY_MODE=trakt`.

## [0.3.5] - 2026-09-30

### Fixed
- Alias learning now keys off the successful AIOStreams `stop` event rather than the translated Trakt scrobble action. An unfinished AIOStreams stop is intentionally sent to Trakt as `/scrobble/pause`, and v0.3.4 therefore missed the exact production case that exposed the dual-IMDb duplicate.
- When a successful stop learns or changes an IMDb alias, the bridge invalidates its pull cache immediately even if the Trakt action was `pause`, so the next pull can return the normalized identity without waiting for the cache TTL.

### Safety
- Plain `pause` events still cannot teach or override an alias.
- Alias learning still occurs only after the upstream Trakt scrobble request succeeds.
- The learned alias model, state cursor schema, and `pull-state:v5:` namespace remain unchanged from v0.3.4.

## [0.3.4] - 2026-09-30

### Added
- Persistent, profile-scoped show identity aliases keyed by the stable Trakt show ID.
- Successful episode `stop` events can teach the bridge which IMDb spelling AIOStreams actually used for playback when Trakt returns a different IMDb alias for the same show.
- Pull-side rewriting for episode playback, watched-show history and show watchlist rows so the learned AIOStreams IMDb spelling is returned consistently.
- Alias state participates in the watch-state version cursor, so learning a new preferred spelling forces a fresh authoritative representation even when Trakt watched/watchlist activity itself did not change.
- Unit coverage for alias persistence, no-op relearning, same-Trakt-show rewrite, successful-stop learning and pull round-trip normalization.

### Changed
- State cursor schema advances to `watch-state-v0.3.4`.
- Persisted pull-cache namespace advances from `pull-state:v4:` to `pull-state:v5:` so a pre-alias cache cannot survive the upgrade boundary.
- App/package/image/User-Agent and setup UI are synchronized to v0.3.4.

### Safety
- No show ID is hard-coded and no additional per-show Trakt API fan-out is introduced.
- Aliases are learned only from a successful episode playback `stop`, not from `played`, `unplayed`, bulk history or watchlist mutations.
- Learning requires a valid IMDb `metaId` and a resolved Trakt show ID; non-IMDb identities remain untouched.
- Rewriting changes only the IMDb spelling. Trakt, TMDb and TVDb IDs remain intact.
- Shows without a learned alias keep v0.3.3 behavior.

## [0.3.3] - 2026-09-30

### Added
- Shared IMDb/TMDb/TVDb normalization for push and pull identity handling.
- `metaId` fallback for push events whose shared `ids` object is absent or incomplete.
- Provider-alias failover: a Trakt 404 for one known spelling can fall through to another known provider ID instead of dropping the event immediately.
- Alias-aware watched `counts`, keyed under every representable show ID Trakt returned, as allowed by the AIOStreams Watch State v2 contract.
- Safe `watched.nextUp` forwarding when the upstream Trakt watched row itself contains a usable `next_episode`.
- Pull diagnostics now include `watchedNextUp`.
- Unit coverage for ID parsing/normalization, metaId fallback, alias failover, alias-aware counts, and next-up forwarding.

### Changed
- The state cursor includes a v0.3.3 schema salt so AIOStreams receives the new authoritative watched representation after upgrade even when Trakt activity itself did not change.
- Persisted pull-cache namespace advances from `pull-state:v3:` to `pull-state:v4:` so a still-fresh v0.3.2 cache cannot postpone the migration.
- Setup UI, app/package/image/User-Agent, README, architecture and upstream notes are synchronized to v0.3.3.

### Safety
- Explicit valid event `ids` remain authoritative; `metaId` only fills missing provider IDs.
- Only Trakt 404 alias misses are swallowed during provider failover. `429`, auth errors and `5xx` still propagate normally.
- The bridge does not invent next-episode numbering and does not add a per-show Trakt progress fan-out, avoiding another rate-limit hotspot on large histories.
- Trakt-only IDs without a representable IMDb/TMDb/TVDb spelling are still skipped rather than exposed as invented AIOStreams IDs.
- Anime/absolute-number mapping remains fail-closed.

## [0.3.2] - 2026-09-30

### Added
- Restart-safe short-lived coverage markers for every video included in a successful AIOStreams bulk `played` / `unplayed` event.
- `BULK_SINGLE_DEDUPE_SECONDS` with a default of 300 seconds.
- Diagnostics for suppressed duplicate episode echoes, including the covering bulk event ID, video ID and event-time delta.
- Unit tests covering same-kind suppression, opposite-state safety, time bounds and movie exclusion.

### Fixed
- Jellyfin-compatible clients can issue new per-episode marks after the same season/show change was already delivered as one supported bulk event. Those echoes previously caused redundant Trakt history writes and could create duplicate watched-history entries.
- Same-kind single episode events covered by a recent successful bulk mark now return `204` without touching Trakt.

### Safety
- Dedupe is scoped by profile + event kind + exact video ID.
- A single event is suppressible only when its own event timestamp is at or after the covering bulk timestamp and within the configured dedupe window.
- Opposite-state events are never suppressed, movie marks are never suppressed, and later same-kind events outside the window are processed normally.
- Coverage markers are written only after the bulk Trakt mutation succeeds and are persisted with TTL in `bridge.db`, so a restart cannot reopen the duplicate window.
- Pull, watchlist, scrobble and authoritative-state behavior are unchanged.

## [0.3.1] - 2026-09-30

### Added
- AIOStreams `watch_state` v2 whole-season and whole-series `played` / `unplayed` bulk marks.
- Manifest now advertises `watchState.push.bulk=true`.
- One AIOStreams bulk part (up to 500 listed videos) is converted into one Trakt `/sync/history` or `/sync/history/remove` request.
- Bulk requests preserve AIOStreams' explicit video list by grouping only those season/episode numbers under the resolved Trakt show; the bridge never marks an entire show implicitly.
- Validation for bulk scope, `metaId`, `part` / `parts`, video count, season consistency and per-video numbering.
- Unit coverage for manifest bulk capability, validation, multi-season grouping, deduplication, add/remove payloads and fail-closed anime numbering.

### Changed
- Successful bulk history changes invalidate the pull cache, so the next pull refreshes authoritative watched/watchlist state.
- Setup UI, app/package/image/User-Agent, README, architecture and upstream notes are synchronized to v0.3.1.

### Safety
- Bulk history uses the Trakt show's nested `seasons[].episodes[]` form and writes only the videos AIOStreams says changed.
- More than 500 videos remain split by AIOStreams into independent idempotent parts; the bridge handles one part per request.
- Anime/absolute-number video IDs remain fail-closed rather than guessing a Trakt season/episode mapping.
- `start`, `pause`, `stop`, watchlist push and all v0.3.0 pull/cache behavior are unchanged.

## [0.3.0] - 2026-09-30

### Added
- Bidirectional movie/show watchlist sync between AIOStreams and Trakt.
- AIOStreams push events `watchlisted` and `unwatchlisted`.
- Trakt watchlist pull mapping into AIOStreams `watchlist[]`.
- Combined watched + watchlist activity cursor so watchlist-only changes advance the same `version` / `since` gate.
- Movie/show watchlist provider-ID resolution and Trakt sync writes.
- Unit tests for watchlist manifest capabilities, pull mapping, cursor changes and Trakt add/remove payloads.

### Changed
- Manifest now advertises `pull.watchlist=true` and the two watchlist push events.
- Fresh authoritative pulls fetch supported movie/show watchlist state together with watched state.
- Meaningful watched/watchlist push mutations invalidate the safe pull cache so Trakt auto-removals and new activity converge on the next pull.
- v0.3.0 uses a versioned persistent pull-cache namespace (`pull-state:v3:`), so a v0.2.x SQLite cache cannot delay the first watchlist-aware authoritative pull after upgrade.
- Ignored playback events no longer resolve media before returning, avoiding unnecessary upstream lookups.
- Trakt watchlist-limit response `420` is treated as non-retryable bridge `422` instead of a retrying 5xx.
- App/package/image/User-Agent, setup UI, README, architecture and upstream notes are synchronized to v0.3.0.

### Safety
- `watched` and `watchlist` are returned atomically only after the complete changed authoritative state was read successfully.
- Cached/stale responses still contain only `version + items`, never authoritative watched/watchlist state.
- AIOStreams watchlist pushes are limited to movie/show scope; episode/season favourites are not invented.
- Trakt assigns list timestamps, so the bridge does not claim to preserve the AIOStreams event timestamp when adding to Trakt.
- Old v0.2.x persisted pull-cache rows are ignored by v0.3.0 and expire naturally under their existing SQLite TTL.
- Bulk played/unplayed marks, dropped state and anime absolute-number mapping remain unadvertised.

## [0.2.3] - 2026-09-30

### Added
- Restart-safe persistence for the matching-version pull cache using the existing SQLite `media_cache` store.
- Cache diagnostics now identify whether a hit came from the in-memory hot copy or from SQLite after a restart.
- Regression test proving the persisted cache survives a database reopen and can be cleared explicitly.

### Changed
- Disconnecting a Trakt profile clears both the in-memory and persisted pull cache.
- Invalid JSON cache rows are deleted instead of being reparsed on every lookup.
- App/package/image/User-Agent, README, architecture and upstream notes are synchronized to v0.2.3.

### Safety
- Persistence does not change pull authority: cached entries still contain only `version + items` and never authoritative `watched` state.
- SQLite retention is bounded by `PULL_STALE_IF_ERROR_SECONDS`; `fetchedAt` is still checked independently before fresh or stale cache use.
- Initial pulls and version mismatches still contact Trakt.

## [0.2.2] - 2026-09-30

### Added
- Bridge-side memory cache for matching-version pull requests.
- In-flight pull coalescing so concurrent requests for the same profile/cursor share one Trakt read.
- Safe stale-cache fallback for transient Trakt `429` / `5xx` responses.
- `PULL_STALE_IF_ERROR_SECONDS` with a default of 3600 seconds.
- Pull diagnostics now identify `trakt`, `coalesced`, `cache`, and `stale-cache` sources.
- Tests covering cache TTL, version matching and stale-cache bounds.

### Changed
- `PULL_TTL_SECONDS` now defaults to 900 seconds and is used by the bridge itself in addition to being advertised in the addon manifest.
- Example image tag, User-Agent, README, architecture and upstream notes are synchronized to v0.2.2.

### Safety
- Cached responses are only served when the caller's `since` exactly matches the cached version.
- Cache responses contain `version + items` only and never synthesize authoritative `watched` state.
- Initial pulls and version mismatches always contact Trakt.
- Stale fallback is bounded and is not used for authentication/reconnect errors.

## [0.2.1] - 2026-09-30

### Fixed
- Repeated successful pull polls that reuse the same `since` cursor are no longer grouped together and shown as misleading `2 attempts`, `4 attempts`, etc. in Recent Events.
- Push retry grouping remains unchanged: stable AIOStreams event IDs still collapse retries into one row and show `recovered` after a later success.

### Changed
- Release metadata, example image tag, example User-Agent, README and architecture notes were synchronized to v0.2.1.

## [0.2.0] - 2026-09-30

### Added
- AIOStreams `watch_state` v2 pull endpoint.
- Trakt → AIOStreams Continue Watching import from `/sync/playback/movies` and `/sync/playback/episodes`.
- Trakt → AIOStreams authoritative watched history import.
- Safe Trakt pagination using `X-Pagination-Page-Count`.
- `since` / `version` gate so unchanged watched history is omitted while playback progress is still refreshed.
- Runtime-based `positionMs` generation when Trakt returns runtime metadata.
- Pull diagnostics in the profile UI.
- Pull safety controls `PULL_TTL_SECONDS` and `PULL_MAX_PAGES`.
- Tests for pull mapping, watched-version behavior, and incomplete-history fail-closed behavior.

### Safety
- A transient or incomplete Trakt watched response fails the pull instead of returning an empty authoritative `watched` block.
- Watchlist, dropped state, bulk marks and anime absolute-number mapping remain unadvertised.

## [0.1.1] - 2026-09-30

### Added
- `Asia/Ho_Chi_Minh` diagnostics timezone support.
- Retry/recovery grouping by AIOStreams event ID.
- Trakt endpoint and `Retry-After` diagnostics for upstream failures.
- Shortened event IDs in the setup UI.

### Fixed
- Recent Events no longer displays container UTC as if it were local time.
- Recovered 429 events are shown as recovered rather than a permanent failure.

## [0.1.0] - 2026-09-30

### Added
- Trakt OAuth 2 Authorization Code flow with encrypted tokens.
- AIOStreams `watch_state` v2 push support for `start`, `pause`, `stop`, `played`, and `unplayed`.
- Idempotent event handling and SQLite diagnostics.
- Trakt movie/show/episode ID resolution and cache.
- AIOStreams 90% vs Trakt 80% watched-threshold guard.
- Docker deployment and host-nginx pattern.
