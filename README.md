# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse, Swiftfin, Strand and Odin.

**Current release: v0.3.5 — playback, watched history, watchlist sync, bulk marks, duplicate-safe reconciliation, cross-ID metadata hardening, and unfinished-stop-safe IMDb-alias convergence.**

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start
- `pause` → Trakt scrobble pause
- unfinished AIOStreams `stop` → Trakt scrobble pause
- finished AIOStreams `stop` → Trakt scrobble stop
- single `played` / `unplayed` → add/remove history
- whole-season/show `played` / `unplayed` → one nested Trakt history sync request per AIOStreams bulk part
- redundant same-kind per-episode echoes covered by a recent successful bulk mark → safely ignored
- `watchlisted` / `unwatchlisted` → add/remove movie/show from Trakt watchlist
- push identity resolution merges `metaId` with supported IMDb/TMDb/TVDb IDs and can fail over across known provider spellings on a 404
- a successful **AIOStreams episode `stop`** can teach the bridge the IMDb spelling AIOStreams actually used for that stable Trakt show
- stable-event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → Continue Watching
- watched movies and episodes → authoritative watched state
- movie/show Trakt watchlist → watchlist/favourites
- learned show aliases rewrite Trakt's IMDb spelling back to the AIOStreams-preferred IMDb spelling for the same stable Trakt show
- watched show `counts` are emitted under every representable IMDb/TMDb/TVDb spelling present after normalization
- `watched.nextUp` is forwarded only when Trakt already supplied a usable exact next episode; the bridge does not guess episode numbering or fan out one request per show
- one `version` / `since` cursor covers watched + supported watchlist activity + learned identity-alias revision
- repeated unchanged pulls can be served from restart-safe cache
- transient Trakt `429` / `5xx` can use bounded stale cache when safe

Trakt remains the canonical long-term tracker source for watched history and watchlist state. AIOStreams remains the Jellyfin-compatible playback/state surface.

## Why v0.3.5 exists

v0.3.4 introduced persistent IMDb-to-IMDb alias reconciliation. Production then exposed one important event-semantics edge case:

```text
AIOStreams event = stop, played=false
        |
        v
Bridge intentionally maps it to Trakt /scrobble/pause
```

The original v0.3.4 implementation learned an alias only when the translated **Trakt action** was `stop`. That meant a perfectly valid unfinished AIOStreams stop — the normal case for Continue Watching — could not teach the alias.

v0.3.5 fixes the trigger:

```text
successful AIOStreams episode stop
        |
        +-- finished   -> Trakt /scrobble/stop
        |
        +-- unfinished -> Trakt /scrobble/pause
        |
        v
learn preferred AIOStreams IMDb after the upstream request succeeds
```

A plain AIOStreams `pause` still cannot teach or override an alias.

## Safety decisions

AIOStreams currently uses a 90% watched threshold while Trakt `/scrobble/stop` can mark watched above 80%. The bridge therefore maps `stop + played:false` to Trakt `/scrobble/pause`, preventing 80–89% progress from being promoted to watched by Trakt.

Alias learning is evidence-based:

- the original AIOStreams event must be an episode `stop`;
- Trakt must successfully accept the translated scrobble request first;
- the event must carry a valid IMDb `metaId`;
- the show must resolve to a stable Trakt show ID;
- `pause`, `played`, `unplayed`, bulk history and watchlist mutations cannot teach or override the playback preference.

No show IDs are hard-coded and alias learning adds no per-show Trakt API fan-out.

The pull-side `watched` and `watchlist` blocks are authoritative. If complete changed state cannot be read from Trakt, the bridge fails the pull rather than returning destructive empty state.

Cached and stale-cache responses contain only `version + items`; they never fabricate authoritative `watched` or `watchlist` blocks. A successful push that changes history/watchlist state or learns an identity alias invalidates the safe pull cache.

For bulk played/unplayed marks, the bridge writes only the explicit `videos[]` AIOStreams says changed. It never sends a bare show/season that could affect episodes outside that set.

Some Jellyfin-compatible clients issue per-episode marks after a successful season/show bulk mark. v0.3.2+ records the exact videos covered by the successful bulk mutation and suppresses only later **same-kind** single-episode echoes inside `BULK_SINGLE_DEDUPE_SECONDS`. Opposite-state events are never suppressed.

Anime/absolute-number episode spaces remain fail-closed. Trakt-only IDs that cannot be represented as IMDb/TMDb/TVDb are skipped instead of becoming invented AIOStreams IDs.

## Current architecture

```text
Infuse / Swiftfin / Strand / Odin / Jellyfin-compatible client
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
                +-- provider-ID normalizer
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

CrossWatch and Remux are not part of this critical path. AIOMetadata may independently provide metadata/watch-state to AIOStreams, but it is **not** a Trakt connection or Trakt authority.

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
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.3.5"}
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

Open the setup page for the bridge and connect the profile to Trakt. The resulting AIOStreams manifest URL is a credential and must not be published.

## AIOStreams manifest

v0.3.5 advertises:

```text
watch_state version: 2
push: start, pause, stop, played, unplayed, watchlisted, unwatchlisted
bulk: true
pull: items + watched + watchlist
ttl: 900s by default
```

## Identity hardening

AIOStreams push events can include both `metaId` and shared provider IDs. The bridge normalizes supported conventional identities into one candidate set:

```text
metaId = tt0903747   -> imdb=tt0903747
metaId = tmdb:1396   -> tmdb=1396
metaId = tvdb:81189  -> tvdb=81189
```

Explicit valid shared IDs remain authoritative while `metaId` fills missing candidates. Resolution attempts known providers in deterministic order. A Trakt 404 for one spelling is treated as an alias miss; auth failures, `429`, and `5xx` are not swallowed.

### Learned IMDb aliases in v0.3.5

Some real titles legitimately resolve to more than one IMDb ID. If AIOStreams plays a show as IMDb A while Trakt returns the same stable show as IMDb B, AIOStreams can retain two watch-state identities because it cannot safely infer arbitrary IMDb A ↔ IMDb B equivalence.

The bridge learns from real playback instead of guessing:

```text
AIOStreams stop: metaId=IMDb A
        |
        v
Bridge resolves stable Trakt show X
Trakt spelling for X = IMDb B
        |
        v
Trakt accepts translated scrobble
        |
        v
persist profile + X -> preferred IMDb A
        |
        v
future Trakt pull for X rewrites IMDb B -> IMDb A
```

The translated upstream scrobble may be either `/scrobble/stop` or `/scrobble/pause`. The learning evidence is the **original successful AIOStreams stop event**, not the translated Trakt action name.

Only the IMDb spelling is rewritten. Stable Trakt, TMDb and TVDb IDs remain intact.

## Pull behavior

Fresh pulls read:

```text
/sync/last_activities
/sync/playback/movies?extended=full
/sync/playback/episodes?extended=full
```

When the state cursor changed they additionally read all pages of:

```text
/sync/watched/movies
/sync/watched/shows?extended=progress
/sync/watchlist/movies/added/desc
/sync/watchlist/shows/added/desc
```

v0.3.5 intentionally keeps the v0.3.4 representation migration identifiers:

```text
state cursor schema: watch-state-v0.3.4
persisted pull cache: pull-state:v5:
```

This is intentional: v0.3.5 fixes the event trigger and cache invalidation semantics; it does not introduce another pull-payload schema. Alias revision remains part of the state cursor, so learning or changing a preferred alias changes the version even when Trakt watched/watchlist timestamps did not move.

When an alias is newly learned, the push result carries `identityAlias` and v0.3.5 invalidates the pull cache immediately — including the common unfinished-stop case whose Trakt action is `scrobble:pause`.

## Next Up behavior

AIOStreams derives the actual next episode locally from imported watched state + metadata. The bridge forwards `watched.nextUp` only when the upstream Trakt row already contains a usable exact `next_episode`.

It does **not** guess season boundaries or issue one Trakt progress request per watched show.

## Pull cache

A successful pull caches only:

```text
version
items
fetchedAt
```

Fresh cache hits require:

```text
request.since == cache.version
cache age < PULL_TTL_SECONDS
```

Transient stale fallback additionally requires:

```text
cache age < PULL_STALE_IF_ERROR_SECONDS
upstream status == 429 or 5xx
```

Neither cache path returns authoritative watched/watchlist blocks.

## Diagnostics

Recent Events shows push and pull activity in the configured timezone. When a successful AIOStreams episode stop learns or changes a show preference, the event detail can include:

```text
Trakt show id
preferred AIOStreams IMDb
Trakt IMDb spelling
previous preference, if any
alias revision
```

Fresh pull diagnostics include item/watched/watchlist counts and the source (`trakt`, `coalesced`, `cache`, or bounded `stale-cache`).

## Backup / disaster recovery

Back up together:

- `trakt_bridge_data` / `/app/data/bridge.db`
- `.env`
- compose definition
- nginx site configuration

The pull cache and recent bulk coverage markers are not DR-critical. Learned aliases are persisted in `bridge.db`; losing them is recoverable because a later successful AIOStreams episode stop can teach them again. `BRIDGE_SECRET_KEY` remains DR-critical.

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
- **v0.3.5:** unfinished AIOStreams stop alias-learning fix + immediate cache invalidation on alias change
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

v0.3.5 has no runtime npm dependencies; it uses Node built-ins including `node:sqlite`.
