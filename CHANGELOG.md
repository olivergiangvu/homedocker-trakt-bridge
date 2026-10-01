# Changelog

All notable changes to HomeDocker Trakt Bridge are documented here.

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
