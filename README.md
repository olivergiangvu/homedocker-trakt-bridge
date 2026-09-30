# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse and Swiftfin.

**Current release: v0.2.2 — push + pull with rate-limit hardening.**

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start
- `pause` → Trakt scrobble pause
- unfinished `stop` → Trakt pause
- finished `stop` → Trakt stop
- `played` → add history
- `unplayed` → remove history
- stable-event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → AIOStreams Continue Watching
- watched movies and episodes → AIOStreams watched state
- `version` / `since` gate avoids rereading unchanged watched history
- current Trakt pagination is followed safely
- repeated unchanged pulls are served from bridge memory cache
- transient Trakt `429` / `5xx` can safely fall back to a bounded stale cache when the caller's `since` still matches the cached version

Trakt remains the canonical long-term watched-history source. AIOStreams is the Jellyfin-compatible playback/state surface.

## Safety decisions

AIOStreams currently uses a 90% watched threshold while Trakt `/scrobble/stop` can mark watched above 80%. The bridge therefore maps `stop + played:false` to Trakt `/scrobble/pause` so 80–89% progress is never promoted to watched by Trakt.

The pull-side `watched` block is authoritative. If Trakt history cannot be read completely, the bridge fails the request rather than returning an empty watched history that could clear imported state in AIOStreams.

The v0.2.2 cache only serves a cached response when AIOStreams sends the same `since` version. Cached and stale-cache responses contain `version + items` only; they never fabricate an authoritative `watched` block. Initial pulls and version mismatches always go back to Trakt.

Anime/absolute episode numbering is not guessed in v0.2.x. Watchlist, dropped state and bulk marks are also deliberately unadvertised.

## Current architecture

```text
Infuse / Swiftfin
       |
       v
   AIOStreams
 watch_state v2
   |       ^
   | push  | pull
   v       |
HomeDocker Trakt Bridge
   |   ^
   |   +-- matching-version pull cache
   |
   v
 Trakt API
```

## Requirements

- Node.js 24+ when running outside Docker
- Docker / Docker Compose for the recommended deployment
- Trakt API application
- public HTTPS URL for OAuth callback
- AIOStreams with Watch State v2 support

## 1. Create the Trakt API app

Create a Trakt API application and configure the redirect URI exactly as:

```text
https://YOUR-BRIDGE-DOMAIN/oauth/callback
```

If using a dedicated HTTPS port, include it exactly, for example:

```text
https://example.com:18449/oauth/callback
```

Keep the Client ID and Client Secret private.

## 2. Configure

```bash
cp .env.example .env
openssl rand -hex 32   # BRIDGE_SECRET_KEY
openssl rand -hex 32   # ADMIN_KEY
```

Set at minimum:

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
```

`BRIDGE_SECRET_KEY` is DR-critical. Losing it makes stored Trakt tokens unreadable and changes all derived profile/setup/addon credentials.

`PULL_TTL_SECONDS` is the bridge's own unchanged-pull cache TTL and is also advertised in the addon manifest. AIOStreams currently has a separate global `WATCH_STATE_PULL_TTL` setting that controls when its UI triggers an on-demand read; the bridge cache prevents those reads from turning into repeated Trakt API calls.

`PULL_STALE_IF_ERROR_SECONDS` only applies to a previously successful, matching-version cache entry and only for transient `429` / `5xx` failures.

## 3. Start

```bash
docker compose -f compose.example.yml up -d --build
```

Health check:

```bash
curl http://127.0.0.1:7000/health
```

Expected:

```json
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.2.2"}
```

## 4. Reverse proxy

The recommended HomeDocker deployment keeps port 7000 loopback-only and lets host nginx terminate TLS:

```nginx
server {
    listen 18449 ssl;
    server_name example.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header X-Forwarded-Port $server_port;
    }
}
```

Do not expose container port 7000 directly to the Internet.

## 5. Connect Trakt

Open:

```text
https://YOUR-BRIDGE-DOMAIN/setup?key=YOUR_ADMIN_KEY
```

Create a profile → **Connect Trakt** → authorize.

The profile page then displays the AIOStreams manifest URL. Treat that URL as a credential.

## 6. Install in AIOStreams

Add the manifest as a custom addon. v0.2.x advertises:

```text
watch_state version: 2
push: start, pause, stop, played, unplayed
bulk: false
pull: items + watched
ttl: 900s by default
```

AIOStreams will call:

```text
GET .../watch_state/pull.json?since=<previous-version>
```

The bridge only returns the authoritative watched block when Trakt's watched activity version changed. Matching-version repeated reads can be answered from the bridge cache without contacting Trakt.

## Trakt pull behavior

Continue Watching reads:

```text
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

Watched history reads:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
```

Pagination follows Trakt's `X-Pagination-Page-Count`; v0.2.x requests 250 movie rows/page and 100 watched-show progress rows/page.

ID output prefers IMDb (`tt...`), then `tmdb:`, then `tvdb:`. Standard episodes are emitted as `metaId:season:episode`, matching the IDs currently produced by AIOStreams/AIOMetadata in the HomeDocker setup.

## Diagnostics

The profile UI shows push and pull activity in the configured timezone.

Push delivery retries are grouped by stable AIOStreams event ID and surface as `recovered` after a later successful retry. Pull polling is different: repeated requests can legitimately reuse the same `since` cursor, so each pull request stays as its own Recent Events row.

v0.2.2 labels bridge-cache hits as `cached`. If Trakt returns a transient `429` / `5xx` and a safe matching-version cache is available, the row is labeled `stale`; its detail includes the upstream error/path and cache age.

Trakt error diagnostics include the upstream endpoint and `Retry-After` when available.

## Backup / disaster recovery

Back up all of the following together:

- `trakt_bridge_data` / `/app/data/bridge.db`
- `.env`
- compose definition
- nginx site configuration

The v0.2.2 pull cache is intentionally memory-only; it does not need backup and is rebuilt after restart.

For a consistent SQLite backup, briefly stop/quiesce the bridge while staging the DB copy, then restart it before restic/rclone uploads the staged backup.

Never regenerate `BRIDGE_SECRET_KEY` during restore unless you intentionally want to invalidate the encrypted Trakt tokens and all derived addon/setup URLs.

## Release history

See [CHANGELOG.md](CHANGELOG.md).

Current milestones:

- **v0.1.0:** push MVP
- **v0.1.1:** diagnostics hardening
- **v0.2.0:** Trakt → AIOStreams playback + watched pull
- **v0.2.1:** pull diagnostics correctness
- **v0.2.2:** bridge-side pull cache, request coalescing and safe stale-on-transient-error fallback
- **v0.3:** watchlist, dropped state, next-up/ID hardening, bulk marks
- **v1.0:** production migrations, release image workflow, broader compatibility hardening

## Security

- Trakt access and refresh tokens are AES-256-GCM encrypted at rest.
- Tokens never appear in addon URLs.
- setup/addon keys are HMAC-derived from the DR-critical bridge secret.
- admin/setup/manifest responses use `Cache-Control: no-store`.
- the manifest URL itself is a credential; do not publish it.
- query-string setup keys can appear in browser history or reverse-proxy logs; restrict access and avoid sharing screenshots/logs containing them.

## Development

Requires Node.js 24+.

```bash
npm run check
npm test
```

v0.2.2 has no runtime npm dependencies; it uses Node built-ins including `node:sqlite`.
