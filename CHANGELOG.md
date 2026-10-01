# Changelog

All notable changes to HomeDocker Trakt Bridge are documented here.

## [0.3.6] - 2026-10-01

### Added
- `PULL_IDENTITY_MODE=trakt|aiostreams`.
- Default `trakt` mode preserves the IMDb spelling returned by Trakt on Trakt → AIOStreams pulls.
- Optional `aiostreams` mode retains the v0.3.5 learned-alias rewrite behavior.
- Regression coverage for the production dual-IMDb case: Trakt `tt44051354` vs AIOStreams-learned `tt44094505` for the same stable show.
- Pull identity mode participates in the watch-state version basis.

### Changed
- Pull-state schema advances to `watch-state-v0.3.6` so changing pull identity representation forces a fresh authoritative state.
- Learned IMDb aliases remain persisted and continue to be learned from successful AIOStreams episode `stop` events in both modes.
- In `trakt` mode, learned alias revision is retained for diagnostics/persistence but does not rewrite Trakt pull rows.
- Package/image/User-Agent/example configuration are synchronized to v0.3.6.

### Fixed
- Restores compatibility with clients such as Strand that use native Trakt and AIOStreams watch-state simultaneously. v0.3.4/v0.3.5 could expose two valid IMDb spellings for the same title because native Trakt preserved Trakt IMDb while the bridge rewrote the AIOStreams pull to a learned AIOStreams IMDb.
- Restores v0.3.3-style Trakt pull identity behavior without rolling back v0.3.4/v0.3.5 alias learning, bulk dedupe, provider fallback, cache safety or unfinished-stop handling.

### Upgrade
- Production upgrades from v0.3.5 should remove persisted `pull-state:v5:*` rows once before the first v0.3.6 authoritative pull.
- Do **not** delete profile tokens, `identity-alias:v1:*`, event history or other `media_cache` records.
- Recommended HomeDocker setting: `PULL_IDENTITY_MODE=trakt`.

## [0.3.5] - 2026-09-30

### Fixed
- Alias learning keys off the successful AIOStreams `stop` event rather than the translated Trakt scrobble action.
- An unfinished AIOStreams stop may safely map to `/scrobble/pause` and still teach the playback-confirmed IMDb alias.
- A changed alias immediately invalidates the pull cache.

### Safety
- Plain `pause` events cannot teach or override an alias.
- Alias learning occurs only after the upstream Trakt scrobble succeeds.

## [0.3.4] - 2026-09-30

### Added
- Persistent, profile-scoped show identity aliases keyed by stable Trakt show ID.
- Successful episode `stop` events can teach the IMDb spelling AIOStreams used for playback.
- Pull-side rewriting of episode playback, watched-show and show-watchlist rows to the learned AIOStreams IMDb.
- Alias-aware state-version migration and `pull-state:v5:` cache namespace.

### Safety
- No show ID is hard-coded.
- Rewriting changes only IMDb spelling; Trakt, TMDb and TVDb IDs remain intact.

## [0.3.3] - 2026-09-30

### Added
- Shared IMDb/TMDb/TVDb normalization for push and pull identity handling.
- `metaId` fallback when shared IDs are absent/incomplete.
- Provider-alias failover after Trakt 404.
- Alias-aware watched counts under every representable show ID.
- Safe exact `watched.nextUp` forwarding when supplied by Trakt.
- `pull-state:v4:` migration namespace.

## [0.3.2] - 2026-09-30

### Added
- Restart-safe short-lived coverage markers for successful bulk played/unplayed events.
- `BULK_SINGLE_DEDUPE_SECONDS`.
- Suppression of same-kind per-episode echoes already covered by a recent successful bulk mark.

## [0.3.1] - 2026-09-30

### Added
- Whole-season/show AIOStreams bulk played/unplayed support.
- Nested Trakt history requests using only explicit AIOStreams `videos[]`.
- Validation and fail-closed anime numbering.

## [0.3.0] - 2026-09-30

### Added
- Bidirectional movie/show watchlist sync.
- Combined watched + watchlist state cursor.
- Authoritative watchlist pull.
- Versioned persisted pull cache migration.

## [0.2.3] - 2026-09-30

### Added
- Restart-safe persisted pull cache in SQLite.
- Cache source diagnostics for memory vs SQLite.

## [0.2.2] - 2026-09-30

### Added
- Matching-version pull cache.
- In-flight pull coalescing.
- Bounded stale-cache fallback for transient Trakt `429` / `5xx`.

## [0.2.1] - 2026-09-30

### Fixed
- Pull diagnostics no longer group independent successful polls as retries.

## [0.2.0] - 2026-09-30

### Added
- Trakt → AIOStreams Continue Watching and authoritative watched-history pull.
- Safe pagination and `since` / `version` gating.

## [0.1.1] - 2026-09-30

### Added
- Diagnostics timezone, retry/recovery grouping and upstream error details.

## [0.1.0] - 2026-09-30

### Added
- Trakt OAuth 2 flow with encrypted tokens.
- AIOStreams watch-state push for playback and watched state.
- Idempotent event handling, SQLite diagnostics and threshold safety.
