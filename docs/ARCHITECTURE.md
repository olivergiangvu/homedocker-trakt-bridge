# Architecture — v0.3.4

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
        +-- learned show alias store
        |     profile + Trakt show id -> preferred AIOStreams IMDb
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

v0.3.4 is bidirectional for playback progress, watched/unwatched state, movie/show watchlist state, and whole-season/show watched/unwatched bulk marks. It also remembers which IMDb spelling AIOStreams actually used for successful episode playback when Trakt returns another IMDb alias for the same stable Trakt show.

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

A Trakt search 404 means only that provider spelling missed, so v0.3.3+ continues to the next known alias. Authentication failures, `429`, and `5xx` are not swallowed and keep their existing retry/reconnect semantics.

### Learned IMDb aliases

A real title can have two IMDb IDs that both resolve to the same stable Trakt show. AIOStreams cannot currently infer that two different `tt...` values are aliases, so a local playback row under IMDb A and a Trakt pull row under IMDb B can appear as two resumable episodes.

v0.3.4 learns only from a successful episode playback stop:

```text
AIOStreams stop metaId = IMDb A
        |
        v
Bridge resolves Trakt show X
Trakt show X reports IMDb B
        |
        v
persist profile + X -> preferredMetaId IMDb A
```

The alias record also remembers Trakt's IMDb spelling for diagnostics. It lives in the existing SQLite-backed `media_cache` store with a long TTL.

Learning is deliberately excluded from `played`, `unplayed`, bulk-history and watchlist operations. Those library-level operations may arrive through a different metadata spelling and must not override the identity confirmed by real playback.

### Pull aliases

Without a learned show preference, the preferred output spelling remains:

```text
IMDb -> TMDb -> TVDb
```

When a learned alias exists for the Trakt show ID, pull rows rewrite only `show.ids.imdb` to the AIOStreams-preferred IMDb before building playback, watched and watchlist state. Trakt/TMDb/TVDb IDs remain unchanged.

A single watched episode list therefore stays compact and deterministic. AIOStreams explicitly permits `watched.counts` to be keyed by every ID a show answers to; after normalization v0.3.4 emits the advisory count under every representable alias still present on the row.

Trakt-only IDs without a representable IMDb/TMDb/TVDb alias are still skipped rather than invented.

## Next Up policy

AIOStreams computes the real Next Up episode locally from imported watched rows and metadata. Its Watch State pull contract also allows `watched.nextUp` hints.

The bridge remains deliberately conservative:

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

A successful episode `stop` is also the only event allowed to teach or change a learned IMDb alias. Learning occurs after Trakt accepts the scrobble request; a failed/429 stop does not commit a new preference.

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

Before the builders run, episode-playback, watched-show and show-watchlist rows are rewritten through the profile's learned Trakt-show aliases. No extra Trakt call is made for this normalization.

`watched` and `watchlist` are treated atomically. An incomplete changed-state read fails rather than returning a destructive partial replacement.

### Version / migration gate

The state hash includes:

```text
schema = watch-state-v0.3.4
identityAliasVersion
movies.watched_at
episodes.watched_at
movies.watchlisted_at
shows.watchlisted_at
```

The alias version is a deterministic representation of the profile's learned show aliases plus a monotonic revision. Learning or changing a preference therefore changes the cursor even when Trakt watched/watchlist activity timestamps did not move.

Persisted safe pull cache moves to:

```text
pull-state:v5:<profile>
```

Old v0.3.3 `pull-state:v4:` rows are ignored and expire naturally. Together with the new schema salt, this guarantees the first v0.3.4 pull cannot return an old pre-alias cached shape.

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

Successful `scrobble:stop` already invalidates the pull cache. Because alias learning happens only after that stop succeeds, the next pull sees both the new alias state and the changed v0.3.4 cursor immediately.

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

When a successful stop learns a preference, the push result includes:

```text
Trakt show id
preferred AIOStreams IMDb
Trakt IMDb spelling
previous preference, if any
alias revision
```

## Deferred after v0.3.4

- dropped/undropped state
- active per-show Trakt progress fan-out for richer Next Up (not planned unless rate-limit economics change)
- anime/absolute-number episode mapping
- deeper metadata hydration for Trakt-only IDs
- generalized non-IMDb duplicate-entity reconciliation if a real production case requires it
- release image workflow / v1.0 migration framework
