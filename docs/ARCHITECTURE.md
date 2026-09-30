# Architecture — v0.1

```text
Jellyfin client
      |
      v
 AIOStreams
 watch_state v2
      |
      | POST /watch_state/push/...
      v
HomeDocker Trakt Bridge
      |
      +-- validate + idempotency
      +-- provider-ID -> Trakt media resolver/cache
      +-- OAuth token refresh (single-use refresh tokens)
      +-- AIOStreams 90% threshold guard
      |
      v
   Trakt API
```

## Authority and scope

v0.1 is deliberately **push-only**. It does not advertise `watchState.pull`, so AIOStreams will not try to import Trakt history yet.

Supported push events:

- `start`
- `pause`
- `stop`
- `played`
- `unplayed`

Bulk marks, watchlist, dropped state and Trakt -> AIOStreams pull are reserved for later releases.

## Important semantic guard

AIOStreams marks an item played at 90%. Trakt `/scrobble/stop` marks watched above 80%. Therefore a `stop` event with `played:false` is intentionally sent to Trakt as `/scrobble/pause`, not `/scrobble/stop`. This preserves AIOStreams' watched decision for progress in the 80–89% range.

## Episode resolution

AIOStreams sends the **show's** provider IDs plus `season` and `episode`; Trakt's current episode scrobble schema requires an episode ID. The bridge therefore:

1. resolves the show using `/search/{id_type}/{id}?type=show`;
2. resolves the episode using `/shows/{traktShowId}/seasons/{season}/episodes/{episode}`;
3. caches the resulting Trakt episode ID locally.

This is also why v0.1 does not guess episode IDs from titles.
