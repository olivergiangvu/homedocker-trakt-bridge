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
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:1.2.0
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

AIOStreams has its own watch-state pull cadence. Bridge TTL alone does not guarantee the same end-to-end client refresh interval, and no AIOStreams `WATCH_STATE_*` environment override is required for the v1.2 baseline while upstream defaults already provide satisfactory freshness.

## Optional AIO local-state reconciler (v1.2 detect-only)

AIOStreams remains unmodified. The bridge can optionally inspect the AIOStreams SQLite database through a **read-only mount** to detect local resume-state changes that were not accompanied by a watch-state playback delivery.

The feature is disabled by default:

```env
AIO_RECONCILER_MODE=off
```

For v1.2 detect-only mode:

```env
AIO_RECONCILER_MODE=detect
AIO_DB_PATH=/aio-data/db.sqlite
AIO_RECONCILE_INTERVAL_SECONDS=15
AIO_RECONCILE_GRACE_SECONDS=30
AIO_RECONCILE_QUIET_SECONDS=300
AIO_RECONCILE_COVERAGE_LOOKBACK_SECONDS=180
AIO_RECONCILE_POSITION_TOLERANCE_MS=2000
AIO_RECONCILE_SINK_NAME=homedocker-trakt-bridge
AIO_RECONCILE_SINK_INSTANCE_ID=e3fe3b0
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
- keeps one restart-safe pending candidate per AIO item and replaces it whenever a newer local row arrives
- requires the item to remain unchanged for `AIO_RECONCILE_QUIET_SECONDS` (default 300s) before classifying it as settled
- checks playback coverage only against the configured HomeDocker sink, optionally pinned by AIO addon instance ID
- coverage requires a **delivered** HomeDocker `start`, `pause`, or `stop` whose positive `positionMs` matches the final AIO row within `AIO_RECONCILE_POSITION_TOLERANCE_MS`
- coverage may look backward up to `AIO_RECONCILE_COVERAGE_LOOKBACK_SECONDS` because AIO can write a later generic UserData row after an already-delivered stop
- stores only its own cursor/pending/settled watermarks in the bridge database
- logs a settled missing-delivery candidate only after the quiescence window; intermediate rows are not emitted as final candidates
- never calls Trakt and never writes to AIOStreams

The first enabled v1.2 run establishes a fresh v2 baseline cursor and does not replay historical AIO rows. The detector also requires exactly one connected bridge profile; ambiguous multi-profile deployments fail safe. If the configured HomeDocker sink cannot be resolved uniquely, settlement is blocked rather than guessed.

This remains intentionally an observation phase. The 300s quiet window reduces false finalization from sparse UserData updates but is not treated as proof that playback ended. The bounded position-aware coverage lookback prevents a later generic UserData/unplayed row from falsely hiding a valid HomeDocker stop that carried the same resume point.

### Experimental compare-only mode (v1.3 development)

The next reconciler phase adds an opt-in comparison mode:

```env
AIO_RECONCILER_MODE=compare
```

Compare mode keeps the entire v1.2 detector unchanged, then places only settled missing-delivery candidates onto a separate restart-safe comparison queue. That queue uses authenticated Trakt **GET** playback endpoints to classify whether native Trakt already holds the same/newer state, is older/behind, or has no matching current playback.

Compare mode:

- keeps the AIO SQLite mount read-only
- preserves v1.2 sink/delivery/quiet-window qualification
- uses the existing Trakt read/shared cooldown lanes
- never calls Trakt POST/PUT/DELETE endpoints
- gives a newer native-Trakt timestamp precedence even when its numeric progress is lower
- treats equivalent or ahead Trakt progress as authoritative
- classifies older-and-behind Trakt playback as a recovery candidate only
- fails closed on unusable or ambiguous media identity
- keeps Trakt read failures/rate limits retryable instead of turning them into a write decision
- persists compare terminal markers separately from v1.2 detect settlement markers

There is still **no automatic Trakt recovery write in compare mode**. A later guarded writeback phase must be qualified separately before it can synthesize any playback update.


### AIO false-unplayed echo guard (v1.3 RC3)

Some Jellyfin-compatible clients can POST a single UserData update that contains both `Played=false` and a positive `PlaybackPositionTicks`. AIOStreams currently processes the played flag first and can queue an `unplayed` push before the same request stores the positive resume position. This creates a contradictory outbound history-remove even though AIO's final local state remains unfinished playback.

RC2 adds an opt-in Bridge-side guard:

```env
AIO_UNPLAYED_ECHO_GUARD=true
```

The guard only suppresses a single-item `unplayed` when the exact AIO delivery is proven to be one of two reproduced composite UserData echoes:

- **Positive-resume echo:** the current AIO row is `origin=local`, `played=0`, still has a positive resume position, was updated in the same tightly bounded transaction window, and an equivalent **delivered** HomeDocker `start`, `pause`, or `stop` already carries that positive position.
- **Zero-position stop echo (RC3):** the current row is `origin=local`, `played=0`, `position_ms=0`, and both `updated_at` and `last_played_at` were stamped contemporaneously with the exact `unplayed` delivery. AIO's explicit `unplayed` write does not update `last_played_at`; a subsequent stop write does, even at position zero.

The RC3 discriminator is based on AIO's own local-provider semantics rather than a loose "recent playback" heuristic. A controlled explicit Mark Unwatched left `last_played_at` unchanged and therefore remained outside the guard.

If any proof is missing, ambiguous, or stale, or the AIO database cannot be read, the guard fails open and preserves the existing `/sync/history/remove` behavior. Bulk marks remain outside this guard.

The guard is disabled by default for public installs and does not write AIOStreams.

### AIO history-lineage guard (v1.3 RC4)

RC4 adds a broader opt-in guard that covers both false history removal and false history addition:

```env
AIO_HISTORY_ECHO_GUARD=true
```

This guard keeps isolated manual history marks available and does not disable `played/unplayed` support. It combines three pieces of evidence:

- a fresh authoritative Trakt watched snapshot captured when Bridge serves watched state to AIO;
- a fast event-time journal of the exact AIO `watch_deliveries` row plus the contemporaneous read-only `watch_state` row;
- cohort detection for rapid single-mark fanout.

Default tuning:

```env
AIO_HISTORY_EVIDENCE_INTERVAL_MS=1000
AIO_HISTORY_EVIDENCE_LOOKBACK_SECONDS=120
AIO_HISTORY_EVIDENCE_MAX_ROWS=500
AIO_HISTORY_COHORT_WINDOW_MS=10000
AIO_HISTORY_COHORT_MIN_ITEMS=3
CANONICAL_HISTORY_MAX_AGE_SECONDS=900
```

The decision rules are intentionally asymmetric:

- a single `played` is suppressed when a fresh canonical Trakt snapshot already says the item is watched, because sending it to `/sync/history` would create a fresh `watched_at` and can reorder old history;
- a single `played` on an item the fresh snapshot says is unwatched is allowed as a genuine Mark Watched;
- a single `unplayed` on a canonically watched item remains allowed unless deterministic event-time evidence proves one of the reproduced composite echoes or it belongs to a sync-fanout cohort;
- a single `unplayed` on an already-unwatched item is suppressed as redundant;
- at least 3 distinct single items carrying the same mark kind inside the 10-second cohort window are treated as state-sync fanout;
- AIO native bulk season/show marks are excluded from the cohort guard and retain their existing behavior.

The 10-second default is based on the HomeDocker 48-hour soak, where one confirmed three-item `played` fanout spanned about 5.7 seconds. The larger observed cohorts contained 14 and 9 items, including non-sequential episodes.

RC4 keeps `AIO_UNPLAYED_ECHO_GUARD=true` as the RC3 fallback when the event-time journal did not capture sufficient immutable evidence. Both guards keep the AIO database read-only and require no Bridge schema migration.

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
