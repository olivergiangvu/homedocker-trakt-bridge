# Architecture — v0.2.2

```text
Infuse / Swiftfin / Jellyfin-compatible client
                    |
                    v
               AIOStreams
             watch_state v2
               /         \
              / push      \ pull
             v              ^
HomeDocker Trakt Bridge ----+
        |      ^
        |      +-- matching-version memory cache
        |      +-- in-flight pull coalescing
        |      +-- bounded stale fallback on transient 429/5xx
        |
        +-- /sync/playback/* -> Continue Watching
        +-- /sync/watched/*  -> watched history
        +-- /sync/last_activities -> watched version gate
        |
        +-- validate + idempotency
        +-- provider-ID -> Trakt resolver/cache
        +-- OAuth token refresh
        +-- 90% AIOStreams watched-threshold guard
        +-- safe pagination + fail-closed authoritative pull
        |
        v
      Trakt API
```

## Authority

Trakt is the canonical long-term watched-history source. AIOStreams is the playback/state surface for Jellyfin-compatible clients.

v0.2.x is bidirectional:

- **Push:** AIOStreams playback and watched changes are written to Trakt.
- **Pull:** Trakt playback progress and watched history are read back into AIOStreams.

CrossWatch and Remux are not part of the critical path for this bridge.

## Push semantics

Supported events:

- `start`
- `pause`
- `stop`
- `played`
- `unplayed`

AIOStreams marks watched at 90%; Trakt `/scrobble/stop` can mark watched above 80%. Therefore `stop + played:false` is deliberately mapped to `/scrobble/pause` so progress between 80% and 89% cannot become watched accidentally.

## Pull semantics

The manifest advertises:

```json
{
  "pull": {
    "items": true,
    "watched": true,
    "ttlSeconds": 900
  }
}
```

`PULL_TTL_SECONDS` is also the bridge's local cache TTL. AIOStreams currently uses its own global `WATCH_STATE_PULL_TTL` setting to decide when the Jellyfin UI should trigger an on-demand pull, so the bridge cannot rely on manifest TTL alone to protect the Trakt API.

### Continue Watching

A fresh bridge read calls:

- `/sync/playback/movies?extended=full`
- `/sync/playback/episodes?extended=full`

It returns standard AIOStreams IDs, preferring IMDb and falling back to `tmdb:` then `tvdb:`. When Trakt supplies runtime, the bridge returns both `durationMs` and calculated `positionMs`; otherwise it still returns `progressPercent` for AIOStreams to combine with an existing local duration.

### Watched history

When the caller's `since` no longer matches the Trakt watched version, the bridge reads:

- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`

Movie IDs are returned directly in the chosen AIOStreams ID space. Show season/episode progress is converted to video IDs such as `tt0903747:3:10`.

The watched block is authoritative. If Trakt returns an incomplete show-progress payload, the bridge returns an error instead of an empty history, preventing AIOStreams from deleting previously imported watched state.

### Version / since gate

`/sync/last_activities` is reduced to a stable watched-history version based on movie and episode watched timestamps. AIOStreams sends the previous version as `?since=...`.

- if `since` differs from the current Trakt version, the bridge fetches and returns authoritative watched history;
- if `since` matches, `watched` is omitted;
- matching-version repeated pulls may be served from bridge cache without touching Trakt.

## v0.2.2 rate-limit hardening

### Fresh cache

Each successful pull stores only:

```text
version
items
fetchedAt
```

The cache is memory-only and scoped per bridge profile.

A cache hit is allowed only when:

```text
request.since == cache.version
and
cache age < PULL_TTL_SECONDS
```

The cached response contains only `version + items`. It never contains or invents `watched`.

### In-flight coalescing

Concurrent requests for the same `profileId + since` share one Trakt pull task. This prevents multiple UI refreshes from multiplying upstream calls before the first request completes.

### Stale-on-transient-error

If a fresh pull fails with `429` or `5xx`, a stale cached response may be used only when:

```text
request.since == cache.version
and
cache age < PULL_STALE_IF_ERROR_SECONDS
```

Authentication/reconnect failures do not use stale fallback. A stale response still contains only `version + items`, so it cannot erase watched history.

## Pagination

Current Trakt watched endpoints are paginated. The bridge follows `X-Pagination-Page-Count`, requests 250 movies/page and 100 shows/page for `extended=progress`, and fails if pagination exceeds the configured safety cap.

## Diagnostics model

Push and pull diagnostics use different identities by design.

A push event's AIOStreams `id` is an idempotency key. Retries preserve that ID, so repeated log rows with the same push ID represent delivery attempts for one event and may be grouped into `recovered` after a later success.

A pull event uses `pull|<since>` only as a cursor label. Independent polls are never grouped as retry attempts. v0.2.2 records the pull source as `trakt`, `coalesced`, `cache`, or `stale-cache`.

## Deferred to v0.3+

- Trakt watchlist
- dropped/undropped state
- AIOStreams bulk marks
- richer next-up generation
- anime/absolute-number episode mapping
- deeper metadata/orphan-ID reconciliation
