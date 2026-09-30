# Changelog

All notable changes to HomeDocker Trakt Bridge are documented here.

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
- Initial pulls and version mismatches always go to Trakt.
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
