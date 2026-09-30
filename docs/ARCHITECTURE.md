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
        +-- /sync/playback/* -> Continue Watching
        +-- /sync/watched/*  -> watched history
        +-- /sync/history    -> single + bulk played marks
        +-- /sync/history/remove -> single + bulk unplayed marks
        +-- /sync/watchlist/* -> movie/show watchlist
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
- whole-season and whole-series played/unplayed marks;
- movie/show watchlist state.

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

### Bulk played/unplayed marks

AIOStreams uses the same push route for bulk marks, with the show/meta ID in the path and `scope: season` or `scope: series` in the body. Each request contains at most 500 changed videos and has a stable event ID; larger operations arrive as numbered `part` / `parts` requests.

The bridge does **not** send a bare Trakt show or season when the AIOStreams scope is broad. It writes exactly the supplied `videos[]` by grouping them into Trakt's nested show → seasons → episodes request shape:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

This preserves AIOStreams' actual metadata enumeration and avoids marking episodes that were not part of the client operation. Duplicate episode rows inside a part are collapsed before the Trakt write.

Bulk requests reject malformed part metadata, more than 500 videos, season-scope videos from another season, and anime/absolute-number episode IDs that the bridge cannot map safely to Trakt broadcast numbering.

A successful bulk history request returns the same logical `history:add` / `history:remove` action used by single marks, with diagnostic fields `bulk:true`, scope, video count and part numbers. The existing pull-cache invalidation path therefore applies automatically.

### Watchlist push

AIOStreams sends watchlist changes only for movie/show favourites. v0.3.x accepts:

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

A successful `history:add`, `history:remove`, `watchlist:add`, `watchlist:remove`, or completed `scrobble:stop` invalidates the pull cache. The next pull therefore observes Trakt-side automatic watchlist removal and the new state version instead of replaying a now-obsolete cache entry.

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

The v0.3 cache namespace remains `pull-state:v3:`. v0.3.1 does not change the state-version basis, so a v0.3.0 cache remains compatible across the upgrade.

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

A push event's AIOStreams `id` is an idempotency key. Retries preserve that ID, so repeated log rows with the same push ID represent delivery attempts for one event and may be grouped into `recovered` after a later success. Bulk parts also have distinct stable IDs, so each part is independently idempotent.

A pull event uses `pull|<since>` only as a cursor label. Independent polls are never grouped as retry attempts. Fresh pull detail includes watched counts and `watchlistItems`. Cache rows include `cacheLayer` as `memory` or `sqlite`.

## Deferred after v0.3.1

- dropped/undropped state
- richer next-up generation
- anime/absolute-number episode mapping
- deeper metadata/orphan-ID reconciliation
