# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse, Swiftfin, Strand and Odin.

**Current development release: v0.4.0 — production hardening on top of the v0.3.7 functional baseline.**

v0.4.0 does not redesign the watch-state protocol. It adds the lifecycle pieces needed before a future production/1.0 release: explicit DB migrations, readiness/status endpoints, a production operator dashboard, container smoke tests, tag-based GHCR release publishing, and a formal HomeDocker authority model.

## HomeDocker authority model

```text
Trakt
  canonical watched / resume history
        ^
        |
HomeDocker Trakt Bridge
        ^
        |
AIOStreams
  Jellyfin-compatible state surface
        |
 Strand / Odin / Remux / Trellis

AIOMetadata
  metadata/catalog + local playstate
  secondary-tracker write/fan-out
        |
 Simkl / MDBList / other enabled trackers
```

For the owner AIOMetadata Jellyfin user:

```text
Trackers = This server only
```

That setting controls **tracker readback** into AIOMetadata's Jellyfin Continue Watching / watched / Next Up surface. It does not disable enabled Watch Tracking writes to secondary trackers.

This keeps Trakt Bridge as the single external history read authority while preserving AIOMetadata's write/fan-out role.

See [`docs/AIOMETADATA.md`](docs/AIOMETADATA.md).

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start when progress is at least 1%
- `pause` → Trakt scrobble pause when progress is at least 1%
- unfinished `stop` → Trakt pause when progress is at least 1%
- sub-1% start/pause/unfinished-stop → acknowledged + ignored locally
- completed `stop` → Trakt scrobble stop
- explicit played stop below 1% → history add
- single `played` / `unplayed` → history add/remove
- season/show bulk played/unplayed → one nested history request per validated bulk part
- duplicate same-kind single echoes after successful bulk → bounded suppression
- `watchlisted` / `unwatchlisted` → Trakt watchlist add/remove
- IMDb/TMDb/TVDb fallback resolution
- playback-learned IMDb alias evidence keyed by stable Trakt show ID
- stable event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → Continue Watching
- watched movies/episodes → authoritative watched state
- movie/show watchlist → watchlist/favourites
- alias-aware watched counts
- exact Next Up only when supplied upstream
- restart-safe pull cache
- bounded stale fallback on transient `429` / `5xx`

## Dual-IMDb compatibility

Production exposed one real show with two valid IMDb spellings:

```text
Trakt IMDb:      tt44051354
AIOStreams IMDb: tt44094505
TMDb:            276470
TVDb:            480791
```

v0.3.4/v0.3.5 learned the AIOStreams spelling and rewrote future Trakt pull rows. That works for AIOStreams-only clients, but Strand also uses native Trakt and therefore saw both IDs as separate Continue Watching identities.

Since v0.3.6:

```env
PULL_IDENTITY_MODE=trakt      # default / HomeDocker production
PULL_IDENTITY_MODE=aiostreams # optional legacy learned-alias rewrite
```

`trakt` preserves Trakt's IMDb spelling on pull so native Trakt and AIOStreams converge on the same identity.

v0.3.7 additionally suppresses Trakt scrobbles below 1% progress, preventing deterministic `422` retry loops.

## What v0.4.0 adds

### Explicit database migrations

Bridge SQLite now uses `PRAGMA user_version`.

```text
v0.3.x DB schema 0
        |
        v
migration 1: baseline-v0.4.0
        |
        v
v0.4.0 DB schema 1
```

Existing profiles, encrypted tokens, processed events, aliases, event history and pull cache are preserved.

A database newer than the binary fails closed instead of being silently downgraded.

### Health and readiness

```text
GET /health
  process liveness

GET /readiness
  DB + supported schema + at least one connected profile

GET /status?key=<ADMIN_KEY>
  authenticated operator diagnostics
```

Readiness does not call Trakt, so container health does not consume rate limit or depend on Internet latency.

### Production operator dashboard

The profile setup page now surfaces:

- runtime version / health;
- canonical history authority;
- pull identity mode;
- DB schema version;
- last successful pull;
- pull item / watched / watchlist counts;
- unresolved vs historical errors;
- AIOMetadata `This server only` authority guidance;
- learned alias diagnostics and effective pull identity;
- filtered recent events: `All | Errors | Pull | Playback | Ignored`.

### CI and release engineering

Every push/PR runs:

```text
npm run check
npm test
Docker build
container /health smoke test
/readiness setup-required smoke test
```

Tags matching `v*` additionally:

- re-run code/tests;
- build an amd64 production image;
- publish to `ghcr.io/<owner>/homedocker-trakt-bridge`;
- create a GitHub Release.

Production should pin an explicit version or digest. Do not deploy `latest`.

## Requirements

- Node.js 24+ outside Docker
- Docker / Docker Compose for recommended deployment
- Trakt API application
- public HTTPS OAuth callback URL
- AIOStreams Watch State v2 support

## Configuration

```bash
cp .env.example .env
openssl rand -hex 32   # BRIDGE_SECRET_KEY
openssl rand -hex 32   # ADMIN_KEY
```

Minimum production settings:

```env
PUBLIC_BASE_URL=https://YOUR-BRIDGE-DOMAIN
TRAKT_CLIENT_ID=...
TRAKT_CLIENT_SECRET=...
BRIDGE_SECRET_KEY=...
ADMIN_KEY=...
DISPLAY_TIMEZONE=Asia/Ho_Chi_Minh
PULL_TTL_SECONDS=900
PULL_STALE_IF_ERROR_SECONDS=3600
PULL_MAX_PAGES=500
BULK_SINGLE_DEDUPE_SECONDS=300
PULL_IDENTITY_MODE=trakt
```

`BRIDGE_SECRET_KEY` is DR-critical. Losing or changing it makes stored Trakt tokens unreadable and changes derived setup/addon credentials.

## Local source deployment

```bash
docker compose -f compose.example.yml up -d --build
```

The recommended HomeDocker topology keeps port 7000 loopback-only and lets host nginx terminate TLS.

## Health checks

```bash
curl -fsS http://127.0.0.1:7000/health
curl -sS -i http://127.0.0.1:7000/readiness
```

Expected production liveness:

```json
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.4.0"}
```

Expected readiness after Trakt is connected:

```text
HTTP 200
status = ready
schemaVersion = 1
connectedProfiles >= 1
```

## Security

- Trakt access/refresh tokens are AES-256-GCM encrypted at rest.
- Tokens never appear in addon URLs.
- setup/addon keys are HMAC-derived from the DR-critical bridge secret.
- admin/setup/manifest responses use `Cache-Control: no-store`.
- the manifest URL itself is a credential.
- `/status` requires `ADMIN_KEY`.
- production container runs as non-root `node`.
- application port should remain loopback-only behind nginx.

## Backup / disaster recovery

Back up together:

- `trakt_bridge_data` / `/app/data/bridge.db`;
- `.env`;
- compose definition;
- nginx site configuration.

For a rollback across a DB schema-changing release, restore the matching pre-upgrade DB backup as well as the older image/source.

See [`docs/PRODUCTION.md`](docs/PRODUCTION.md).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — canonical topology, identity, DB lifecycle and runtime boundaries
- [`docs/UPSTREAM.md`](docs/UPSTREAM.md) — AIOStreams, AIOMetadata and Trakt contracts
- [`docs/AIOMETADATA.md`](docs/AIOMETADATA.md) — why `This server only` is required in HomeDocker
- [`docs/PRODUCTION.md`](docs/PRODUCTION.md) — deploy/health/backup/rollback checklist
- [`CHANGELOG.md`](CHANGELOG.md) — release history

## Development

```bash
npm run check
npm test
```

v0.4.0 has no runtime npm dependencies and uses Node built-ins including `node:sqlite`.

## Roadmap to 1.0

```text
0.3.7  functional stable baseline
0.4.0  production lifecycle hardening
0.5.0  end-to-end integration / recovery hardening
0.9.0  release candidate + burn-in
1.0.0  compatibility freeze / production release
```

v0.4.0 is intended to be production-capable for HomeDocker after successful migration and burn-in, but it is not yet the final 1.0 compatibility contract.
