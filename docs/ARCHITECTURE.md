# Architecture — v0.3.7

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
                +-- pull identity mode
                |     trakt      -> preserve Trakt IMDb
                |     aiostreams -> learned AIOStreams IMDb rewrite
                |
                +-- sub-1% scrobble guard
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

v0.3.7 remains bidirectional for playback progress, watched/unwatched state, movie/show watchlist state and whole-season/show bulk marks. It keeps the v0.3.6 pull-identity model unchanged and adds a push-side guard for scrobble progress below 1%.

For HomeDocker production, AIOMetadata may still receive playback and fan out to secondary trackers, but it should not act as a second history authority when Trakt Bridge is the selected history source.

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

A Trakt 404 is treated as a provider-spelling miss. Authentication failures, `429`, and `5xx` are not swallowed.

## Learned IMDb aliases

A real title can legitimately have two IMDb IDs that resolve to the same stable Trakt show. AIOStreams cannot safely infer arbitrary IMDb A ↔ IMDb B equivalence.

v0.3.4 introduced a persistent alias model keyed by:

```text
profile + stable Trakt show id
```

The stored preference contains:

```text
preferredMetaId = IMDb spelling AIOStreams used for playback
traktImdb       = IMDb spelling returned by Trakt
revision        = monotonic alias-state revision
```

v0.3.5 corrected alias learning so a successful unfinished AIOStreams `stop` translated to Trakt `/scrobble/pause` can still teach the alias.

The alias record remains persisted in v0.3.7. A sub-1% stop ignored by v0.3.7 does not teach an alias because there is no successful upstream scrobble to serve as evidence.

## Why pull identity became configurable

Production Strand uses native Trakt and AIOStreams simultaneously. The dual-IMDb production title exposed this split:

```text
Native Trakt -> tt44051354
AIOStreams playback -> tt44094505
```

v0.3.4/v0.3.5 rewrote Trakt pull rows from `tt44051354` to learned `tt44094505`. Native Trakt inside Strand still saw `tt44051354`, so Strand could surface two Continue Watching cards.

Before v0.3.4, both paths preserved Trakt spelling and naturally converged.

v0.3.6 therefore added:

```env
PULL_IDENTITY_MODE=trakt      # default
PULL_IDENTITY_MODE=aiostreams # optional legacy v0.3.5 behavior
```

### `trakt` mode

- raw Trakt IMDb spelling is preserved for episode playback, watched-show and show-watchlist pull rows;
- learned aliases are still stored and can still be updated from successful AIOStreams stops;
- alias state is diagnostic/persistent evidence but does not rewrite pull identity;
- recommended when clients use native Trakt in parallel with AIOStreams.

### `aiostreams` mode

- pull rows are rewritten through learned aliases exactly as v0.3.5 intended;
- useful when AIOStreams is the only watch-state surface and local identity convergence is preferred.

No provider IDs are hard-coded.

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

v0.3.7 adds the upstream minimum-progress guard before media resolution/scrobble delivery:

```text
progress < 1%
  start / pause / unfinished stop -> ignored + 204
  explicit played stop           -> history-add

progress >= 1%
  existing scrobble mapping applies
```

This prevents deterministic Trakt `422` retry loops for sessions that open and immediately pause/stop at the beginning. Ignored events are marked processed, so a stable AIOStreams retry ID converges successfully.

Alias learning occurs only after the translated upstream request succeeds. A failed, rate-limited, or sub-1% ignored request does not commit a new preference.

### Bulk history

Whole-season/show marks contain explicit `videos[]`, `part`, and `parts`. Each part is validated and converted into one nested Trakt history request containing only those videos.

### Bulk → single reconciliation

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

The pull row pipeline remains:

```text
Trakt rows
   |
   +-- PULL_IDENTITY_MODE=trakt
   |      -> preserve rows
   |
   +-- PULL_IDENTITY_MODE=aiostreams
          -> rewrite show IMDb using learned alias
   |
   v
AIOStreams watch-state builders
```

`watched` and `watchlist` remain atomic authoritative blocks. An incomplete changed-state read fails rather than returning a destructive partial replacement.

## Version / migration gate

v0.3.7 does not change pull representation. It intentionally keeps the v0.3.6 schema salt:

```text
schema = watch-state-v0.3.6
```

The selected pull identity mode participates in the state-version basis. Switching `trakt` ↔ `aiostreams` therefore produces a new version even when Trakt activity timestamps did not change.

The one-time v0.3.5 → v0.3.6 migration removed persisted `pull-state:v5:<profile>` before the first authoritative v0.3.6 pull. Upgrading v0.3.6 → v0.3.7 requires no additional pull-cache purge.

Do not remove:

```text
profiles
OAuth tokens
identity-alias:v1:*
processed events
```

## Restart-safe rate-limit hardening

Each successful pull caches only:

```text
version
items
fetchedAt
```

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

## Next Up policy

The bridge remains conservative:

- forwards exact `next_episode` only when already supplied upstream;
- does not guess season boundaries;
- does not assume contiguous numbering;
- does not add one-request-per-show Trakt progress fan-out.

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

Successful alias learning can include:

```text
Trakt show id
preferred AIOStreams IMDb
Trakt IMDb spelling
previous preference
alias revision
```

In `trakt` mode these aliases remain visible diagnostics but do not alter pull identity. Sub-1% push retries are converted from repeated upstream `422` errors into an ignored/processed event.

## Deferred after v0.3.7

- dropped/undropped state
- active per-show Trakt progress fan-out for richer Next Up
- anime/absolute-number episode mapping
- deeper metadata hydration for Trakt-only IDs
- generalized non-IMDb duplicate-entity reconciliation
- release image workflow / v1.0 migration framework
