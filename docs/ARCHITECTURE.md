# Architecture — v0.2

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
        |           |
        |           +-- /sync/playback/* -> Continue Watching
        |           +-- /sync/watched/*  -> watched history
        |           +-- /sync/last_activities -> watched version gate
        |
        +-- validate + idempotency
        +-- provider-ID -> Trakt resolver/cache
        +-- OAuth token refresh
        +-- 90% AIOStreams watched-threshold guard
        +-- safe pagination + fail-closed pull
        |
        v
      Trakt API
```

## Authority

Trakt is the canonical long-term watched-history source. AIOStreams is the playback/state surface for Jellyfin-compatible clients.

v0.2 is bidirectional:

- **Push:** AIOStreams playback and watched changes are written to Trakt.
- **Pull:** Trakt playback progress and watched history are read back into AIOStreams.

CrossWatch and Remux are not part of the critical path for this bridge.

## Push semantics

Supported events:

- `start`
- `pause`
- `stop`
- `played`
- `unplayed`

AIOStreams marks watched at 90%; Trakt `/scrobble/stop` can mark watched above 80%. Therefore `stop + played:false` is deliberately mapped to `/scrobble/pause` so progress between 80% and 89% cannot become watched accidentally.

## Pull semantics

The manifest advertises:

```json
{
  "pull": {
    "items": true,
    "watched": true,
    "ttlSeconds": 300
  }
}
```

### Continue Watching

The bridge reads:

- `/sync/playback/movies?extended=full`
- `/sync/playback/episodes?extended=full`

It returns standard AIOStreams IDs, preferring IMDb and falling back to `tmdb:` then `tvdb:`. When Trakt supplies runtime, the bridge returns both `durationMs` and calculated `positionMs`; otherwise it still returns `progressPercent` for AIOStreams to combine with an existing local duration.

### Watched history

The bridge reads:

- `/sync/watched/movies`
- `/sync/watched/shows?extended=progress`

Movie IDs are returned directly in the chosen AIOStreams ID space. Show season/episode progress is converted to video IDs such as `tt0903747:3:10`.

The watched block is authoritative. If Trakt returns an incomplete show-progress payload, the bridge returns an error instead of an empty history, preventing AIOStreams from deleting previously imported watched state.

### Version / since gate

`/sync/last_activities` is reduced to a stable watched-history version based on movie and episode watched timestamps. AIOStreams sends the previous version as `?since=...`.

- playback `items` are refreshed on every pull;
- if `since` matches, the bridge omits `watched`;
- if watched activity changed, the bridge performs the paginated watched-history read and returns the new authoritative block.

## Pagination

Current Trakt watched endpoints are paginated. The bridge follows `X-Pagination-Page-Count`, requests 250 movies/page and 100 shows/page for `extended=progress`, and fails if pagination exceeds the configured safety cap.

## Deferred to v0.3+

- Trakt watchlist
- dropped/undropped state
- AIOStreams bulk marks
- richer next-up generation
- anime/absolute-number episode mapping
