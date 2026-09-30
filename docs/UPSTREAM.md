# Upstream contracts used by v0.2.3

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.2.3 relies on these documented/current semantics:

- push event IDs are stable across retries;
- missing `durationMs` means unknown, never zero;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull accepts `items`, authoritative `watched`, and an optional `version`;
- AIOStreams sends the last version back as `?since=`;
- an omitted `watched` block means no new watched information, while an empty authoritative block means nothing is watched;
- current pull limits are intentionally high (`WATCH_STATE_PULL_MAX_ITEMS` 5000 and `WATCH_STATE_PULL_MAX_WATCHED` 50000 by default);
- AIOStreams' current on-demand read freshness is controlled by its global `WATCH_STATE_PULL_TTL` setting, not solely by the addon's manifest `ttlSeconds` hint;
- the current AIOStreams default for that global on-demand TTL is 300 seconds, while its background read interval defaults to 1800 seconds.

The bridge therefore never substitutes an empty `watched` object for an upstream failure. It protects Trakt independently with a matching-version local cache because the bridge cannot assume AIOStreams will schedule pulls at the addon's advertised TTL. v0.2.3 persists only that safe non-authoritative cache entry (`version + items + fetchedAt`) so the protection survives a container recreate.

## Trakt

Primary references:

- https://github.com/trakt/trakt-api
- https://trakt.docs.apiary.io/reference/sync

OAuth / push invariants retained from v0.1:

- Authorization Code OAuth flow;
- access tokens are refreshed with the latest returned single-use refresh token;
- `/scrobble/start`, `/pause`, `/stop` provide active playback state;
- `/scrobble/stop` has a lower watched threshold than AIOStreams, so the bridge preserves the AIOStreams `played` decision.

v0.2 pull endpoints:

- `/sync/last_activities`
- `/sync/playback/movies`
- `/sync/playback/episodes`
- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`

As of July 2026, Trakt's watched endpoints are paginated in production. Requests without pagination return only the first page; clients must follow the pagination headers. `extended=progress` is the required form for season/episode watched progress and is capped more tightly than normal watched pages.

Trakt may respond with `429` and `Retry-After`. v0.2.x only converts such a transient failure into a successful stale-cache response when the caller's `since` still exactly matches a previously successful cached version. The same bounded fallback may be used for transient `5xx`. Authentication/reconnect failures remain errors.

Persisting the safe cache in SQLite does not change Trakt authority. Initial pulls, version mismatches, expired cache entries and any request requiring a new authoritative watched block still contact Trakt.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity mapping and authoritative watched import are fail-closed: if a response is incomplete or cannot be mapped safely, the bridge does not guess a destructive replacement state.
