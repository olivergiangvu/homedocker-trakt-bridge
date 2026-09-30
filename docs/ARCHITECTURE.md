# Architecture — v0.3.5

```text
Infuse / Swiftfin / Strand / Odin / Jellyfin-compatible client
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
                +-- learned show alias store
                |     profile + Trakt show id -> preferred AIOStreams IMDb
                |
                +-- /scrobble/* -> playback transitions
                +-- /sync/history -> single + bulk watched state
                +-- recent bulk coverage -> suppress duplicate single echoes
                +-- /sync/watchlist/* -> movie/show watchlist
                +-- /sync/playback/* -> Continue Watching pull
                +-- /sync/watched/* -> authoritative watched pull
                +-- /sync/last_activities -> combined state/version gate
                |
                v
              Trakt API
```

## Authority

Trakt is the canonical long-term tracker source for watched history and watchlist state. AIOStreams is the Jellyfin-compatible playback/state surface.

v0.3.5 is bidirectional for playback progress, watched/unwatched state, movie/show watchlist state, and whole-season/show watched/unwatched bulk marks. It also converges multiple valid IMDb spellings for the same stable Trakt show using playback-confirmed evidence.

CrossWatch and Remux are not part of the critical path. AIOMetadata can independently supply metadata/watch-state to AIOStreams, but it is not a Trakt connection or Trakt authority.

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

`providerIdsForEvent()` merges a representable `metaId` with valid `event.ids`; explicit shared-vocabulary IDs remain authoritative while `metaId` fills omissions.

### Provider resolution

For movie/show resolution the bridge tries known aliases in deterministic order:

```text
IMDb -> TMDb -> TVDb (shows only for TVDb)
```

A Trakt 404 is treated as a provider-spelling miss, so the bridge may continue to another known provider spelling. Authentication failures, `429`, and `5xx` are not swallowed.

## Learned IMDb aliases

A real title can legitimately have two IMDb IDs that resolve to the same stable Trakt show. AIOStreams cannot safely infer arbitrary IMDb A ↔ IMDb B equivalence, so a local playback row under one IMDb ID and an imported tracker row under another can coexist as separate watch-state identities.

v0.3.4 introduced the persistent alias model. v0.3.5 corrects the event semantics used to learn it.

The stable key is:

```text
profile + Trakt show id
```

The stored preference contains:

```text
preferredMetaId = IMDb spelling AIOStreams actually used for playback
traktImdb       = IMDb spelling returned by Trakt for diagnostics
revision        = monotonic alias-state revision
```

The alias record is persisted in the existing SQLite-backed `media_cache` store with a long TTL.

### Why v0.3.5 changed the learning trigger

AIOStreams can emit:

```text
event = stop
played = false
```

For safety, the bridge translates that unfinished stop into:

```text
Trakt /scrobble/pause
```

v0.3.4 mistakenly tied alias learning to the translated Trakt action being `stop`. That excluded the normal unfinished Continue Watching case.

v0.3.5 binds identity evidence to the original AIOStreams event instead:

```text
AIOStreams episode stop
        |
        +-- unfinished -> Trakt pause
        |
        +-- finished   -> Trakt stop
        |
        v
upstream request succeeds
        |
        v
learn/update alias
```

A plain AIOStreams `pause` cannot teach or override an alias. Neither can `played`, `unplayed`, bulk-history, or watchlist operations.

## Pull aliases

Without a learned show preference, output remains deterministic:

```text
IMDb -> TMDb -> TVDb
```

When a learned alias exists for a Trakt show ID, pull rows rewrite only `show.ids.imdb` to the preferred AIOStreams IMDb before building playback, watched and watchlist state. Stable Trakt, TMDb and TVDb IDs remain intact.

A single watched episode list stays compact. AIOStreams permits `watched.counts` to be keyed by each representable show ID; after normalization the bridge emits advisory counts under the remaining representable identities.

Trakt-only IDs without a representable IMDb/TMDb/TVDb alias remain skipped rather than invented.

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

AIOStreams marks watched at 90%; Trakt `/scrobble/stop` can mark watched above 80%. Therefore:

```text
AIOStreams stop + played:false -> Trakt /scrobble/pause
AIOStreams completed stop      -> Trakt /scrobble/stop
```

Alias learning occurs only after the translated upstream request succeeds. A failed/429 request does not commit a new preference.

### Bulk history

Whole-season/show marks contain explicit `videos[]`, `part`, and `parts`. Each part is validated and converted into one nested Trakt history request containing only those videos.

The bridge never sends a bare show or season object that could affect episodes not listed by AIOStreams.

### Bulk → single reconciliation

Production observed valid bulk marks followed later by new per-episode Jellyfin-compatible calls. v0.3.2+ stores short-lived successful bulk coverage in SQLite.

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

Before builders run, episode-playback, watched-show and show-watchlist rows are rewritten through the profile's learned Trakt-show aliases. This introduces no extra per-show Trakt request.

`watched` and `watchlist` are treated atomically. An incomplete changed-state read fails rather than returning a destructive partial replacement.

## Version / migration gate

v0.3.5 intentionally retains the v0.3.4 pull-representation migration identifiers:

```text
schema = watch-state-v0.3.4
persisted safe pull cache = pull-state:v5:<profile>
```

This is deliberate. v0.3.5 fixes the trigger and cache invalidation semantics; it does not change the pull payload schema introduced by v0.3.4.

The state hash also includes `identityAliasVersion`, a deterministic representation of learned aliases plus a monotonic revision. Learning or changing an alias therefore changes the cursor even when Trakt watched/watchlist timestamps did not move.

Old `pull-state:v4:` rows remain ignored and expire naturally.

## Restart-safe rate-limit hardening

Each successful pull caches only:

```text
version
items
fetchedAt
```

The hot entry lives in memory and the same safe non-authoritative entry is persisted in SQLite.

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

### Alias-driven cache invalidation in v0.3.5

A learned alias is returned from push processing as `identityAlias`. `invalidatesPullCache()` treats any result carrying that field as authoritative cache invalidation.

This matters for unfinished stops:

```text
AIOStreams stop
  -> Trakt scrobble:pause
  -> alias learned
  -> pull cache cleared immediately
```

Therefore the next pull can return the normalized IMDb spelling without waiting for `PULL_TTL_SECONDS`.

Finished `scrobble:stop` continues to invalidate the pull cache through the normal mutation path as well.

## Next Up policy

AIOStreams computes the real Next Up episode locally from imported watched rows and metadata. Its Watch State pull contract can also accept `watched.nextUp` hints.

The bridge remains conservative:

- forwards exact `next_episode` only when already supplied upstream;
- does not guess season boundaries;
- does not assume contiguous numbering;
- does not add `/shows/:id/progress/watched` fan-out for hundreds of shows.

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

Cache rows identify memory vs SQLite. Push retry IDs keep recovered/retrying grouping. Bulk duplicate suppression is logged as `ignored: covered_by_recent_bulk`.

When a successful AIOStreams episode stop learns a preference, the push result can include:

```text
Trakt show id
preferred AIOStreams IMDb
Trakt IMDb spelling
previous preference, if any
alias revision
```

## Deferred after v0.3.5

- dropped/undropped state
- active per-show Trakt progress fan-out for richer Next Up unless rate-limit economics change
- anime/absolute-number episode mapping
- deeper metadata hydration for Trakt-only IDs
- generalized non-IMDb duplicate-entity reconciliation if a real production case requires it
- release image workflow / v1.0 migration framework
