# Upstream contracts used by v0.3.1

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.1 relies on these documented/current semantics:

- push event IDs are stable across retries;
- missing `durationMs` means unknown, never zero;
- playback push supports `start`, `pause`, `stop`, `played`, and `unplayed`;
- movie/show favourite changes are exposed as `watchlisted` / `unwatchlisted` when the addon advertises them;
- watchlist events use `scope: movie` or `scope: series` and the meta ID in the route;
- declaring `watchState.push.bulk=true` changes whole-season/show played/unplayed marks from one event per episode into one request per part;
- a bulk mark uses `scope: season` or `scope: series`, the show/meta ID in the route, and a `videos[]` list containing the changed episodes;
- bulk parts contain at most 500 videos and expose stable `part` / `parts` metadata;
- the protocol explicitly says to write `videos[]`, not blindly mark the whole show, because a tracker show-level operation could include episodes the metadata surface did not list;
- bulk requests are delivered after single events; each part is independently retried/idempotent;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull accepts `items`, authoritative `watched`, authoritative `watchlist`, and an optional `version`;
- `watchlist[]` rows use `type`, `metaId`, and optional Unix-second `at`;
- AIOStreams sends the last version back as `?since=`;
- omitted authoritative blocks mean no new information for that block, while an explicitly empty returned block is authoritative empty state;
- AIOStreams' current on-demand read freshness is controlled by its global `WATCH_STATE_PULL_TTL` setting, not solely by the addon's manifest `ttlSeconds` hint;
- the current AIOStreams default for that global on-demand TTL is 300 seconds, while its background read interval defaults to 1800 seconds.

The bridge therefore never substitutes empty watched/watchlist state for an upstream failure. Cached and stale responses intentionally contain only `version + items`.

The v0.3 state-version basis is broader than v0.2.x because it includes watchlist activity. The local persisted pull-cache namespace remains `pull-state:v3:`. v0.3.1 does not change that cursor basis.

## Trakt

Primary references:

- `trakt/trakt-api`
- `https://trakt.docs.apiary.io/reference/sync`

OAuth / playback invariants retained from earlier releases:

- Authorization Code OAuth flow;
- access tokens are refreshed with the latest returned single-use refresh token;
- `/scrobble/start`, `/pause`, `/stop` provide active playback state;
- `/scrobble/stop` has a lower watched threshold than AIOStreams, so the bridge preserves the AIOStreams `played` decision.

Pull endpoints:

- `/sync/last_activities`
- `/sync/playback/movies`
- `/sync/playback/episodes`
- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`
- `/sync/watchlist/movies/added/desc`
- `/sync/watchlist/shows/added/desc`

The Trakt `last_activities` payload exposes separate watched and watchlisted timestamps. The bridge includes movie/episode watched activity plus movie/show watchlist activity in the single AIOStreams state version.

Watchlist writes:

- `POST /sync/watchlist` to add movie/show items;
- `POST /sync/watchlist/remove` to remove them.

The public Sync reference documents JSON POST bodies for both add and remove. Trakt assigns the list timestamp server-side; a custom `listed_at` / `watchlisted_at` timestamp is not supported by the normal sync add endpoint, so the bridge does not claim to preserve the AIOStreams event time.

### Bulk history writes

Current Trakt Sync contracts document that `/sync/history` accepts movies, shows, seasons and episodes. A show object may contain nested `seasons[]`, and each season may contain explicit `episodes[]` with episode numbers and optional `watched_at` timestamps. `/sync/history/remove` merges the same bulk-media request schema.

v0.3.1 therefore maps one AIOStreams bulk part to exactly one Trakt request:

- `played` → `POST /sync/history`;
- `unplayed` → `POST /sync/history/remove`.

The bridge resolves the show once, groups only the supplied AIOStreams `videos[]` into nested Trakt season/episode rows, and never sends a bare show object. This follows AIOStreams' explicit bulk guidance and avoids changing episodes outside the operation.

The current public reference also documents that watched items are automatically removed from the Trakt watchlist. The bridge therefore invalidates its pull cache after mutations that can change watched/watchlist authority so the next pull can observe that removal.

Trakt can return HTTP `420` when account watchlist limits are exceeded. The bridge exposes that as non-retryable `422` to AIOStreams instead of turning it into a retrying 5xx delivery loop.

As of 2026, Trakt sync list/history endpoints may paginate. The bridge follows pagination headers and enforces `PULL_MAX_PAGES` as a safety cap.

Trakt may respond with `429` and `Retry-After`. The bridge only converts a transient `429` / `5xx` into a successful stale-cache response when the caller's `since` exactly matches a previously successful cached version. Authentication/reconnect failures remain errors.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity mapping and authoritative state import are fail-closed: if a changed response is incomplete or cannot be mapped safely, the bridge does not guess a destructive replacement state.

v0.3.1 supports movie/show watchlist sync and standard-numbered bulk played/unplayed marks. Season/episode watchlist state, dropped state and anime/absolute-number mapping remain outside the advertised capability set.
