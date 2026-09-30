# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse and Swiftfin.

**Current release: v0.3.1 — playback, watched history, movie/show watchlist sync and efficient whole-season/show played/unplayed bulk marks.**

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start
- `pause` → Trakt scrobble pause
- unfinished `stop` → Trakt pause
- finished `stop` → Trakt stop
- single `played` → add history
- single `unplayed` → remove history
- whole-season/show `played` / `unplayed` → one nested Trakt history sync request per AIOStreams bulk part
- `watchlisted` → add movie/show to Trakt watchlist
- `unwatchlisted` → remove movie/show from Trakt watchlist
- stable-event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → AIOStreams Continue Watching
- watched movies and episodes → AIOStreams watched state
- movie/show Trakt watchlist → AIOStreams watchlist/favourites surface
- one `version` / `since` cursor covers watched + supported watchlist activity
- current Trakt pagination is followed safely
- repeated unchanged pulls are served from a matching-version bridge cache
- safe pull cache survives container restarts through SQLite
- transient Trakt `429` / `5xx` can fall back to bounded stale cache when the caller's `since` still matches the cached version

Trakt remains the canonical long-term tracker source for watched history and watchlist state. AIOStreams is the Jellyfin-compatible playback/state surface.

## Safety decisions

AIOStreams currently uses a 90% watched threshold while Trakt `/scrobble/stop` can mark watched above 80%. The bridge therefore maps `stop + played:false` to Trakt `/scrobble/pause` so 80–89% progress is never promoted to watched by Trakt.

The pull-side `watched` and `watchlist` blocks are authoritative. If the complete changed state cannot be read from Trakt, the bridge fails the pull rather than returning destructive empty state.

The bridge cache only serves when AIOStreams sends the same `since` version. Cached and stale-cache responses contain `version + items` only; they never fabricate authoritative `watched` or `watchlist` blocks. Initial pulls and version mismatches always go back to Trakt.

Successful pushes that can alter authoritative history/watchlist state invalidate the pull cache. This is especially important because Trakt automatically removes watchlist entries when an item becomes watched.

Trakt assigns the timestamp when an item is added to its watchlist. The bridge therefore does not pretend that the AIOStreams event timestamp can be preserved on a Trakt watchlist add.

For bulk played/unplayed marks, the bridge writes only the `videos[]` AIOStreams says changed. It groups those explicit season/episode numbers under the resolved Trakt show instead of sending a bare show object, which could mark episodes the AIOStreams metadata did not list. AIOStreams splits marks larger than 500 videos into independent parts; the bridge handles each part as one idempotent push event and one Trakt sync request.

Anime/absolute episode numbering is not guessed. Bulk events containing anime-spaced video IDs fail closed. Dropped state remains unadvertised.

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
   |   +-- matching-version cache
   |       memory hot path + SQLite persistence
   |
   +---- playback progress
   +---- watched history + bulk season/show marks
   +---- movie/show watchlist
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

`PULL_STALE_IF_ERROR_SECONDS` is also the retention bound for the persisted safe pull cache. It only applies to a previously successful, matching-version cache entry and only for transient `429` / `5xx` failures.

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
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.3.1"}
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

Add the manifest as a custom addon. v0.3.1 advertises:

```text
watch_state version: 2
push: start, pause, stop, played, unplayed, watchlisted, unwatchlisted
bulk: true
pull: items + watched + watchlist
ttl: 900s by default
```

AIOStreams calls:

```text
GET .../watch_state/pull.json?since=<previous-version>
```

The bridge returns authoritative watched + watchlist blocks only when the combined Trakt state version changed. Matching-version repeated reads can be answered from the bridge cache without contacting Trakt.

### Upgrading from v0.2.x

v0.3.x changes both the state-version hash and the persisted pull-cache namespace. The cache key prefix is `pull-state:v3:`; old v0.2.x `pull-state:` entries are deliberately ignored and expire naturally. This guarantees the first post-upgrade pull is watchlist-aware instead of being temporarily answered by a still-fresh v0.2.x cache entry.

## Trakt pull behavior

Continue Watching reads:

```text
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

Changed watched/watchlist state reads:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

The combined state cursor is derived from Trakt `/sync/last_activities` watched timestamps plus movie/show watchlist timestamps. A watchlist-only change therefore advances the same AIOStreams `since` cursor and triggers a safe authoritative refresh.

Pagination follows Trakt's `X-Pagination-Page-Count`; the bridge requests 250 watched-movie rows/page and 100 rows/page for watched shows and watchlists.

ID output prefers IMDb (`tt...`), then `tmdb:`, then `tvdb:`. Standard episodes are emitted as `metaId:season:episode`, matching the IDs currently produced by AIOStreams/AIOMetadata in the HomeDocker setup.

## Trakt push behavior

Watchlist events are accepted only for AIOStreams `scope: movie` and `scope: series`. They map to:

```text
watchlisted   -> POST /sync/watchlist
unwatchlisted -> POST /sync/watchlist/remove
```

Provider IDs from AIOStreams are resolved to Trakt media first. Trakt watchlist limit responses are treated as non-retryable client-state errors rather than converted into a retrying 5xx loop.

### Bulk played / unplayed marks

When AIOStreams marks a whole season or series it sends `scope: season` or `scope: series`, a `videos[]` list, and `part` / `parts`. The manifest advertises `bulk: true`, so AIOStreams sends at most 500 changed videos in one request instead of one push per episode.

The bridge resolves the parent show once and converts exactly those videos into Trakt's nested history shape:

```json
{
  "shows": [{
    "ids": { "trakt": 42 },
    "seasons": [{
      "number": 2,
      "episodes": [
        { "number": 1, "watched_at": "..." },
        { "number": 2, "watched_at": "..." }
      ]
    }]
  }]
}
```

Routes:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

For `unplayed`, `watched_at` is omitted. Duplicate season/episode rows inside a part are collapsed before the Trakt request. The bridge never sends the show alone because that could affect episodes outside AIOStreams' explicit `videos[]` set.

## Pull cache

Each successful pull stores only:

```text
version
items
fetchedAt
```

The hot copy lives in memory. The same non-authoritative cache entry is persisted in `bridge.db`. After a container recreate, a matching `since` can therefore still be answered without immediately hitting Trakt.

A fresh cache hit requires:

```text
request.since == cache.version
cache age < PULL_TTL_SECONDS
```

A transient-error fallback additionally requires:

```text
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Neither cache path ever returns authoritative `watched` or `watchlist` state.

## Diagnostics

The profile UI shows push and pull activity in the configured timezone.

Push delivery retries are grouped by stable AIOStreams event ID and surface as `recovered` after a later successful retry. Pull polling is different: repeated requests can legitimately reuse the same `since` cursor, so each pull request stays as its own Recent Events row.

A successful bulk push is logged as the original `played` or `unplayed` event with detail such as `history:bulk-add` / `history:bulk-remove`, including scope, video count and `part` / `parts`.

Fresh pull diagnostics include watched counts and `watchlistItems`. Cache hits are labeled `cached`; detail includes `cacheLayer:"memory"` or `cacheLayer:"sqlite"`. If Trakt returns a transient `429` / `5xx` and a safe matching-version cache is available, the row is labeled `stale` and includes the upstream error/path plus cache age.

Trakt error diagnostics include the upstream endpoint and `Retry-After` when available.

## Backup / disaster recovery

Back up all of the following together:

- `trakt_bridge_data` / `/app/data/bridge.db`
- `.env`
- compose definition
- nginx site configuration

The persisted pull cache lives inside `bridge.db`, but it is not DR-critical. Restoring an old/expired cache is safe because both SQLite expiry and the embedded `fetchedAt` age are rechecked before use.

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
- **v0.2.3:** restart-safe SQLite persistence for the safe pull cache
- **v0.3.0:** bidirectional movie/show watchlist sync
- **v0.3.1:** efficient AIOStreams bulk played/unplayed marks
- **v0.3.2:** next-up and metadata/orphan-ID hardening
- **v1.0:** production migrations, release image workflow and broader compatibility hardening

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

v0.3.1 has no runtime npm dependencies; it uses Node built-ins including `node:sqlite`.
