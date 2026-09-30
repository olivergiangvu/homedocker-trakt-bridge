# Changelog

All notable changes to HomeDocker Trakt Bridge are documented here.

## [0.3.1] - 2026-09-30

### Added
- AIOStreams Watch State v2 bulk `played` / `unplayed` support for whole-season and whole-series marks.
- Manifest now advertises `watchState.push.bulk=true`.
- One Trakt `/sync/history` or `/sync/history/remove` request per AIOStreams bulk part instead of one request per episode.
- Bulk diagnostics include scope, changed-video count and AIOStreams `part` / `parts` metadata.
- Tests for bulk contract validation, nested Trakt payloads, duplicate episode collapse and anime fail-closed behavior.

### Changed
- Bulk history writes resolve the show once, group only the supplied `videos[]` by season, and use Trakt's nested show → season → episode sync shape.
- Successful bulk played/unplayed marks reuse the existing history cache invalidation path so the next pull sees the new authoritative state.
- App/package/image/User-Agent, README and protocol notes are synchronized to v0.3.1.

### Safety
- A series bulk mark never sends a bare show object to Trakt; only episodes explicitly listed by AIOStreams are changed.
- Each bulk request is capped at the protocol maximum of 500 videos and validates season/episode numbers before any Trakt write.
- Season-scoped marks reject videos from another season.
- Kitsu/MAL/AniList/AniDB-spaced bulk episode IDs remain unsupported because absolute/anime numbering is not mapped safely to Trakt broadcast numbering.
- Stable AIOStreams bulk-part event IDs retain the existing idempotency behavior, so a retried part cannot create a second bridge write after it has been marked processed.

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
- Trakt assigns watchlist timestamps, so the bridge does not claim to preserve the AIOStreams event timestamp when adding to Trakt.
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
