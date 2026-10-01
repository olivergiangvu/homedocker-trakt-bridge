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
- at least one connected Trakt profile

Inspect the JSON body for:

```text
database
schemaVersion
schemaExpected
connectedProfiles
status
```

## Trakt profile is disconnected

Open the profile dashboard and use **Reconnect Trakt**.

If OAuth cannot complete, verify:

- `PUBLIC_BASE_URL`
- Trakt app redirect URI
- reverse proxy HTTPS routing
- `TRAKT_CLIENT_ID`
- `TRAKT_CLIENT_SECRET`

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

## DB schema mismatch

The bridge fails closed if the DB schema is newer than the binary understands.

Do not force `PRAGMA user_version` manually.

Use the matching application version or restore the matching pre-upgrade DB backup.
