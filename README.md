# HomeDocker Trakt Bridge

Self-hosted **AIOStreams `watch_state` v2 → Trakt** bridge.

**Current release: v0.1.0 (push MVP).**

## What v0.1 does

- Trakt OAuth 2 Authorization Code flow.
- Encrypted access/refresh tokens (AES-256-GCM at rest).
- Automatic refresh of Trakt access tokens; replaces single-use refresh tokens after every refresh.
- AIOStreams `watch_state` v2 manifest.
- Push events: `start`, `pause`, `stop`, `played`, `unplayed`.
- Idempotency on AIOStreams event `id`.
- Trakt movie/show/episode ID resolution with SQLite cache.
- SQLite diagnostics and recent-event view.
- Docker image with healthcheck.
- Minimal setup UI; no JS framework and no runtime npm dependencies.

Not in v0.1: Trakt → AIOStreams pull, watchlist, dropped state, viewers, bulk marks. Anime/absolute-number episode mapping is also deliberately rejected in v0.1 rather than risking a wrong Trakt episode; that resolver is planned for v0.3.

## Why `stop` can become `pause`

AIOStreams' current watch-state contract says `played` is based on a **90%** watched threshold. Trakt's `/scrobble/stop` marks an item watched above **80%**. To avoid turning an AIOStreams `played:false` event at 80–89% into a Trakt watched item, the bridge sends that stop to `/scrobble/pause` instead. If `played:true`, it uses `/scrobble/stop`.

## 1. Create a Trakt API application

Create an app in Trakt Developer settings. Configure the redirect URI exactly as:

```text
https://YOUR-BRIDGE-DOMAIN/oauth/callback
```

Enable scrobbling permission for the application.

Record the Client ID and Client Secret.

## 2. Configure

```bash
cp .env.example .env
openssl rand -hex 32   # BRIDGE_SECRET_KEY
openssl rand -hex 32   # ADMIN_KEY
```

Edit `.env` and set at minimum:

```env
PUBLIC_BASE_URL=https://traktbridge.example.com
TRAKT_CLIENT_ID=...
TRAKT_CLIENT_SECRET=...
BRIDGE_SECRET_KEY=...
ADMIN_KEY=...
```

`BRIDGE_SECRET_KEY` is **DR-critical**. Losing it makes stored Trakt tokens unreadable and changes all derived setup/addon URLs.

## 3. Start

```bash
docker compose -f compose.example.yml up -d --build
```

Health check:

```bash
curl http://127.0.0.1:7000/health
```

## 4. nginx

Example host-nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:7000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

TLS should terminate at nginx.

## 5. Create a bridge profile

Open:

```text
https://YOUR-BRIDGE-DOMAIN/setup?key=YOUR_ADMIN_KEY
```

Create a profile, open it, then click **Connect Trakt**.

After OAuth succeeds, copy the generated `manifest.json` URL from the profile page.

The manifest URL itself is a credential. Keep it private.

## 6. Install in AIOStreams

Add the generated manifest URL as a custom addon. AIOStreams should detect:

```text
watch_state version: 2
push: start, pause, stop, played, unplayed
bulk: false
```

If the bridge URL is a Docker-private address rather than the public HTTPS URL, AIOStreams requires `WATCH_STATE_ALLOW_PRIVATE_URLS=true`. For the recommended public-nginx URL, that setting is not required.

## AIOStreams protocol behavior relied on by v0.1

- only Jellyfin-client playback produces watch-state push events;
- event IDs are stable across retries and should be deduplicated;
- `200/204` means delivered;
- `401/403` means reconnect required;
- `429/5xx` is retried with backoff;
- a missing `durationMs` means progress is unknown and must not be treated as zero.

## Backup / DR

Back up:

- `trakt_bridge_data` (`/app/data/bridge.db`)
- `.env` / secrets
- compose definition

The SQLite DB is live state. For your HomeDocker backup flow, quiesce `trakt-bridge` during the short staging copy, then restart it before restic/rclone upload.

## Roadmap

- **v0.2:** Trakt → AIOStreams pull: playback progress, watched history, version/since gate, safe pagination.
- **v0.3:** watchlist, next-up/ID mapping hardening, bulk marks.
- **v1.0:** production hardening, migrations, richer diagnostics, release image workflow.

## Security notes

- no raw Trakt token is placed in addon URLs;
- Trakt tokens are AES-256-GCM encrypted at rest;
- setup/addon keys are HMAC-derived from the DR-critical bridge secret;
- admin/setup/manifest responses use `Cache-Control: no-store`;
- do not expose `/setup?key=...` in screenshots, logs, bookmarks shared with others, or public issue reports.

## Development

Requires Node 24+.

```bash
npm run check
npm test
```

No `npm install` is required for v0.1; it uses Node built-ins only, including `node:sqlite`.
