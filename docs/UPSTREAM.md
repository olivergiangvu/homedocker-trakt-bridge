# Upstream contracts used by v0.1

Reviewed 2026-09-30.

## AIOStreams

- Watch State reference: https://docs.aiostreams.viren070.me/reference/addon-protocol/watch-state/
- Contract version: `watchState.version = 2`
- v0.1 relies on stable event IDs across retries, `positionMs`/`durationMs`, the `played` decision, and AIOStreams' documented response handling (`2xx`, `401/403`, `429/5xx`).
- Current docs state AIOStreams uses a 90% watched threshold for the `played` field.

## Trakt

- Developer portal: https://developer.trakt.tv/
- Official API source: https://github.com/trakt/trakt-api
- OAuth Authorization Code flow uses `https://auth.trakt.tv/oauth/authorize` and `https://auth.trakt.tv/oauth/token`.
- Access tokens are currently documented as 7-day tokens; refresh tokens are single-use and must be replaced after a successful refresh.
- Current scrobble schema requires movie details (`title`, `year`, IDs) or an episode ID. AIOStreams sends show IDs for episode playback, so the bridge resolves the Trakt episode ID before scrobbling.
- Trakt `/scrobble/stop` marks watched above 80%, which differs from AIOStreams' 90% threshold. The bridge intentionally maps AIOStreams `stop + played:false` to Trakt `pause`.

## Compatibility policy

The bridge advertises only capabilities it implements. If an upstream contract changes incompatibly, fail closed rather than guessing media identity or watched state.
