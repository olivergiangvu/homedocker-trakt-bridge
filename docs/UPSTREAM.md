# Upstream contracts used by v0.3.2

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.2 relies on these documented/current semantics:

- push event IDs are stable across retries;
- missing `durationMs` means unknown, never zero;
- playback push supports `start`, `pause`, `stop`, `played`, and `unplayed`;
- movie/show favourite changes are exposed as `watchlisted` / `unwatchlisted` when the addon advertises them;
- watchlist events use `scope: movie` or `scope: series` and the meta ID in the route;
- `watchState.push.bulk=true` changes whole-season/show `played` / `unplayed` delivery from one event per video into bulk requests for that bulk dispatch;
- bulk marks use `scope: season` or `scope: series`, put the meta ID in the path/body, include the explicit changed `videos[]`, and include `part` / `parts`;
- AIOStreams splits a mark over more than 500 videos into consecutive independent bulk requests;
- bulk `videos[]` is the authoritative set to write; an addon must not interpret a show-level mark as permission to affect tracker episodes not listed by AIOStreams;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull accepts `items`, authoritative `watched`, authoritative `watchlist`, and an optional `version`;
- `watchlist[]` rows use `type`, `metaId`, and optional Unix-second `at`;
- AIOStreams sends the last version back as `?since=`;
- omitted authoritative blocks mean no new information for that block, while an explicitly empty returned block is authoritative empty state;
- AIOStreams' current on-demand read freshness is controlled by its global `WATCH_STATE_PULL_TTL` setting, not solely by the addon's manifest `ttlSeconds` hint;
- the current AIOStreams default for that global on-demand TTL is 300 seconds, while its background read interval defaults to 1800 seconds.

The bridge therefore never substitutes empty watched/watchlist state for an upstream failure. Cached and stale responses intentionally contain only `version + items`.

The v0.3.x state-version basis includes watchlist activity. The local persisted pull-cache namespace remains `pull-state:v3:`. Existing v0.2.x `pull-state:` rows are ignored after upgrade so an old cache cannot postpone the first authoritative watchlist-aware pull.

### Bulk mark compatibility boundary

AIOStreams explicitly warns that metadata IDs and episode numbering can come from different spaces. The bridge accepts bulk season/show marks only when their `videos[]` can be represented as ordinary season/episode numbers for the resolved show. Anime/absolute-number spaced video IDs such as `kitsu:`, `mal:`, `anilist:` and `anidb:` remain fail-closed.

Current AIOStreams source has two important properties:

1. `dispatchBulkMark()` chooses **either** bulk parts for a sink that advertises `bulk=true` **or** one event per video for a non-bulk sink. One invocation does not deliberately emit both forms to the same sink.
2. The Jellyfin season/show `setPlayed()` path records each episode into AIOStreams local watch state and then calls `reportBulkMark()` once; that branch returns without directly calling `reportPlayback()` for every episode.

HomeDocker production testing nevertheless observed a valid bulk season event followed later by new per-episode Jellyfin marks with different event IDs and later event timestamps. Those are therefore treated as a separate client/API reconciliation path rather than retries of the bulk event itself.

The Watch State v2 contract gives every delivery its own stable event ID, but it does not promise that an external Jellyfin-compatible client will never make a later semantically redundant per-episode API call after a season-level action. v0.3.2 hardens the bridge against that real compatibility pattern without changing the advertised protocol.

The dedupe is intentionally narrow: only same-profile, same-kind, exact-video single episode events whose own `at` falls after the successful bulk `at` and inside `BULK_SINGLE_DEDUPE_SECONDS` are suppressed. Opposite-state events, movies, earlier events, and events outside the window are not suppressed.

## Trakt

Primary references:

- `trakt/trakt-api`
- `https://trakt.docs.apiary.io/reference/sync`

OAuth / playback invariants retained from earlier releases:

- Authorization Code OAuth flow;
- access tokens are refreshed with the latest returned single-use refresh token;
- `/scrobble/start`, `/pause`, `/stop` provide active playback state;
- `/scrobble/stop` has a lower watched threshold than AIOStreams, so the bridge preserves the AIOStreams `played` decision.

v0.3.x pull endpoints:

- `/sync/last_activities`
- `/sync/playback/movies`
- `/sync/playback/episodes`
- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`
- `/sync/watchlist/movies/added/desc`
- `/sync/watchlist/shows/added/desc`

The Trakt `last_activities` payload exposes separate watched and watchlisted timestamps. v0.3.x includes movie/episode watched activity plus movie/show watchlist activity in the single AIOStreams state version.

Watchlist writes:

- `POST /sync/watchlist` to add movie/show items;
- `POST /sync/watchlist/remove` to remove them.

The public Sync reference documents JSON POST bodies for both add and remove. Trakt assigns the list timestamp server-side; a custom `listed_at` / `watchlisted_at` timestamp is not supported by the normal sync add endpoint, so the bridge does not claim to preserve the AIOStreams event time.

### Bulk history contract

Current Trakt Sync history accepts shows, seasons, episodes and movies. For a show object it also accepts nested:

```text
shows[].seasons[].number
shows[].seasons[].episodes[].number
shows[].seasons[].episodes[].watched_at
```

The same bulk media schema is accepted by `/sync/history/remove` (with optional history IDs additionally supported there).

The bridge resolves the parent show once, groups only AIOStreams' explicit `videos[]` by season, and sends one nested `shows` request per AIOStreams bulk part:

- `played` -> `POST /sync/history`, with `watched_at` on each listed episode;
- `unplayed` -> `POST /sync/history/remove`, without `watched_at`.

The bridge deliberately does not send a bare show or a bare season. Trakt documents that a bare show can mark all episodes and a season can mark all episodes in that season, which would be broader than AIOStreams' explicit changed-video set.

Because history add accepts `watched_at`, sending a semantically duplicate `played` event later with a different timestamp can create another watched-history entry rather than merely being a harmless transport retry. That is the concrete reason v0.3.2 suppresses recently bulk-covered same-kind single episode echoes before they reach Trakt.

The current public reference also documents that watched items are automatically removed from the Trakt watchlist. The bridge therefore invalidates its pull cache after mutations that can change watched/watchlist authority so the next pull can observe that removal.

Trakt can return HTTP `420` when account watchlist limits are exceeded. The bridge exposes that as non-retryable `422` to AIOStreams instead of turning it into a retrying 5xx delivery loop.

As of 2026, Trakt sync list/history endpoints may paginate. The bridge follows pagination headers and enforces `PULL_MAX_PAGES` as a safety cap.

Trakt may respond with `429` and `Retry-After`. The bridge only converts a transient `429` / `5xx` into a successful stale-cache response when the caller's `since` exactly matches a previously successful cached version. Authentication/reconnect failures remain errors.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity mapping and authoritative state import are fail-closed: if a changed response is incomplete or cannot be mapped safely, the bridge does not guess a destructive replacement state.

v0.3.2 supports movie/show watchlist, standard-numbered season/show bulk played/unplayed marks, and bounded duplicate-safe reconciliation when a Jellyfin-compatible client later echoes the same per-episode state. Season/episode watchlist state, dropped state and anime/absolute-number mapping remain outside the advertised capability set.
