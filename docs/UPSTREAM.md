# Upstream contracts used by v0.2

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.2 relies on these documented semantics:

- push event IDs are stable across retries;
- missing `durationMs` means unknown, never zero;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull accepts `items`, authoritative `watched`, and an optional `version`;
- AIOStreams sends the last version back as `?since=`;
- an omitted `watched` block means no new watched information, while an empty authoritative block means nothing is watched;
- current pull limits are intentionally high (`WATCH_STATE_PULL_MAX_ITEMS` 5000 and `WATCH_STATE_PULL_MAX_WATCHED` 50000 by default).

The bridge therefore never substitutes an empty `watched` object for an upstream failure.

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

## Compatibility policy

The bridge advertises only capabilities it implements. Identity mapping and authoritative watched import are fail-closed: if a response is incomplete or cannot be mapped safely, the bridge does not guess a destructive replacement state.
