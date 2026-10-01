# Upstream contracts used by v0.3.7

Reviewed 2026-10-01.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.7 relies on these semantics:

- push event IDs are stable across retries;
- push bodies carry `metaId`, `videoId`, and shared show/film `ids` when known;
- `ids` describe the show or film, not an episode's own provider IDs;
- `metaId` and `videoId` may live in different provider spaces;
- missing `durationMs` means unknown, never zero;
- movie/show favourite changes are `watchlisted` / `unwatchlisted` when advertised;
- `watchState.push.bulk=true` enables whole-season/show played/unplayed bulk delivery;
- bulk `videos[]` is the authoritative set to write;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull supports `items`, authoritative `watched`, authoritative `watchlist`, and optional `version`;
- omitted authoritative blocks mean no new information while an explicit empty block is authoritative empty state;
- `watched.counts` is advisory and may be keyed by each ID a show answers to;
- AIOStreams sends the previous version back as `?since=`.

The bridge never substitutes empty watched/watchlist state for an upstream failure. Cached/stale responses intentionally contain only `version + items`.

## Identity compatibility boundary

AIOStreams supports multiple provider spellings. v0.3.3+ normalizes the shared conventional spaces used by the HomeDocker Trakt path:

```text
IMDb: tt...
TMDb: tmdb:<number>
TVDb: tvdb:<number>
```

A representable `metaId` can fill a missing provider field in `ids`. Explicit valid shared IDs remain authoritative.

Anime-oriented IDs such as `kitsu:`, `mal:`, `anilist:` and `anidb:` remain outside the standard-number bridge path because episode numbering may differ from Trakt season/episode numbering.

## IMDb-to-IMDb alias boundary

AIOStreams does not guarantee that two distinct valid IMDb IDs for the same real show collapse into one watch-state identity.

Production observed:

```text
AIOStreams playback metaId = tt44094505
Trakt same stable show      = tt44051354
TMDb                        = 276470
TVDb                        = 480791
```

### v0.3.4 alias model

v0.3.4 introduced a profile-scoped learned preference:

```text
stable Trakt show ID -> preferred AIOStreams IMDb metaId
```

Later Trakt pull rows for that show rewrote only the IMDb spelling before entering the AIOStreams payload.

### v0.3.5 stop-event correction

v0.3.5 allowed an unfinished AIOStreams episode `stop`, safely translated to `/scrobble/pause`, to teach/update the learned alias after successful upstream delivery.

### v0.3.6 pull-identity compatibility mode

Production Strand also uses native Trakt. Native Trakt preserves Trakt's IMDb while the v0.3.4/v0.3.5 bridge could rewrite the same title to AIOStreams' learned IMDb. That produced two valid identities in one client.

v0.3.6 therefore makes pull identity explicit:

```env
PULL_IDENTITY_MODE=trakt      # default
PULL_IDENTITY_MODE=aiostreams # v0.3.5 rewrite behavior
```

`trakt` mode:

- preserves raw Trakt IMDb spelling on pull;
- keeps learned aliases persisted for diagnostics/evidence;
- is recommended when native Trakt and AIOStreams are used in parallel.

`aiostreams` mode:

- rewrites show IMDb to the learned AIOStreams spelling for the same stable Trakt show;
- retains v0.3.5 convergence semantics for AIOStreams-only watch-state clients.

## Watched aliases and Next Up

AIOStreams' pull importer accepts watched counts keyed by each provider spelling. The bridge emits the same show count under every representable IMDb/TMDb/TVDb spelling present after the selected identity normalization.

The bridge does not fabricate `nextUp`. It forwards a next-up row only when the upstream watched row already contains a usable exact `next_episode`.

No one-request-per-show progress fan-out is used.

## Bulk mark compatibility boundary

Current AIOStreams behavior supports either bulk parts or per-video events for a sink depending on sink capability.

HomeDocker production observed later per-episode Jellyfin-compatible echoes after a successful bulk mark. v0.3.2+ suppresses only same-kind exact-video echoes inside a bounded event-time window.

## Trakt

Primary references:

- `trakt/trakt-api`
- Trakt Sync API reference

Current pull endpoints:

```text
/sync/last_activities
/sync/playback/movies
/sync/playback/episodes
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

The bridge follows Trakt pagination headers, requests 250 watched movies/page and 100 watched shows/watchlist rows/page, and enforces `PULL_MAX_PAGES`.

### Provider-ID resolution

Trakt search accepts IMDb, TMDb and TVDb IDs. The bridge tries known aliases in deterministic order.

A 404 is treated as a provider-spelling miss and may fall through to another known provider. Rate limiting, auth failures and server errors remain errors.

The stable Trakt show ID returned by resolution is the key used by the learned IMDb store.

## Scrobble semantics

The bridge preserves AIOStreams' stricter watched threshold:

```text
AIOStreams start                 -> /scrobble/start
AIOStreams pause                 -> /scrobble/pause
AIOStreams stop + played:false   -> /scrobble/pause
completed AIOStreams stop        -> /scrobble/stop
```

Production also confirmed Trakt rejects a scrobble at effectively zero progress with HTTP `422`. v0.3.7 therefore applies a minimum-progress guard before sending a scrobble:

```text
progress < 1%
  start / pause / unfinished stop -> ignore + 204
  explicit played stop           -> /sync/history add

progress >= 1%
  normal scrobble mapping applies
```

The 1% boundary is inclusive: exactly 1% remains eligible for scrobbling. The guard is local and creates no additional Trakt lookup.

Because ignored events return success and are entered into the bridge's processed-event idempotency store, AIOStreams retries for the same stable event ID stop after the next delivery instead of repeatedly receiving an upstream-derived `422`.

Alias learning is based on the original AIOStreams `event=stop`, not the translated Trakt path, and runs only after successful upstream delivery. A sub-1% ignored stop cannot teach an alias.

## Watchlist writes

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

Trakt assigns list timestamps server-side.

## Bulk history contract

Trakt Sync history accepts nested show/season/episode objects. The bridge resolves the parent show once, groups only explicit AIOStreams `videos[]`, and sends one request per bulk part:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

A bare show/season is never used.

## Rate limits and stale cache

Trakt can return `429` with `Retry-After`.

The bridge only substitutes a stale cached success when:

```text
caller since == cached version
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Authentication/reconnect failures remain errors.

## Upgrade behavior

v0.3.4 changed pull identity representation and used:

```text
state cursor schema: watch-state-v0.3.4
persisted pull cache: pull-state:v5:
```

v0.3.5 kept those identifiers.

v0.3.6 changes pull representation again and advances the state schema to:

```text
state cursor schema: watch-state-v0.3.6
```

`PULL_IDENTITY_MODE` participates in the state-version basis, so switching modes forces a fresh representation.

v0.3.7 is push-only and intentionally retains the v0.3.6 pull schema and cache behavior. Upgrading v0.3.6 → v0.3.7 requires no pull-cache purge.

For production upgrades directly from v0.3.5, the v0.3.6 one-time migration rule still applies: remove persisted `pull-state:v5:*` rows once before the first authoritative Trakt-preserving pull. Do not remove profile tokens or `identity-alias:v1:*` rows.

## Compatibility policy

v0.3.7 supports:

- playback scrobble push with a fail-safe 1% minimum-progress boundary;
- movie/episode watched push;
- standard-numbered season/show bulk marks;
- movie/show watchlist push + pull;
- watched + Continue Watching pull;
- bounded bulk/single duplicate reconciliation;
- IMDb/TMDb/TVDb identity fallback and alias-aware watched counts;
- persistent playback-learned IMDb-to-IMDb aliases keyed by stable Trakt show ID;
- alias learning from successful AIOStreams episode stops, including unfinished stops translated to Trakt pause when progress is scrobble-eligible;
- configurable Trakt-preserving or AIOStreams-preferred pull identity;
- safe next-up forwarding when Trakt already supplied the exact next episode.

Outside the advertised capability set:

- dropped state;
- anime/absolute-number mapping;
- guessed next-episode numbering;
- Trakt-only IDs without a representable metadata alias;
- guessed IMDb equivalence without successful playback evidence.
