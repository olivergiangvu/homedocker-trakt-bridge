# Architecture — v0.4.0

v0.4.0 keeps the v0.3.7 watch-state semantics and hardens the service around an explicit production authority model, schema migrations, readiness/status surfaces and release engineering.

## Canonical HomeDocker topology

```text
                         READ AUTHORITY
                              |
                              v
                           TRAKT
                              ^
                              |
                       Trakt Bridge
                       v0.4.0
                              ^
                              |
                         AIOStreams
                    Jellyfin state surface
                              |
         +--------------------+--------------------+
         |                    |                    |
       Strand               Odin              Remux/Trellis
   native Trakt ON


                      WRITE / FAN-OUT PATH
                              |
                       playback events
                              |
                         AIOMetadata
                              |
                   secondary trackers
                Simkl / MDBList / etc.
```

## Authority rules

HomeDocker production intentionally has one external history read authority:

```text
WATCH HISTORY / RESUME      = Trakt
JELLYFIN WATCH-STATE SURFACE = AIOStreams
SECONDARY TRACKER FAN-OUT   = AIOMetadata
METADATA / CATALOG          = AIOMetadata
STREAM RESOLUTION           = AIOStreams
```

AIOMetadata should use **Trackers = This server only** for the Jellyfin user that represents the same person as the HomeDocker owner. That picker controls what AIOMetadata reads back into its Jellyfin Continue Watching / watched / Next Up surface. It does not disable enabled Watch Tracking writes to secondary trackers.

This removes a second external history read authority while preserving AIOMetadata's useful write/fan-out role.

## Watch-state data path

```text
Jellyfin-compatible client
          |
          v
     AIOStreams
     watch_state v2
       /       \
      / push    \ pull
     v           ^
 Trakt Bridge ---+
      |
      +-- provider ID normalization
      +-- learned show alias evidence
      +-- pull identity policy
      +-- sub-1% scrobble guard
      +-- duplicate-safe bulk reconciliation
      +-- restart-safe pull cache
      |
      v
    Trakt API
```

Trakt remains canonical for long-term watched history and watchlist state. AIOStreams remains the playback/state surface exposed to clients.

## Identity model

AIOStreams Watch State v2 can carry both `metaId` / `videoId` and shared provider IDs. The bridge normalizes the conventional HomeDocker spaces:

```text
IMDb  -> tt0903747
TMDb  -> tmdb:1396
TVDb  -> tvdb:81189
```

Known aliases are resolved in deterministic order. A Trakt 404 can fall through to another known provider spelling; authentication failures, `429` and `5xx` remain errors.

### Learned IMDb aliases

A real show can legitimately have two valid IMDb IDs. The bridge persists evidence keyed by:

```text
profile + stable Trakt show ID
```

with:

```text
preferredMetaId = IMDb spelling used by AIOStreams playback
traktImdb       = IMDb spelling returned by Trakt
revision        = monotonic alias revision
```

The production regression fixture remains:

```text
Trakt IMDb:      tt44051354
AIOStreams IMDb: tt44094505
TMDb:            276470
TVDb:            480791
```

### Pull identity policy

```env
PULL_IDENTITY_MODE=trakt      # default
PULL_IDENTITY_MODE=aiostreams # optional legacy rewrite mode
```

`trakt` mode preserves the Trakt IMDb spelling on pull. This is the HomeDocker production mode and is required when clients such as Strand use native Trakt in parallel with AIOStreams.

`aiostreams` mode keeps the v0.3.5 learned-alias rewrite behavior for AIOStreams-only clients.

Learned aliases remain stored in both modes. In `trakt` mode they are diagnostic evidence and do not rewrite the authoritative pull representation.

## Push semantics

Supported events:

```text
start
pause
stop
played
unplayed
watchlisted
unwatchlisted
bulk played/unplayed
```

AIOStreams uses a stricter watched threshold than Trakt scrobble stop, so unfinished AIOStreams stops are mapped to Trakt pause rather than accidentally promoting 80–89% progress to watched.

v0.3.7 introduced the production minimum-progress guard, retained by v0.4.0:

```text
progress < 1%
  start / pause / unfinished stop -> ignored + 204
  explicit played stop           -> history-add

progress >= 1%
  normal scrobble mapping applies
```

Ignored events are still entered into the processed-event idempotency store.

## Pull semantics

Fresh pull base reads:

```text
/sync/last_activities
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

When the state cursor changed, authoritative state additionally reads all pages of:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

Authoritative `watched` and `watchlist` blocks are atomic. If a changed-state read cannot be completed, the pull fails rather than returning destructive partial/empty state.

The pull representation remains compatible with v0.3.6/v0.3.7:

```text
state cursor schema = watch-state-v0.3.6
persisted cache key = pull-state:v5:<profile>
```

v0.4.0 does not require another pull-cache purge.

## Pull cache and rate-limit behavior

Successful pull cache contains only:

```text
version
items
fetchedAt
```

A fresh cache hit requires the caller's `since` cursor to match the cached version and the entry to be younger than `PULL_TTL_SECONDS`.

Bounded stale fallback is allowed only for matching cursors on transient `429` / `5xx` and only within `PULL_STALE_IF_ERROR_SECONDS`.

Cached/stale responses never fabricate authoritative watched or watchlist blocks.

## Database lifecycle — new in v0.4.0

v0.3.x created tables opportunistically. v0.4.0 introduces explicit SQLite schema versioning using `PRAGMA user_version` and ordered migrations.

```text
schema 0  -- existing v0.3.x database
    |
    v
migration 1: baseline-v0.4.0
    |
    v
schema 1
```

Migration 1 is intentionally idempotent: existing v0.3.x tables/data are retained while missing baseline objects are created and the schema is marked as version 1.

Safety rules:

- upgrade older supported schemas forward;
- never silently downgrade;
- a database newer than the running binary fails closed;
- profiles, OAuth tokens, processed events, aliases and pull cache remain intact;
- `bridge.db` + `.env` + nginx/compose configuration remain DR-critical.

## Runtime health surfaces — new in v0.4.0

### `/health`

Liveness only. It answers when the process is serving HTTP.

### `/readiness`

Checks:

```text
database query succeeds
schema == expected schema
>= 1 Trakt-connected profile
```

It returns HTTP `200` when ready and `503` with `status=setup_required` when the process is alive but not operationally configured.

No live Trakt API request is performed by readiness, so health checks do not consume rate limit or become dependent on Internet latency.

### `/status?key=<ADMIN_KEY>`

Authenticated operator JSON. It exposes safe operational diagnostics only: schema, authority model, pull summary, unresolved/historical error counts and learned alias diagnostics. It does not expose OAuth tokens, bridge secrets or addon/setup credentials.

## Operator UI — new in v0.4.0

The setup page is now an operational dashboard rather than only a connection page. It surfaces:

```text
runtime version / health
history authority
pull identity mode
DB schema
last successful pull
items / watched / watchlist counts
unresolved vs historical errors
AIOMetadata authority guidance
learned identity aliases + effective pull spelling
filtered recent events
```

Event filters:

```text
All | Errors | Pull | Playback | Ignored
```

Historical errors remain visible for audit while unresolved retry chains are counted separately.

## Container and network boundary

The production container:

- runs as the non-root `node` user;
- stores persistent state under `/app/data`;
- exposes application port 7000;
- uses Docker liveness healthcheck against `/health`;
- is expected to bind host port 7000 to loopback only;
- relies on host nginx for public TLS termination.

Do not expose port 7000 directly to the Internet.

## CI / release lifecycle — new in v0.4.0

Every push/PR must pass:

```text
npm run check
npm test
Docker image build
container /health smoke test
/readiness setup-required smoke test
```

Tagged releases (`v*`) additionally publish an immutable GHCR image and create a GitHub Release after code/tests pass.

Production deployment should pin an explicit release tag or digest rather than `latest`.

## Deferred after v0.4.0

- full mock Trakt integration test matrix (`401/404/422/429/5xx`, pagination, OAuth refresh);
- end-to-end fake AIOStreams ↔ Bridge ↔ mock Trakt protocol tests;
- restore/migration test from real production backup artifacts;
- anime/absolute-number episode mapping;
- generalized non-IMDb duplicate entity reconciliation;
- v0.9 release-candidate burn-in and v1.0 compatibility freeze.
