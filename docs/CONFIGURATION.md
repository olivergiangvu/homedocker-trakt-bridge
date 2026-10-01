# Configuration

This page covers the settings most users are likely to change.

For the complete list of environment variables, use [`.env.example`](../.env.example) as the source of truth.

## Required settings

```env
PUBLIC_BASE_URL=https://trakt.example.com
TRAKT_CLIENT_ID=...
TRAKT_CLIENT_SECRET=...
BRIDGE_SECRET_KEY=...
ADMIN_KEY=...
```

Generate strong random values for the two local secrets:

```bash
openssl rand -hex 32
```

Keep `.env`, `bridge.db`, `BRIDGE_SECRET_KEY`, `ADMIN_KEY`, and the generated profile manifest URL private.

## Container image

The public GHCR package can be pulled anonymously.

For the easiest install:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:latest
```

For reproducible production deployments, pin a release tag:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:0.9.2
```

or an immutable digest:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge@sha256:<digest>
```

An immutable digest gives the strongest rollback and release-verification guarantee because it cannot move to a different image later.

## Identity mode

Recommended when clients can also use native Trakt:

```env
PULL_IDENTITY_MODE=trakt
```

This preserves Trakt's IMDb identity on state pulls and avoids creating a second representation of the same title in clients that combine native Trakt and AIOStreams state.

## Freshness controls

The conservative public default is:

```env
PULL_CACHE_TTL_SECONDS=300
PULL_HINT_SECONDS=300
PULL_STALE_IF_ERROR_SECONDS=3600
```

- `PULL_CACHE_TTL_SECONDS` controls how long an unchanged matching state may be served without a new Trakt read.
- `PULL_HINT_SECONDS` is advertised to AIOStreams as the preferred pull freshness hint.
- `PULL_STALE_IF_ERROR_SECONDS` allows a known matching state to remain available during temporary Trakt rate limits or upstream errors.

Lower values such as 120 or 60 seconds can improve freshness in some deployments, but they should be staged and measured rather than copied blindly. HomeDocker accepted `60s` for both cache TTL and hint on v0.9.2 with zero new upstream `429` during the acceptance windows, but that is a deployment-specific optimized profile rather than the public default.

AIOStreams has its own watch-state pull cadence. Bridge TTL alone does not guarantee the same end-to-end client refresh interval, and no AIOStreams `WATCH_STATE_*` environment override is required for the v1.0 baseline while upstream defaults already provide satisfactory freshness.

## Reverse proxy

The default Compose file binds the application to loopback only:

```text
127.0.0.1:7000
```

Terminate HTTPS with your reverse proxy and forward traffic to that local port.

`PUBLIC_BASE_URL` must be the externally reachable HTTPS URL used by Trakt OAuth and AIOStreams.

## AIOMetadata

If AIOMetadata is also connected to the same Jellyfin-compatible ecosystem, the recommended read-authority setting is:

```text
Trackers = This server only
```

This prevents secondary tracker history from becoming a second competing read authority. Enabled tracking writes can still fan out independently.

See [INTEGRATIONS.md](INTEGRATIONS.md) for the topology and coexistence rules.
