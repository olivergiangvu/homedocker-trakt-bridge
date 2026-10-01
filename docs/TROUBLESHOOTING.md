# Troubleshooting

This guide focuses on production symptoms rather than implementation internals.

## Dashboard shows `Attention`

The dashboard only treats **recent active errors** as operational attention.

Historical failures remain in the database for diagnostics but should not keep the service unhealthy indefinitely.

Check:

```text
Active errors · 30m
Latest poll
Last authoritative sync
Recent events -> Errors
```

Then inspect raw detail only if needed.

## Continue Watching shows duplicate cards

First determine whether the duplicate exists in AIOStreams state or only in the client.

### 1. Check AIOStreams watch state

If two different provider identities exist for the same title, identify which sink created each row.

HomeDocker production should have one external watched/resume read authority:

```text
Trakt -> Trakt Bridge -> AIOStreams
```

AIOMetadata should use:

```text
Trackers = This server only
```

### 2. Check pull identity mode

Recommended:

```env
PULL_IDENTITY_MODE=trakt
```

This is required when clients may read native Trakt and AIOStreams in parallel.

### 3. Check the client only after server state is clean

If AIOStreams contains one identity but the client still shows two cards, the remaining issue is client-side cached state.

Do not delete AIOStreams state blindly to fix a client cache symptom.

The historical dual-IMDb case is documented in [DEVELOPMENT.md](DEVELOPMENT.md).

## Trakt returns HTTP 422 for `/scrobble/pause`

AIOStreams can emit pause/unfinished-stop events at `0%` progress.

Trakt rejects sub-1% scrobbles.

Since v0.3.7 the bridge handles this locally:

```text
progress < 1%
  start / pause / unfinished stop -> ignored + acknowledged
  explicit played stop           -> history add
```

If you see old `422` rows in event history, check their timestamp. They may be historical rows retained from before the guard was deployed.

## Watched movie/episode counts show `—`

A cache-only poll does not necessarily include the full watched/watchlist payload.

The dashboard separates:

```text
Latest poll             = most recent pull request, including cache responses
Last authoritative sync = most recent full pull carrying watched/watchlist state
```

Use the authoritative counts for health interpretation.

## `/health` works but `/readiness` returns 503

`/health` only proves the process is alive.

`/readiness` also requires:

- working DB access
- supported schema
- at least one profile with usable Trakt credentials

Inspect the JSON body for:

```text
database
schemaVersion
schemaExpected
connectedProfiles
status
```

If `status=setup_required` after the service had previously been connected, inspect authenticated `/status` for:

```text
profile.connectionState
profile.reconnectRequired
```

## Trakt profile requires reconnect

v0.5.0 treats Trakt OAuth `invalid_grant` as a credential-state transition rather than a generic retryable failure.

Expected behavior:

```text
invalid_grant
  -> unusable local OAuth tokens are cleared
  -> profile.connectionState = reconnect_required
  -> profile.reconnectRequired = true
  -> /readiness = setup_required
```

Open the profile dashboard and complete **Connect/Reconnect Trakt**. A successful OAuth callback stores fresh tokens and returns the profile state to `connected`.

Do not restore the rejected token values from an old `.env` or manual DB edit. OAuth tokens live in `bridge.db` and are encrypted with `BRIDGE_SECRET_KEY`.

If OAuth cannot complete, verify:

- `PUBLIC_BASE_URL`
- Trakt app redirect URI
- reverse proxy HTTPS routing
- `TRAKT_CLIENT_ID`
- `TRAKT_CLIENT_SECRET`

## Trakt profile is manually disconnected

A manual disconnect is different from `reconnect_required`:

```text
profile.connectionState = disconnected
profile.reconnectRequired = false
```

Use the profile setup page to connect again when desired.

## Manifest does not work in AIOStreams

Use the **Manifest URL** displayed on the profile dashboard.

Do not manually reconstruct it.

Verify:

- profile still exists
- bridge secret has not changed unexpectedly
- public HTTPS URL is reachable
- manifest URL has not been truncated when copied

The manifest URL is a credential. If it is exposed, rotate the relevant bridge secret/profile credentials rather than publishing the URL in logs or screenshots.

## Pulls are repeatedly served from stale cache

Stale cache is only used for bounded fallback on transient Trakt failures such as rate limiting or upstream 5xx.

Inspect Recent Events -> Pull and the raw detail for:

```text
source
cacheLayer
ageSeconds
upstreamError
retryAfter
```

Persistent stale fallback indicates the upstream issue is not recovering.

Authentication/reconnect failures do not use stale fallback as a substitute for valid credentials.

## DB schema mismatch

The bridge fails closed if the DB schema is newer than the binary understands.

Do not force `PRAGMA user_version` manually.

Use the matching application version or restore the matching pre-upgrade DB backup.
