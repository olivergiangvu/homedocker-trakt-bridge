# Upstream contracts used by v0.3.5

Reviewed 2026-09-30.

## AIOStreams

Primary reference:

- `Viren070/AIOStreams/packages/docs/content/docs/reference/addon-protocol/watch-state.mdx`
- current protocol: `watchState.version = 2`

v0.3.5 relies on these current semantics:

- push event IDs are stable across retries;
- push bodies carry `metaId`, `videoId`, and shared show/film `ids` when known;
- `ids` describe the show or film, not an episode's own provider IDs;
- `metaId` and `videoId` may live in different provider spaces;
- missing `durationMs` means unknown, never zero;
- movie/show favourite changes are `watchlisted` / `unwatchlisted` when advertised;
- `watchState.push.bulk=true` changes whole-season/show `played` / `unplayed` delivery into bulk requests for that dispatch;
- bulk `videos[]` is the authoritative set to write;
- AIOStreams treats `200/204` as delivered, `401/403` as reconnect-required and `429/5xx` as retryable;
- pull supports `items`, authoritative `watched`, authoritative `watchlist`, and optional `version`;
- omitted authoritative blocks mean no new information while an explicit empty block is authoritative empty state;
- `watched.counts` is advisory and may be keyed by each ID a show answers to;
- `watched.nextUp` may supply one next-episode row per show while AIOStreams derives its actual Next Up surface from metadata + watched state;
- AIOStreams sends the previous version back as `?since=`;
- current on-demand pull freshness is controlled by global `WATCH_STATE_PULL_TTL`, not only the addon's `ttlSeconds` hint.

The bridge therefore never substitutes empty watched/watchlist state for an upstream failure. Cached/stale responses intentionally contain only `version + items`.

## Identity compatibility boundary

AIOStreams supports multiple provider spellings. v0.3.3+ normalizes the shared conventional spaces used by the HomeDocker Trakt path:

```text
IMDb: tt...
TMDb: tmdb:<number>
TVDb: tvdb:<number>
```

A representable `metaId` can fill a missing provider field in `ids`. Explicit valid shared IDs remain authoritative.

Anime-oriented IDs such as `kitsu:`, `mal:`, `anilist:` and `anidb:` remain outside the standard-number bridge path because episode numbering can be absolute or otherwise differ from Trakt season/episode numbering.

## IMDb-to-IMDb alias boundary

AIOStreams' canonical matching helps across supported provider spaces but does not guarantee that two distinct valid IMDb IDs for the same real show collapse into one watch-state identity.

Production observed exactly this pattern:

```text
AIOStreams playback metaId = IMDb A
Trakt same stable show      = IMDb B
```

Without explicit equivalence, AIOStreams can retain two resumable rows.

### v0.3.4 alias model

v0.3.4 introduced a profile-scoped learned preference:

```text
stable Trakt show ID -> preferred AIOStreams IMDb metaId
```

Later Trakt pull rows for that same stable show rewrite only the IMDb spelling before entering the AIOStreams watch-state payload.

This is evidence-based and does not infer equivalence from title similarity, episode numbers or progress similarity alone.

### v0.3.5 stop-event correction

AIOStreams can send an unfinished episode stop:

```text
event = stop
played = false
```

The bridge intentionally translates it to Trakt:

```text
/scrobble/pause
```

v0.3.4 tied learning to the translated action being `stop`, so an unfinished Continue Watching stop could not teach the alias.

v0.3.5 instead treats the **original successful AIOStreams episode `stop` event** as the identity evidence. After the translated upstream request succeeds:

- a finished stop translated to `/scrobble/stop` may learn/update the alias;
- an unfinished stop translated to `/scrobble/pause` may also learn/update the alias;
- a plain AIOStreams `pause` cannot learn or override an alias;
- `played`, `unplayed`, bulk and watchlist operations cannot learn or override an alias.

A failed or `429` stop does not persist a new preference.

## Watched aliases and Next Up

AIOStreams' pull importer accepts watched counts keyed by each provider spelling and uses its own canonical matching layer for compatible metadata identities. The bridge emits the same show count under every representable IMDb/TMDb/TVDb spelling present after normalization.

The bridge does **not** fabricate `nextUp`. It forwards a next-up row only when the upstream watched row already contains a usable exact `next_episode`.

No one-request-per-show progress fan-out is used.

## Bulk mark compatibility boundary

Current AIOStreams source has two important properties:

1. `dispatchBulkMark()` chooses either bulk parts for a sink declaring `bulk=true` or one event per video for a non-bulk sink; one invocation does not deliberately emit both forms to the same sink.
2. The Jellyfin season/show `setPlayed()` path records local episode state then calls `reportBulkMark()` once and returns.

HomeDocker production nevertheless observed later new per-episode Jellyfin-compatible events after a successful bulk mark. v0.3.2+ treats those as a separate client/API reconciliation path and suppresses only same-kind exact-video echoes inside a bounded event-time window.

## Trakt

Primary references:

- `trakt/trakt-api`
- Trakt Sync API reference

### Watched endpoint behavior in 2026

Trakt's current watched endpoints require pagination for complete large histories. The bridge follows pagination headers. `extended=progress` is used for `/sync/watched/shows`.

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

The bridge requests 250 watched movies/page and 100 watched shows/watchlist rows/page, following the applied `X-Pagination-Page-Count` instead of assuming the requested limit was honored.

### Next episode

Trakt has a per-show `/shows/{id}/progress/watched` endpoint whose response may include `next_episode`. Calling it once for hundreds of watched shows would create a large API fan-out and is intentionally **not** part of v0.3.5.

If a progress-shaped row already supplied to the bridge contains `next_episode`, it can be forwarded. Otherwise AIOStreams derives Next Up locally.

### Provider-ID resolution

Trakt search accepts provider IDs such as IMDb, TMDb and TVDb. v0.3.3+ tries known aliases in deterministic order.

A 404 is treated as a provider-spelling miss and may fall through to the next known provider. Rate limiting, auth failures and server errors remain real errors.

The stable Trakt show ID returned by this resolution is also the key used by the learned IMDb preference introduced in v0.3.4 and retained by v0.3.5. This lets two valid IMDb spellings converge without another Trakt lookup on pull.

## Scrobble semantics relevant to v0.3.5

The bridge preserves AIOStreams' stricter watched threshold by mapping unfinished stops safely:

```text
AIOStreams start                 -> /scrobble/start
AIOStreams pause                 -> /scrobble/pause
AIOStreams stop + played:false   -> /scrobble/pause
completed AIOStreams stop        -> /scrobble/stop
```

The alias-learning gate is intentionally based on the original AIOStreams `event=stop`, not the translated path. It still runs only **after** the translated request succeeds.

This distinction is required because `/scrobble/pause` can represent either:

- a real AIOStreams `pause` — no alias learning; or
- an unfinished AIOStreams `stop` — valid alias-learning evidence after successful delivery.

## Watchlist writes

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

Trakt assigns the list timestamp server-side; the bridge does not claim to preserve the AIOStreams event timestamp when adding to the watchlist.

## Bulk history contract

Trakt Sync history accepts nested show/season/episode objects. The bridge resolves the parent show once, groups only AIOStreams' explicit `videos[]`, and sends one request per bulk part:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

A bare show/season is deliberately never used because it could affect episodes AIOStreams did not list.

Because history add accepts `watched_at`, a later semantically duplicate played event can become another history entry rather than a harmless transport retry. Recently bulk-covered same-kind single episode echoes are therefore filtered before reaching Trakt.

## Rate limits and stale cache

Trakt can return `429` with `Retry-After`.

The bridge only substitutes a stale cached success when:

```text
caller since == cached version
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Authentication/reconnect failures remain errors.

Alias learning is performed only after the relevant translated scrobble request succeeds. A `429` or other failure does not persist a new alias.

## Upgrade behavior

v0.3.4 changed the pull identity representation by adding learned IMDb-to-IMDb reconciliation, so it advanced migration guards to:

```text
state cursor schema: watch-state-v0.3.4
identity alias revision: included in state hash
persisted pull cache: pull-state:v5:
```

v0.3.5 intentionally **keeps those identifiers unchanged**. The package release fixes event-trigger/cache-invalidation semantics without introducing a new pull payload schema.

When an alias is learned or changed, its revision changes the state cursor. v0.3.5 also invalidates the bridge pull cache immediately whenever the successful push result contains `identityAlias`, including an unfinished stop translated to `scrobble:pause`.

## Compatibility policy

The bridge advertises only capabilities it implements. Identity and authoritative-state mapping are fail-closed where semantics are uncertain.

v0.3.5 supports:

- playback scrobble push;
- standard movie/episode watched push;
- standard-numbered season/show bulk marks;
- movie/show watchlist push + pull;
- watched + Continue Watching pull;
- bounded bulk/single duplicate reconciliation;
- IMDb/TMDb/TVDb identity fallback and alias-aware watched counts;
- persistent playback-learned IMDb-to-IMDb show reconciliation keyed by stable Trakt show ID;
- alias learning from successful AIOStreams episode stops even when an unfinished stop is safely translated to Trakt pause;
- immediate pull-cache invalidation when alias state changes;
- safe next-up forwarding when Trakt already supplied the exact next episode.

Still outside the advertised capability set:

- dropped state;
- anime/absolute-number mapping;
- guessed next-episode numbering;
- Trakt-only IDs without a representable metadata alias;
- guessed IMDb equivalence without successful playback evidence.
