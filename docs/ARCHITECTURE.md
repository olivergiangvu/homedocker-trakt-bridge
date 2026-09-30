# Architecture — v0.3.1

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
        |      +-- matching-version cache
        |      |     +-- in-memory hot copy
        |      |     +-- SQLite persistence in bridge.db
        |      +-- in-flight pull coalescing
        |      +-- bounded stale fallback on transient 429/5xx
        |
        +-- /scrobble/* -> playback transitions
        +-- /sync/history -> single + bulk watched state
        +-- /sync/watchlist/* -> movie/show watchlist
        +-- /sync/playback/* -> Continue Watching pull
        +-- /sync/watched/* -> authoritative watched pull
        +-- /sync/last_activities -> combined state version gate
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

Trakt is the canonical long-term tracker source for watched history and watchlist state. AIOStreams is the playback/state surface for Jellyfin-compatible clients.

v0.3.1 is bidirectional for:

- playback progress;
- watched/unwatched state;
- movie/show watchlist state;
- whole-season and whole-series watched/unwatched bulk marks.

CrossWatch and Remux are not part of the critical path for this bridge.

## Push semantics

Supported events:

- `start`
- `pause`
- `stop`
- `played`
- `unplayed`
- `watchlisted`
- `unwatchlisted`

The manifest advertises `watchState.push.bulk=true`.

AIOStreams marks watched at 90%; Trakt `/scrobble/stop` can mark watched above 80%. Therefore `stop + played:false` is deliberately mapped to `/scrobble/pause` so progress between 80% and 89% cannot become watched accidentally.

### Single played/unplayed marks

Single movie/episode marks retain the existing mapping:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

Episodes are resolved safely to Trakt episode IDs before a single-item write.

### Bulk season/show marks

AIOStreams Watch State v2 sends a whole-season/show mark as one or more requests with:

```text
scope = season | series
videos[] = explicit changed videos
part / parts
```

Each part contains at most 500 videos. v0.3.1 validates the part metadata and every video row, resolves the parent show once, groups only the supplied season/episode numbers, and sends one nested Trakt history request for that part.

Example internal mapping:

```json
{
  "shows": [{
    "ids": { "trakt": 42 },
    "seasons": [{
      "number": 2,
      "episodes": [
        { "number": 1, "watched_at": "..." },
        { "number": 2, "watched_at": "..." }
      ]
    }]
  }]
}
```

For `unplayed`, `watched_at` is omitted and the body is sent to `/sync/history/remove`.

The bridge deliberately does **not** send a bare show or season object. Trakt accepts those broad forms, but they could affect episodes not present in AIOStreams' explicit `videos[]` set. Duplicate season/episode rows inside one part are collapsed before the Trakt request.

Anime/absolute-number video IDs remain fail-closed because AIOStreams metadata numbering cannot safely be assumed to equal Trakt broadcast numbering.

### Watchlist push

AIOStreams sends watchlist changes only for movie/show favourites. The bridge accepts:

```text
scope=movie  -> Trakt movie watchlist
scope=series -> Trakt show watchlist
```

Writes are:

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

The bridge resolves shared provider IDs to Trakt media before writing. It does not copy the AIOStreams event timestamp into Trakt because the Trakt sync endpoint assigns the watchlist timestamp server-side.

A successful `history:add`, `history:remove`, `history:bulk-add`, `history:bulk-remove`, `watchlist:add`, `watchlist:remove`, or completed `scrobble:stop` invalidates the pull cache. The next pull therefore observes the new authoritative state instead of replaying an obsolete cache entry.

## Pull semantics

The manifest advertises:

```json
{
  "pull": {
    "items": true,
    "watched": true,
    "watchlist": true,
    "ttlSeconds": 900
  }
}
```

`PULL_TTL_SECONDS` is also the bridge's local cache TTL. AIOStreams currently uses its own global `WATCH_STATE_PULL_TTL` setting to decide when the Jellyfin UI should trigger an on-demand pull, so the bridge cannot rely on manifest TTL alone to protect the Trakt API.

### Continue Watching

Every fresh bridge read calls:

- `/sync/playback/movies?extended=full`
- `/sync/playback/episodes?extended=full`

It returns standard AIOStreams IDs, preferring IMDb and falling back to `tmdb:` then `tvdb:`. When Trakt supplies runtime, the bridge returns both `durationMs` and calculated `positionMs`; otherwise it still returns `progressPercent`.

### Authoritative watched + watchlist state

When the caller's `since` no longer matches the current combined state version, the bridge reads all supported authoritative state atomically:

- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`
- `/sync/watchlist/movies/added/desc`
- `/sync/watchlist/shows/added/desc`

Movie watched IDs are returned directly in the chosen AIOStreams ID space. Show season/episode progress is converted to video IDs such as `tt0903747:3:10`.

Watchlist rows become:

```json
{ "type": "movie", "metaId": "tt0111161", "at": 1788900000 }
{ "type": "series", "metaId": "tt0903747", "at": 1788800000 }
```

Rows that have only a Trakt-internal ID and no representable IMDb/TMDb/TVDb ID are skipped instead of inventing an AIOStreams ID.

`watched` and `watchlist` are treated as authoritative together. If any changed-state read is incomplete or fails, the pull fails rather than returning a partially empty replacement that could remove local state incorrectly.

### Version / since gate

`/sync/last_activities` is reduced to a stable state version based on:

- movie watched timestamp;
- episode watched timestamp;
- movie watchlist timestamp;
- show watchlist timestamp.

AIOStreams sends the previous version as `?since=...`.

- if `since` differs, the bridge fetches and returns authoritative watched + watchlist state;
- if `since` matches, both authoritative blocks are omitted;
- playback `items` continue to refresh independently;
- matching-version repeated pulls may be served from bridge cache without touching Trakt.

v0.3.x uses the persistent pull-cache namespace `pull-state:v3:`. Old v0.2.x `pull-state:` entries are ignored and expire naturally.

## Restart-safe rate-limit hardening

### Cache model

Each successful pull stores only:

```text
version
items
fetchedAt
```

The entry is held in two layers:

```text
memory hot copy
    |
    +-- miss -> SQLite media_cache (bridge.db)
```

SQLite retention is bounded by `PULL_STALE_IF_ERROR_SECONDS`. The embedded `fetchedAt` timestamp is checked independently before a response can be treated as fresh or stale.

A cache hit is allowed only when:

```text
request.since == cache.version
and
cache age < PULL_TTL_SECONDS
```

Cached responses never contain or invent authoritative `watched` or `watchlist` state.

### In-flight coalescing

Concurrent requests for the same `profileId + since` share one Trakt pull task. This prevents multiple UI refreshes from multiplying upstream calls before the first request completes.

### Stale-on-transient-error

If a fresh pull fails with `429` or `5xx`, a stale cached response may be used only when:

```text
request.since == cache.version
and
cache age < PULL_STALE_IF_ERROR_SECONDS
```

Authentication/reconnect failures do not use stale fallback. A stale response still contains only `version + items`, so it cannot erase watched history or watchlist state.

## Pagination

The bridge follows Trakt `X-Pagination-Page-Count`, requests 250 watched movies/page and 100 rows/page for watched shows and watchlists, and fails if pagination exceeds the configured safety cap.

## Diagnostics model

Push and pull diagnostics use different identities by design.

A push event's AIOStreams `id` is an idempotency key. Retries preserve that ID, so repeated log rows with the same push ID represent delivery attempts for one event and may be grouped into `recovered` after a later success.

A bulk part is also one stable AIOStreams event. On success its detail records `history:bulk-add` or `history:bulk-remove`, scope, number of videos and `part` / `parts`.

A pull event uses `pull|<since>` only as a cursor label. Independent polls are never grouped as retry attempts. Fresh pull detail includes watched counts and `watchlistItems`. Cache rows include `cacheLayer` as `memory` or `sqlite`.

## Deferred after v0.3.1

- dropped/undropped state
- richer next-up generation
- anime/absolute-number episode mapping
- deeper metadata/orphan-ID reconciliation
