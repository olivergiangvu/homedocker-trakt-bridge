# Architecture — v0.3.3

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
        +-- provider-ID normalizer
        |     +-- metaId fallback
        |     +-- IMDb / TMDb / TVDb alias set
        |     +-- 404 alias failover
        |
        +-- /scrobble/* -> playback transitions
        +-- /sync/history -> single + bulk watched state
        +-- recent bulk coverage -> suppress duplicate single echoes
        +-- /sync/watchlist/* -> movie/show watchlist
        +-- /sync/playback/* -> Continue Watching pull
        +-- /sync/watched/* -> authoritative watched pull
        +-- /sync/last_activities -> combined state version gate
        |
        v
      Trakt API
```

## Authority

Trakt is the canonical long-term tracker source for watched history and watchlist state. AIOStreams is the Jellyfin-compatible playback/state surface.

v0.3.3 is bidirectional for playback progress, watched/unwatched state, movie/show watchlist state, and whole-season/show watched/unwatched bulk marks.

CrossWatch and Remux are not part of the critical path.

## Identity model

AIOStreams Watch State v2 carries two related identity surfaces:

```text
metaId / videoId
ids = shared show/film provider IDs
```

The bridge normalizes supported shared IDs to:

```text
IMDb  -> tt0903747
TMDb  -> tmdb:1396
TVDb  -> tvdb:81189
```

`providerIdsForEvent()` merges a representable `metaId` with valid `event.ids`; explicit shared-vocabulary IDs win while `metaId` fills omissions.

This matters for client and metadata paths where AIOStreams knows the canonical meta ID but does not populate every provider field.

### Provider resolution

For movie/show resolution the bridge tries known aliases in deterministic order:

```text
IMDb -> TMDb -> TVDb (shows only for TVDb)
```

A Trakt search 404 means only that provider spelling missed, so v0.3.3 continues to the next known alias. Authentication failures, `429`, and `5xx` are not swallowed and keep their existing retry/reconnect semantics.

### Pull aliases

The preferred output spelling remains:

```text
IMDb -> TMDb -> TVDb
```

A single watched episode list therefore stays compact and deterministic. However AIOStreams explicitly permits `watched.counts` to be keyed by every ID a show answers to. v0.3.3 emits the same advisory count under every representable alias returned by Trakt.

Example:

```json
{
  "counts": {
    "tt0903747": { "watched": 37, "total": 62, "at": 1788974000 },
    "tmdb:1396": { "watched": 37, "total": 62, "at": 1788974000 },
    "tvdb:81189": { "watched": 37, "total": 62, "at": 1788974000 }
  }
}
```

This reduces raw-ID/orphan presentation when the imported history spelling and the active metadata spelling differ.

Trakt-only IDs without a representable IMDb/TMDb/TVDb alias are still skipped rather than invented.

## Next Up policy

AIOStreams computes the real Next Up episode locally from imported watched rows and metadata. Its Watch State pull contract also allows `watched.nextUp` hints.

v0.3.3 is deliberately conservative:

- if the Trakt watched row itself contains a valid `next_episode`, the bridge forwards that exact episode plus `last_watched_at`;
- otherwise `nextUp` is omitted for that show;
- no season boundary is guessed;
- no contiguous-numbering assumption is made;
- no `/shows/:id/progress/watched` fan-out is added for hundreds of shows.

The last point is intentional: this server already protects a large Trakt history from rate-limit pressure, so a per-show Next Up query fan-out would undo that work.

## Push semantics

Supported push events:

```text
start
pause
stop
played
unplayed
watchlisted
unwatchlisted
```

Manifest: `watchState.push.bulk=true`.

AIOStreams marks watched at 90%; Trakt `/scrobble/stop` can mark watched above 80%. `stop + played:false` is therefore mapped to Trakt pause so 80–89% cannot become watched accidentally.

### Bulk history

Whole-season/show marks contain explicit `videos[]`, `part`, and `parts`. Each part is validated and converted to one nested Trakt history request containing only those videos.

The bridge never sends a bare show or season object because that could affect episodes not present in AIOStreams metadata.

### Bulk → single reconciliation

Production showed valid bulk marks followed ~1 minute later by new per-episode Jellyfin-compatible events with different IDs. v0.3.2+ stores short-lived successful bulk coverage in SQLite.

A later single episode is suppressed only when all match:

```text
same profile
same event kind
same videoId
single.at >= bulk.at
delta <= BULK_SINGLE_DEDUPE_SECONDS
```

Opposite-state events, movies, earlier events and events outside the window always pass through.

## Pull semantics

Fresh pull base reads:

```text
/sync/last_activities
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

When the state cursor changed, authoritative state additionally reads all pages of:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

`watched` and `watchlist` are treated atomically. An incomplete changed-state read fails rather than returning a destructive partial replacement.

### Version / migration gate

The state hash includes:

```text
schema = watch-state-v0.3.3
movies.watched_at
episodes.watched_at
movies.watchlisted_at
shows.watchlisted_at
```

The schema salt intentionally changes the cursor once at upgrade. Persisted safe pull cache also moves to:

```text
pull-state:v4:<profile>
```

Old v0.3.2 `pull-state:v3:` rows are ignored and expire naturally. Together these guarantee the first v0.3.3 pull returns the new alias-aware authoritative representation instead of an old cached shape.

## Restart-safe rate-limit hardening

Each successful pull caches only:

```text
version
items
fetchedAt
```

The hot entry lives in memory and the same safe non-authoritative copy is persisted in SQLite.

Fresh cache hit:

```text
request.since == cache.version
cache age < PULL_TTL_SECONDS
```

Transient stale fallback additionally requires:

```text
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Cached responses never contain authoritative watched/watchlist blocks.

## Pagination

The bridge follows Trakt `X-Pagination-Page-Count`, requests 250 watched movies/page and 100 rows/page for watched shows/watchlists, and enforces `PULL_MAX_PAGES`.

## Diagnostics

Fresh pull detail includes:

```text
version
items
watchedMovies
watchedEpisodes
watchedNextUp
watchlistItems
source
```

Cache rows identify `memory` vs `sqlite`. Push retry IDs keep existing recovered/retrying grouping. Bulk duplicate suppression is logged as `ignored: covered_by_recent_bulk`.

## Deferred after v0.3.3

- dropped/undropped state
- active per-show Trakt progress fan-out for richer Next Up (not planned unless rate-limit economics change)
- anime/absolute-number episode mapping
- deeper metadata hydration for Trakt-only IDs
- release image workflow / v1.0 migration framework
