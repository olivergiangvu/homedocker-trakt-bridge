# Upstream contracts used by v0.3.0

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.0 relies on these documented/current semantics:

- push event IDs are stable across retries;
- missing `durationMs` means unknown, never zero;
- playback push supports `start`, `pause`, `stop`, `played`, and `unplayed`;
- movie/show favourite changes are exposed as `watchlisted` / `unwatchlisted` when the addon advertises them;
- watchlist events use `scope: movie` or `scope: series` and the meta ID in the route;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull accepts `items`, authoritative `watched`, authoritative `watchlist`, and an optional `version`;
- `watchlist[]` rows use `type`, `metaId`, and optional Unix-second `at`;
- AIOStreams sends the last version back as `?since=`;
- omitted authoritative blocks mean no new information for that block, while an explicitly empty returned block is authoritative empty state;
- AIOStreams' current on-demand read freshness is controlled by its global `WATCH_STATE_PULL_TTL` setting, not solely by the addon's manifest `ttlSeconds` hint;
- the current AIOStreams default for that global on-demand TTL is 300 seconds, while its background read interval defaults to 1800 seconds.

The bridge therefore never substitutes empty watched/watchlist state for an upstream failure. Cached and stale responses intentionally contain only `version + items`.

## Trakt

Primary references:

- `trakt/trakt-api`
- `https://trakt.docs.apiary.io/reference/sync`

OAuth / playback invariants retained from earlier releases:

- Authorization Code OAuth flow;
- access tokens are refreshed with the latest returned single-use refresh token;
- `/scrobble/start`, `/pause`, `/stop` provide active playback state;
- `/scrobble/stop` has a lower watched threshold than AIOStreams, so the bridge preserves the AIOStreams `played` decision.

v0.3.0 pull endpoints:

- `/sync/last_activities`
- `/sync/playback/movies`
- `/sync/playback/episodes`
- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`
- `/sync/watchlist/movies/added/desc`
- `/sync/watchlist/shows/added/desc`

The Trakt `last_activities` payload exposes separate watched and watchlisted timestamps. v0.3.0 includes movie/episode watched activity plus movie/show watchlist activity in the single AIOStreams state version.

v0.3.0 watchlist writes:

- `POST /sync/watchlist` to add movie/show items;
- `POST /sync/watchlist/remove` to remove them.

The public Sync reference documents JSON POST bodies for both add and remove. Trakt assigns the list timestamp server-side; a custom `listed_at` / `watchlisted_at` timestamp is not supported by the normal sync add endpoint, so the bridge does not claim to preserve the AIOStreams event time.

The current public reference also documents that watched items are automatically removed from the Trakt watchlist. The bridge therefore invalidates its pull cache after mutations that can change watched/watchlist authority so the next pull can observe that removal.

Trakt can return HTTP `420` when account watchlist limits are exceeded. The bridge exposes that as non-retryable `422` to AIOStreams instead of turning it into a retrying 5xx delivery loop.

As of 2026, Trakt sync list/history endpoints may paginate. The bridge follows pagination headers and enforces `PULL_MAX_PAGES` as a safety cap.

Trakt may respond with `429` and `Retry-After`. The bridge only converts a transient `429` / `5xx` into a successful stale-cache response when the caller's `since` exactly matches a previously successful cached version. Authentication/reconnect failures remain errors.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity mapping and authoritative state import are fail-closed: if a changed response is incomplete or cannot be mapped safely, the bridge does not guess a destructive replacement state.

v0.3.0 supports watchlist movies and shows because those are the watchlist scopes AIOStreams exposes from Jellyfin favourites. Season/episode watchlist state, dropped state, bulk played/unplayed marks and anime absolute-number mapping remain outside the advertised capability set.
