# Upstream contracts used by v0.4.0

Reviewed 2026-10-01.

v0.4.0 keeps the functional watch-state behavior of v0.3.7 and adds production lifecycle hardening. This document separates the contracts the bridge depends on from HomeDocker-specific authority choices.

## AIOStreams

Primary protocol reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current bridge contract: `watchState.version = 2`

The bridge relies on these semantics:

- push event IDs are stable across retries;
- push bodies can carry `metaId`, `videoId` and shared provider `ids`;
- shared `ids` describe the show/film identity, not an episode-specific provider namespace;
- missing `durationMs` means unknown, not zero;
- movie/show favourites are `watchlisted` / `unwatchlisted` when advertised;
- `watchState.push.bulk=true` enables whole-season/show played/unplayed delivery;
- bulk `videos[]` is the authoritative changed set;
- `200/204` is delivery success;
- `401/403` means reconnect/credential failure;
- `429/5xx` is retryable;
- pull supports `items`, authoritative `watched`, authoritative `watchlist`, and optional `version`;
- omitted authoritative blocks mean no replacement information;
- explicit empty authoritative blocks mean authoritative empty state;
- AIOStreams returns the previous pull version as `?since=`.

The bridge never substitutes an empty watched/watchlist block for an upstream failure. Cached and stale responses intentionally contain only `version + items`.

## AIOStreams identity compatibility

Supported conventional shared spaces:

```text
IMDb: tt...
TMDb: tmdb:<number>
TVDb: tvdb:<number>
```

A representable `metaId` may fill a provider field omitted from `ids`; explicit valid shared IDs remain authoritative.

Anime-oriented IDs such as `kitsu:`, `mal:`, `anilist:` and `anidb:` remain outside the standard-number bridge path because episode numbering may not match Trakt season/episode numbering.

### Dual-IMDb production boundary

Production observed one stable Trakt show represented by two valid IMDb spellings:

```text
AIOStreams playback metaId = tt44094505
Trakt same stable show      = tt44051354
TMDb                        = 276470
TVDb                        = 480791
```

v0.3.4 introduced playback-learned alias evidence. v0.3.6 made pull representation configurable. v0.4.0 retains:

```env
PULL_IDENTITY_MODE=trakt      # default / HomeDocker production
PULL_IDENTITY_MODE=aiostreams # learned-alias rewrite mode
```

`trakt` preserves Trakt IMDb spelling so a client can use native Trakt and AIOStreams simultaneously without being handed two different IMDb identities for the same state.

`aiostreams` rewrites the Trakt spelling to the learned AIOStreams-preferred spelling for AIOStreams-only convergence.

## AIOMetadata Jellyfin API

Primary upstream implementation/documentation reviewed:

- `cedya77/aiometadata/docs/jellyfin.md`
- `cedya77/aiometadata/addon/lib/jellyfin/trackerSource.ts`

AIOMetadata deliberately separates **what is written to trackers** from **what its Jellyfin surface reads back**.

### Local playstate

Playback through AIOMetadata's Jellyfin API is recorded in its local `jellyfin_playstate` table. Continue Watching and watched state can therefore exist independently of tracker readback.

### Tracker writes

When Watch Tracking is enabled for a connected service and the configured record policy allows the event, playback reported through a supported server can still be written/fanned out to those tracker accounts.

The user-card **Trackers** picker does not disable those writes.

### Tracker reads

The picker decides what AIOMetadata reads back into its Jellyfin Continue Watching / watched ticks / Next Up surface.

Upstream behavior includes:

```text
Automatic        -> external tracker readback according to AIOMetadata policy
One tracker      -> read that tracker
This server only -> read no external tracker history; use local server playstate
```

In current upstream code, `This server only` is represented by the `off` resume-source choice; it makes both the chosen source and resume-source list empty.

### HomeDocker authority requirement

When Trakt Bridge is the canonical history source for AIOStreams, HomeDocker should use:

```text
AIOMetadata Jellyfin user
Trackers = This server only
```

This prevents Simkl/MDBList/PublicMetaDB/AniList/MAL history from being re-imported as a second Jellyfin watch-state authority while preserving enabled AIOMetadata tracker writes/fan-out.

Watchlist selection is a separate AIOMetadata picker and may remain independently configured if desired.

## Trakt

Primary references:

- `trakt/trakt-api`
- Trakt Sync API

Fresh pull endpoints used by the bridge:

```text
/sync/last_activities
/sync/playback/movies
/sync/playback/episodes
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

The bridge follows Trakt pagination headers and enforces `PULL_MAX_PAGES` as a fail-safe cap.

### Provider resolution

Known IMDb/TMDb/TVDb spellings are attempted deterministically. A Trakt 404 may fall through to another known provider spelling. Authentication failure, rate limiting and server failure remain errors.

The stable Trakt show ID returned by successful resolution is the key for learned IMDb alias evidence.

## Trakt scrobble semantics

The bridge preserves AIOStreams' stricter played decision:

```text
AIOStreams start               -> /scrobble/start
AIOStreams pause               -> /scrobble/pause
AIOStreams stop + played:false -> /scrobble/pause
completed AIOStreams stop      -> /scrobble/stop
```

Production confirmed Trakt rejects effectively zero-progress scrobbles with HTTP `422`. Since v0.3.7:

```text
progress < 1%
  start / pause / unfinished stop -> ignore + 204
  explicit played stop           -> /sync/history add

progress >= 1%
  normal scrobble mapping applies
```

Exactly 1% remains eligible for scrobbling.

Ignored sub-1% events are marked processed so stable AIOStreams retries converge without repeatedly reaching Trakt.

## Watched, bulk and watchlist contracts

Single watched changes map to Trakt history add/remove.

Whole-season/show bulk events resolve the parent show once and send only explicit AIOStreams `videos[]`; a bare show/season is never treated as all episodes.

A later per-episode echo is suppressed only when profile, event kind, exact video, event order and configured dedupe window all match.

Watchlist writes:

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

## Pull cache and failure policy

A cached success can answer only when:

```text
caller since == cached version
cache age < PULL_TTL_SECONDS
```

A stale cached success can answer only when:

```text
caller since == cached version
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Authentication/reconnect failures are never hidden by stale cache.

## Pull representation / upgrade policy

v0.4.0 intentionally retains the v0.3.6/v0.3.7 watch-state representation:

```text
state cursor schema: watch-state-v0.3.6
persisted pull cache: pull-state:v5:<profile>
```

`PULL_IDENTITY_MODE` participates in the version basis; switching modes forces a fresh authoritative representation.

No pull-cache purge is required for v0.3.7 -> v0.4.0.

## Bridge database upgrade contract — new in v0.4.0

Bridge-local SQLite schema lifecycle is now independent of the AIOStreams pull representation.

```text
v0.3.x DB: PRAGMA user_version=0
v0.4.0 DB: PRAGMA user_version=1
```

Migration 1 preserves existing tables/data, creates any missing baseline objects and marks the DB schema version. A DB newer than the running binary fails closed.

This migration does not delete:

```text
profiles
OAuth tokens
processed events
identity-alias:v1:*
pull-state:v5:*
event history
```

## Operational endpoint contract — new in v0.4.0

```text
GET /health
  liveness only

GET /readiness
  DB + supported schema + connected-profile readiness
  200 ready
  503 setup_required otherwise

GET /status?key=<ADMIN_KEY>
  authenticated safe operator diagnostics
```

Readiness intentionally does not make a live Trakt request.

## Compatibility policy

v0.4.0 supports:

- playback scrobble push with sub-1% fail-safe guard;
- movie/episode watched push;
- standard-numbered season/show bulk marks;
- movie/show watchlist push + pull;
- watched + Continue Watching pull;
- bounded duplicate reconciliation;
- IMDb/TMDb/TVDb fallback;
- learned IMDb-to-IMDb evidence keyed by stable Trakt show ID;
- configurable Trakt-preserving/AIOStreams-preferred pull identity;
- safe Next Up forwarding only when an exact next episode already exists upstream;
- explicit DB migration/readiness/status lifecycle around the existing protocol.

Still outside the advertised capability set:

- dropped state;
- anime/absolute-number episode translation;
- guessed next episode numbering;
- guessed IMDb equivalence without successful playback evidence;
- generalized non-IMDb duplicate reconciliation.
