# Upstream contracts used by v0.3.4

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.4 relies on these current semantics:

- push event IDs are stable across retries;
- push bodies carry `metaId`, `videoId`, and shared show/film `ids` when known;
- `ids` are the show's or film's IDs, never the episode's own IDs;
- `metaId` and `videoId` can live in different ID spaces;
- missing `durationMs` means unknown, never zero;
- movie/show favourite changes are `watchlisted` / `unwatchlisted` when advertised;
- `watchState.push.bulk=true` changes whole-season/show `played` / `unplayed` delivery into bulk requests for that dispatch;
- bulk `videos[]` is the authoritative set to write;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull supports `items`, authoritative `watched`, authoritative `watchlist`, and optional `version`;
- omitted authoritative blocks mean no new information while an explicit empty block is authoritative empty state;
- `watched.counts` is advisory and may be keyed by every ID the show answers to;
- `watched.nextUp` supplies one next-episode row per show; AIOStreams uses the show's type and activity time while deriving its own Next Up surface from metadata + watched state;
- AIOStreams sends the last version back as `?since=`;
- current on-demand pull freshness is controlled by global `WATCH_STATE_PULL_TTL`, not only the addon's `ttlSeconds` hint.

The bridge therefore never substitutes empty watched/watchlist state for an upstream failure. Cached and stale responses intentionally contain only `version + items`.

### Identity compatibility boundary

AIOStreams deliberately supports multiple provider spellings. v0.3.3+ normalizes the shared conventional spaces used by the HomeDocker Trakt path:

```text
IMDb: tt...
TMDb: tmdb:<number>
TVDb: tvdb:<number>
```

A representable `metaId` can fill a missing provider field in `ids`. This is safe because AIOStreams defines `metaId` as the meta the video belongs to. Explicit valid shared IDs still win when present.

Anime-oriented IDs such as `kitsu:`, `mal:`, `anilist:` and `anidb:` remain outside the standard-number bridge path because episode numbering can be absolute or otherwise differ from Trakt broadcast season/episode numbering.

### IMDb-to-IMDb alias boundary

AIOStreams' canonical matching currently helps across supported provider spaces but does not guarantee that two distinct IMDb IDs for the same real show collapse into one watch-state identity. Production observed one show where:

```text
AIOStreams playback metaId = IMDb A
Trakt same stable show      = IMDb B
```

Both IDs are valid, but without an explicit equivalence AIOStreams can retain two resumable rows.

v0.3.4 therefore learns a profile-scoped preference only from a successful episode playback `stop`: after the bridge resolves that stop to a stable Trakt show ID, it persists `Trakt show ID -> AIOStreams metaId`. Later Trakt pull rows for that stable show rewrite only the IMDb spelling before entering the AIOStreams watch-state payload.

This is intentionally evidence-based and does not infer equivalence merely from similar titles or episode numbers.

### Watched aliases and Next Up

AIOStreams' current pull importer accepts watched counts keyed by each ID spelling and uses its canonical matching layer to relate compatible metadata identities. The bridge emits the same show count under every representable IMDb/TMDb/TVDb alias present after normalization.

The bridge does **not** fabricate `nextUp`. It only forwards a next-up row when the upstream watched row already carries a usable `next_episode`. This keeps the bridge compatible with the schema while avoiding one Trakt progress request per show.

### Bulk mark compatibility boundary

Current AIOStreams source has two important properties:

1. `dispatchBulkMark()` chooses either bulk parts for a sink declaring `bulk=true` or one event per video for a non-bulk sink; one invocation does not deliberately emit both forms to the same sink.
2. The Jellyfin season/show `setPlayed()` path records local episode state then calls `reportBulkMark()` once and returns.

HomeDocker production nevertheless observed later new per-episode Jellyfin-compatible events after a successful bulk mark. v0.3.2+ treats those as a separate client/API reconciliation path and suppresses only same-kind exact-video echoes inside a bounded event-time window.

## Trakt

Primary references:

- `trakt/trakt-api`
- Trakt Sync API reference

### Watched endpoint behavior in 2026

Trakt's 2026 watched-endpoint changes require pagination for complete histories. The bridge explicitly follows pagination headers. `extended=progress` is used for `/sync/watched/shows` because season progress is no longer part of the default watched-show response.

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

The bridge requests 250 watched movies/page and 100 watched shows/watchlist rows/page, following the applied `X-Pagination-Page-Count` rather than assuming the requested limit was honored.

### Next episode

Trakt has a per-show `/shows/{id}/progress/watched` endpoint whose response can include `next_episode`. Calling that endpoint for hundreds of watched shows would create a large request fan-out and is intentionally **not** part of v0.3.4.

If a progress-shaped row already supplied to the bridge contains `next_episode`, it can be forwarded safely. Otherwise AIOStreams derives Next Up locally from the authoritative watched import and its own metadata.

### Provider-ID resolution

Trakt search accepts provider IDs such as IMDb, TMDb and TVDb. v0.3.3+ tries known aliases in deterministic order.

A search 404 is treated as a provider-spelling miss and may fall through to the next alias. This behavior is intentionally limited to 404. Rate limiting, auth failures and server errors are not treated as identity misses.

The stable Trakt show ID returned by that resolution is also the key used by v0.3.4's learned IMDb preference. This lets two valid IMDb spellings converge without another Trakt lookup on pull.

### Watchlist writes

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

Trakt assigns the list timestamp server-side; the bridge does not claim to preserve AIOStreams' event timestamp for watchlist add.

### Bulk history contract

Trakt Sync history accepts nested show/season/episode objects. The bridge resolves the parent show once, groups only AIOStreams' explicit `videos[]`, and sends one request per bulk part:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

A bare show/season is deliberately never used because it could affect episodes AIOStreams did not list.

Because history add accepts `watched_at`, a later semantically duplicate played event can become another history entry rather than a harmless transport retry. That is why recently bulk-covered same-kind single episode echoes are filtered before reaching Trakt.

### Rate limits and stale cache

Trakt can return `429` with `Retry-After`. The bridge only substitutes a stale cached success when:

```text
caller since == cached version
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Authentication/reconnect failures remain errors.

Alias learning is performed only after a successful episode stop request. A `429` or other failed stop does not persist a new preferred IMDb spelling.

## Upgrade behavior

v0.3.4 changes pull identity behavior by adding learned IMDb-to-IMDb reconciliation. Migration guards ensure AIOStreams cannot receive a still-fresh pre-alias payload after upgrade:

```text
state cursor schema: watch-state-v0.3.4
identity alias revision: included in state hash
persisted pull cache: pull-state:v5:
```

The old v0.3.3 `pull-state:v4:` safe-cache rows are ignored and expire under their existing TTL.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity and authoritative-state mapping are fail-closed where semantics are uncertain.

v0.3.4 supports:

- playback scrobble push;
- standard movie/episode watched push;
- standard-numbered season/show bulk marks;
- movie/show watchlist push + pull;
- watched + Continue Watching pull;
- bounded bulk/single duplicate reconciliation;
- IMDb/TMDb/TVDb identity fallback and alias-aware watched counts;
- persistent playback-learned IMDb-to-IMDb show reconciliation keyed by Trakt show ID;
- safe next-up forwarding when Trakt already supplied the exact next episode.

Still outside the advertised capability set:

- dropped state;
- anime/absolute-number mapping;
- guessed next-episode numbering;
- Trakt-only IDs without a representable metadata alias;
- guessed IMDb equivalence without successful playback evidence.
