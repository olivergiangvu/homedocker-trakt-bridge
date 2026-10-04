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
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:1.0.0
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

Lower values such as 120 or 60 seconds can improve freshness in some deployments, but they should be staged and measured rather than copied blindly. HomeDocker accepted `60s` for both cache TTL and hint through the v0.9.2 → v1.0.0 qualification cycle. That remains a deployment-specific optimized profile rather than the public default.

AIOStreams has its own watch-state pull cadence. Bridge TTL alone does not guarantee the same end-to-end client refresh interval, and no AIOStreams `WATCH_STATE_*` environment override is required for the v1.0 baseline while upstream defaults already provide satisfactory freshness.

## Optional AIO local-state reconciler (rc.5 detect-only)

AIOStreams remains unmodified. The bridge can optionally inspect the AIOStreams SQLite database through a **read-only mount** to detect local resume-state changes that were not accompanied by a watch-state playback delivery.

The feature is disabled by default:

```env
AIO_RECONCILER_MODE=off
```

For the rc.5 detect-only canary:

```env
AIO_RECONCILER_MODE=detect
AIO_DB_PATH=/aio-data/db.sqlite
AIO_RECONCILE_INTERVAL_SECONDS=15
AIO_RECONCILE_GRACE_SECONDS=30
AIO_RECONCILE_MAX_ROWS=100
```

Mount the AIOStreams data directory read-only into the bridge container. A HomeDocker-style Docker volume can be exposed as:

```yaml
volumes:
  - trakt_bridge_data:/app/data
  - /var/lib/docker/volumes/aiostreams_data/_data:/aio-data:ro
```

Detect mode:

- opens the AIO database with SQLite read-only mode and `query_only`
- only inspects unfinished local movie/episode resume rows
- checks for a nearby queued `start`, `pause`, or `stop` delivery
- stores only its own cursor/watermark in the bridge database
- logs a reconciliation candidate when AIO local state changed without a matching playback delivery
- never calls Trakt and never writes to AIOStreams

The first enabled run establishes a baseline cursor and does not replay historical AIO rows. The detector also requires exactly one connected bridge profile; ambiguous multi-profile deployments fail safe.

This is intentionally an observation phase. A later write-capable reconciler must compare a candidate against current Trakt playback before synthesizing any stop so native-Trakt clients remain authoritative when they already committed the same or newer state.

## Duplicate-history guard

The default semantic history guard is:

```env
HISTORY_DEDUPE_SECONDS=300
```

AIOStreams can legitimately emit more than one event around the end of a viewing session. Trakt also creates a history entry itself when a successful `/scrobble/stop` is classified as `action=scrobble`. The bridge therefore keeps a short, restart-safe marker for the canonical Trakt movie/episode and suppresses only an equivalent state write inside this window.

The guard covers both:

- a completed Trakt scrobble followed shortly by an explicit AIOStreams `played` mark
- repeated same-state `played` or `unplayed` marks with different AIOStreams event IDs

An opposite state transition is never suppressed, so `played -> unplayed -> played` remains valid. A same-state event outside the configured window is also processed normally, preserving legitimate later rewatches. The marker uses the existing SQLite cache and requires no database migration.

Set `HISTORY_DEDUPE_SECONDS=0` only when diagnosing raw upstream behavior. This guard cannot see writes made directly by another Trakt client, so native Trakt integrations can still be a separate duplicate-history writer.

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
