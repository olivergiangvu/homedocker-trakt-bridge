# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse and Swiftfin.

**Current release: v0.3.4 — playback, watched history, watchlist sync, bulk marks, duplicate-safe reconciliation, cross-ID metadata hardening, and learned IMDb-alias convergence.**

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start
- `pause` → Trakt scrobble pause
- unfinished `stop` → Trakt pause
- finished `stop` → Trakt stop
- single `played` / `unplayed` → add/remove history
- whole-season/show `played` / `unplayed` → one nested Trakt history sync request per AIOStreams bulk part
- redundant same-kind per-episode echoes covered by a recent successful bulk mark → safely ignored
- `watchlisted` / `unwatchlisted` → add/remove movie/show from Trakt watchlist
- push identity resolution uses the shared `ids` vocabulary first and can recover a provider ID from `metaId` when `ids` is missing or incomplete
- stale provider aliases can fall through to another known IMDb/TMDb/TVDb alias instead of failing the whole event on the first 404
- a successful episode playback `stop` can teach the bridge the AIOStreams-preferred IMDb spelling for that stable Trakt show
- stable-event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → Continue Watching
- watched movies and episodes → watched state
- movie/show Trakt watchlist → watchlist/favourites
- learned show aliases rewrite Trakt's IMDb spelling back to the IMDb spelling AIOStreams actually used for successful playback of the same Trakt show
- watched show `counts` are emitted under **every representable IMDb/TMDb/TVDb spelling** returned after normalization, reducing raw-ID/orphan joins when AIOStreams metadata uses a different spelling
- `watched.nextUp` is emitted only when the upstream Trakt row itself contains a usable `next_episode`; the bridge does not invent episode numbers or fan out one progress request per show
- one `version` / `since` cursor covers watched + supported watchlist activity + learned identity-alias revision
- repeated unchanged pulls are served from a restart-safe matching-version cache
- transient Trakt `429` / `5xx` can use bounded stale cache when safe

Trakt remains the canonical long-term tracker source for watched history and watchlist state. AIOStreams remains the Jellyfin-compatible playback/state surface.

## Safety decisions

AIOStreams currently uses a 90% watched threshold while Trakt `/scrobble/stop` can mark watched above 80%. The bridge therefore maps `stop + played:false` to Trakt `/scrobble/pause` so 80–89% progress is never promoted to watched by Trakt.

The pull-side `watched` and `watchlist` blocks are authoritative. If the complete changed state cannot be read from Trakt, the bridge fails the pull rather than returning destructive empty state.

Cached and stale-cache responses contain `version + items` only. They never fabricate authoritative `watched` or `watchlist` blocks. Successful pushes that can change authoritative history/watchlist state invalidate the pull cache.

For bulk played/unplayed marks, the bridge writes only the explicit `videos[]` AIOStreams says changed. It never sends a bare show/season that could affect tracker episodes outside that set.

Some Jellyfin-compatible clients issue per-episode marks after a successful season/show bulk mark. v0.3.2+ records the exact videos covered by a successful bulk mutation and suppresses only later **same-kind** single-episode echoes inside `BULK_SINGLE_DEDUPE_SECONDS`. Opposite-state events are never suppressed.

v0.3.3+ does not guess unsupported identities. Anime/absolute-number episode spaces remain fail-closed. Trakt-only IDs that cannot be represented as IMDb/TMDb/TVDb are still skipped on pull instead of becoming invented AIOStreams IDs.

v0.3.4 does not hard-code IMDb aliases. A show alias is learned only after Trakt accepts a real episode playback `stop`, keyed by the stable Trakt show ID, and only when AIOStreams supplied a valid IMDb `metaId`. `played`, `unplayed`, bulk and watchlist mutations cannot teach or override the playback preference.

## Current architecture

```text
Infuse / Swiftfin / Jellyfin-compatible client
                    |
                    v
               AIOStreams
             watch_state v2
               /         \
              / push      \ pull
             v              ^
HomeDocker Trakt Bridge ----+
        |      ^
        |      +-- matching-version cache
        |      |     +-- in-memory hot copy
        |      |     +-- SQLite persistence
        |      +-- in-flight pull coalescing
        |      +-- bounded stale fallback on 429/5xx
        |
        +-- provider ID normalizer
        |     metaId + ids -> IMDb / TMDb / TVDb candidates
        +-- learned show alias store
        |     profile + Trakt show id -> preferred AIOStreams IMDb
        +-- /scrobble/*
        +-- /sync/history + bulk history
        +-- recent bulk coverage dedupe
        +-- /sync/watchlist/*
        +-- /sync/playback/*
        +-- /sync/watched/*
        +-- /sync/last_activities
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

## Configuration

```bash
cp .env.example .env
openssl rand -hex 32   # BRIDGE_SECRET_KEY
openssl rand -hex 32   # ADMIN_KEY
```

Minimum settings:

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
```

`BRIDGE_SECRET_KEY` is DR-critical. Losing it makes stored Trakt tokens unreadable and changes derived setup/addon credentials.

## Start

```bash
docker compose -f compose.example.yml up -d --build
```

Health check:

```bash
curl http://127.0.0.1:7000/health
```

Expected:

```json
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.3.4"}
```

## Reverse proxy

Recommended HomeDocker deployment keeps port 7000 loopback-only and lets host nginx terminate TLS:

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

## Connect Trakt

Open:

```text
https://YOUR-BRIDGE-DOMAIN/setup?key=YOUR_ADMIN_KEY
```

Create a profile → **Connect Trakt** → authorize. The profile page displays the AIOStreams manifest URL; treat it as a credential.

## AIOStreams manifest

v0.3.4 advertises:

```text
watch_state version: 2
push: start, pause, stop, played, unplayed, watchlisted, unwatchlisted
bulk: true
pull: items + watched + watchlist
ttl: 900s by default
```

## Identity hardening

AIOStreams push events normally include both `metaId` and `ids`. In practice, metadata addons and client paths can produce incomplete shared IDs. v0.3.3 normalizes them into one candidate set:

```text
metaId = tt0903747        -> imdb=tt0903747
metaId = tmdb:1396        -> tmdb=1396
metaId = tvdb:81189       -> tvdb=81189
```

Explicit valid `ids` values override/fill that set. Resolution attempts providers in deterministic order. A Trakt 404 for one spelling is treated as an alias miss and the next known provider is tried; `429`, auth failures and `5xx` still propagate normally.

On pull, the normal preferred item spelling remains:

```text
IMDb -> TMDb -> TVDb
```

but `watched.counts` is written under every representable alias after normalization. AIOStreams' Watch State v2 contract explicitly allows counts keyed by every ID a show answers to. This improves joins when the imported episode is IMDb-spaced but the active metadata surface was discovered under `tmdb:` or `tvdb:`.

### Learned IMDb aliases in v0.3.4

Some real titles legitimately resolve to more than one IMDb ID. If AIOStreams plays a show as IMDb A while Trakt returns the same stable Trakt show as IMDb B, AIOStreams cannot currently infer that IMDb A and IMDb B are aliases and can create duplicate watch-state rows.

v0.3.4 learns from successful playback rather than guessing:

```text
AIOStreams stop: metaId=IMDb A
        |
        v
Bridge resolves stable Trakt show X
Trakt spelling for X = IMDb B
        |
        v
persist profile + X -> preferred IMDb A
        |
        v
future Trakt pull for X is rewritten to IMDb A
```

Only the IMDb spelling is rewritten. The Trakt/TMDb/TVDb IDs are retained. Learning does not introduce another Trakt API call; it reuses the show resolution already needed for the episode scrobble.

### Next Up behavior

AIOStreams derives the actual next episode locally from watched state + metadata. Its pull contract also accepts `watched.nextUp` hints.

The conservative policy remains:

- if a Trakt progress-shaped watched row already contains `next_episode`, the bridge forwards that exact episode and the show's last-watch timestamp;
- otherwise it omits `nextUp` for that show;
- it does **not** guess `season+1`, assume contiguous episodes, or call `/shows/:id/progress/watched` once for hundreds of shows.

This keeps Next Up accurate without recreating the Trakt rate-limit problem the bridge cache was designed to solve.

## Pull behavior

Fresh pulls read:

```text
/sync/last_activities
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

When the state version changed they additionally read all pages of:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

v0.3.4 advances the internal state-version schema to `watch-state-v0.3.4`, includes the learned identity-alias revision in the cursor, and uses persisted cache namespace `pull-state:v5:`. The first post-upgrade pull therefore cannot be answered by a still-fresh v0.3.3 cache. Learning a new preferred alias also changes the cursor after the successful stop invalidates the pull cache.

## Bulk played / unplayed

AIOStreams `bulk=true` season/show marks carry explicit `videos[]` and `part/parts`. Each part becomes one nested Trakt history request:

```text
played   -> POST /sync/history
unplayed -> POST /sync/history/remove
```

After a successful bulk write, one short-lived coverage marker is stored per listed video. A later single episode event is ignored only when all of these match:

```text
same profile
same event kind
same video id
single.at >= bulk.at
single.at - bulk.at <= BULK_SINGLE_DEDUPE_SECONDS
```

Suppressed events are marked processed, logged as `ignored: covered_by_recent_bulk`, return `204`, and do not touch Trakt.

## Pull cache

A successful pull caches only:

```text
version
items
fetchedAt
```

The hot copy lives in memory and the same non-authoritative entry is persisted in `bridge.db`.

Fresh cache hit:

```text
request.since == cache.version
cache age < PULL_TTL_SECONDS
```

Transient-error fallback additionally requires:

```text
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Neither cache path returns authoritative watched/watchlist blocks.

## Diagnostics

Recent Events shows push and pull activity in the configured timezone.

Fresh pull detail includes:

```text
items
watchedMovies
watchedEpisodes
watchedNextUp
watchlistItems
source
```

Cache hits include `cacheLayer:"memory"` or `cacheLayer:"sqlite"`. Bulk echo suppression is logged as `ignored` with the covering bulk event ID and video ID. When a successful episode stop learns or changes a show preference, its event detail includes the Trakt show ID, preferred AIOStreams IMDb, Trakt IMDb spelling and alias revision.

## Backup / disaster recovery

Back up together:

- `trakt_bridge_data` / `/app/data/bridge.db`
- `.env`
- compose definition
- nginx site configuration

The pull cache and bulk coverage markers are not DR-critical. Learned show aliases are persisted in `bridge.db`; losing them is recoverable because a later successful episode stop can teach them again. `BRIDGE_SECRET_KEY` is DR-critical.

## Release history

See [CHANGELOG.md](CHANGELOG.md).

- **v0.1.0:** push MVP
- **v0.1.1:** diagnostics hardening
- **v0.2.0:** Trakt → AIOStreams playback + watched pull
- **v0.2.1:** pull diagnostics correctness
- **v0.2.2:** pull cache, coalescing, stale-on-transient-error
- **v0.2.3:** restart-safe SQLite pull cache
- **v0.3.0:** bidirectional movie/show watchlist
- **v0.3.1:** season/show bulk played/unplayed
- **v0.3.2:** duplicate-safe bulk/single reconciliation
- **v0.3.3:** provider-ID normalization, alias-aware watched metadata, safe next-up hints
- **v0.3.4:** persistent playback-learned IMDb alias reconciliation for the same Trakt show
- **v1.0:** production migrations, release image workflow and broader compatibility hardening

## Security

- Trakt access/refresh tokens are AES-256-GCM encrypted at rest.
- Tokens never appear in addon URLs.
- setup/addon keys are HMAC-derived from the DR-critical bridge secret.
- admin/setup/manifest responses use `Cache-Control: no-store`.
- the manifest URL itself is a credential; do not publish it.

## Development

Requires Node.js 24+.

```bash
npm run check
npm test
```

v0.3.4 has no runtime npm dependencies; it uses Node built-ins including `node:sqlite`.
