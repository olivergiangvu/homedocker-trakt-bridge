# HomeDocker Trakt Bridge

Self-hosted **bidirectional AIOStreams `watch_state` v2 ↔ Trakt bridge** for Jellyfin-compatible playback clients such as Infuse, Swiftfin, Strand and Odin.

**Current release: v0.3.7 — v0.3.6 Trakt-preserving identity compatibility plus a fail-safe guard that suppresses Trakt scrobbles below 1% progress.**

## What it does

### AIOStreams → Trakt

- `start` → Trakt scrobble start when progress is at least 1%
- `pause` → Trakt scrobble pause when progress is at least 1%
- unfinished AIOStreams `stop` → Trakt scrobble pause when progress is at least 1%
- sub-1% start/pause/unfinished-stop events → acknowledged and ignored before Trakt
- finished AIOStreams `stop` → Trakt scrobble stop; an explicit played stop below 1% falls back to history add
- single `played` / `unplayed` → add/remove history
- whole-season/show `played` / `unplayed` → one nested Trakt history sync request per AIOStreams bulk part
- redundant same-kind per-episode echoes covered by a recent successful bulk mark → safely ignored
- `watchlisted` / `unwatchlisted` → add/remove movie/show from Trakt watchlist
- push identity resolution merges `metaId` with supported IMDb/TMDb/TVDb IDs and can fail over across known provider spellings on a 404
- a successful AIOStreams episode `stop` can teach the bridge the IMDb spelling AIOStreams actually used for that stable Trakt show
- stable-event idempotency and retry-safe diagnostics

### Trakt → AIOStreams

- paused movie/episode progress → Continue Watching
- watched movies and episodes → authoritative watched state
- movie/show Trakt watchlist → watchlist/favourites
- watched show `counts` are emitted under every representable IMDb/TMDb/TVDb spelling present after normalization
- `watched.nextUp` is forwarded only when Trakt already supplied a usable exact next episode
- one `version` / `since` cursor covers watched + supported watchlist activity + pull identity representation
- repeated unchanged pulls can be served from restart-safe cache
- transient Trakt `429` / `5xx` can use bounded stale cache when safe

Trakt remains the canonical long-term tracker source for watched history and watchlist state. AIOStreams remains the Jellyfin-compatible playback/state surface.

## Why v0.3.7 exists

Production observed AIOStreams retrying both `pause` and unfinished `stop` events at exactly `0%` progress. The bridge previously forwarded those events to Trakt `/scrobble/pause`, where Trakt rejected them with HTTP `422` because they were below the service's minimum meaningful scrobble progress.

v0.3.7 adds a fail-safe boundary before any Trakt scrobble request:

```text
progress < 1%
  start / pause / unfinished stop -> ignore + 204
  explicit played stop           -> history-add

progress >= 1%
  existing scrobble behavior remains unchanged
```

Ignored events are still marked processed, so stable AIOStreams retry IDs recover instead of looping on a guaranteed upstream `422`.

This is a push-only safety fix. It does **not** change the v0.3.6 pull representation, `PULL_IDENTITY_MODE`, watch-state schema, pull cache namespace, profile tokens, or learned aliases.

## Why v0.3.6 exists

v0.3.4 introduced persistent IMDb-to-IMDb alias reconciliation and rewrote future Trakt pull rows to the IMDb spelling learned from successful AIOStreams playback. v0.3.5 corrected alias learning for unfinished AIOStreams `stop` events.

That solved one AIOStreams-only duplicate case but exposed a compatibility regression for clients such as Strand that use **native Trakt and AIOStreams at the same time**:

```text
Native Trakt in Strand
    Trakt IMDb = tt44051354

Bridge v0.3.4/v0.3.5
    learned AIOStreams IMDb = tt44094505
    Trakt pull tt44051354 -> rewritten to tt44094505

Strand sees two valid identities for the same title
    tt44051354 + tt44094505
    -> duplicate Continue Watching cards
```

Before v0.3.4, both native Trakt and the bridge preserved the Trakt IMDb spelling, so they naturally converged.

v0.3.6 restores that compatibility as an explicit mode instead of removing the learned-alias system:

```env
PULL_IDENTITY_MODE=trakt
```

### Pull identity modes

`PULL_IDENTITY_MODE=trakt` **(default)**

- preserves the IMDb spelling returned by Trakt on pull;
- matches v0.3.3-style pull identity behavior;
- recommended when a client uses native Trakt in parallel with AIOStreams;
- learned aliases remain stored for diagnostics and optional future use;
- alias revision does not rewrite the pull representation.

`PULL_IDENTITY_MODE=aiostreams`

- keeps the v0.3.5 learned-alias rewrite behavior;
- rewrites Trakt's IMDb spelling to the AIOStreams-preferred IMDb for the same stable Trakt show;
- useful for clients that rely only on AIOStreams watch-state and need AIOStreams-local identity convergence.

No show IDs are hard-coded. The production regression pair used for tests is:

```text
Trakt IMDb:      tt44051354
AIOStreams IMDb: tt44094505
TMDb:            276470
TVDb:            480791
```

## Safety decisions

AIOStreams currently uses a 90% watched threshold while Trakt `/scrobble/stop` can mark watched above 80%. The bridge therefore maps `stop + played:false` to Trakt `/scrobble/pause`, preventing 80–89% progress from being promoted to watched by Trakt.

v0.3.7 additionally refuses to send a Trakt scrobble below 1% progress. This prevents deterministic `422` retry loops for playback sessions that open and immediately pause/stop at the beginning.

Alias learning remains evidence-based:

- the original AIOStreams event must be an episode `stop`;
- Trakt must successfully accept the translated scrobble request first;
- the event must carry a valid IMDb `metaId`;
- the show must resolve to a stable Trakt show ID;
- `pause`, `played`, `unplayed`, bulk history and watchlist mutations cannot teach or override the playback preference.

The pull-side `watched` and `watchlist` blocks are authoritative. If complete changed state cannot be read from Trakt, the bridge fails the pull rather than returning destructive empty state.

Cached and stale-cache responses contain only `version + items`; they never fabricate authoritative `watched` or `watchlist` blocks.

For bulk played/unplayed marks, the bridge writes only the explicit `videos[]` AIOStreams says changed. Anime/absolute-number episode spaces remain fail-closed.

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
                +-- pull identity mode
                |     trakt      -> preserve Trakt IMDb
                |     aiostreams -> learned alias rewrite
                +-- sub-1% scrobble guard
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

For the HomeDocker production topology, AIOMetadata should not be a second tracker-history authority when Trakt Bridge is the chosen history source. AIOMetadata can still receive playback and fan out to secondary trackers.

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
PULL_IDENTITY_MODE=trakt
```

`PULL_IDENTITY_MODE` accepts only `trakt` or `aiostreams`. Default: `trakt`.

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
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.3.7"}
```

## Reverse proxy

Recommended HomeDocker deployment keeps port 7000 loopback-only and lets host nginx terminate TLS. Do not expose container port 7000 directly to the Internet.

## Connect Trakt

Open the setup page for the bridge and connect the profile to Trakt. The resulting AIOStreams manifest URL is a credential and must not be published.

## AIOStreams manifest

v0.3.7 advertises the same Watch State v2 capabilities as v0.3.6:

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

### Learned IMDb aliases

The learned alias store introduced in v0.3.4 and fixed in v0.3.5 remains present in v0.3.7:

```text
profile + stable Trakt show ID
    -> preferred AIOStreams IMDb
    -> Trakt IMDb spelling
    -> revision
```

The difference introduced by v0.3.6 is **how pull output uses it**:

```text
PULL_IDENTITY_MODE=trakt
    keep Trakt IMDb in playback/watched/watchlist pull rows

PULL_IDENTITY_MODE=aiostreams
    rewrite Trakt IMDb to learned AIOStreams IMDb
```

The translated upstream scrobble may be either `/scrobble/stop` or `/scrobble/pause`. The learning evidence is the original successful AIOStreams `stop` event. A sub-1% ignored stop does not teach or change an alias because no upstream scrobble succeeded.

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

v0.3.7 intentionally keeps the v0.3.6 pull representation schema:

```text
state cursor schema: watch-state-v0.3.6
```

The selected `PULL_IDENTITY_MODE` participates in the version basis, so switching modes forces a new authoritative representation even if Trakt watched/watchlist timestamps did not change.

The one-time v0.3.5 → v0.3.6 `pull-state:v5:*` cleanup remains a v0.3.6 migration step; upgrading v0.3.6 → v0.3.7 requires no additional pull-cache purge.

## Next Up behavior

AIOStreams derives the actual next episode locally from imported watched rows and metadata. The bridge forwards `watched.nextUp` only when the upstream Trakt row already contains a usable exact `next_episode`.

It does not guess season boundaries or issue one Trakt progress request per watched show.

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

Recent Events shows push and pull activity in the configured timezone. Learned alias diagnostics can include:

```text
Trakt show id
preferred AIOStreams IMDb
Trakt IMDb spelling
previous preference, if any
alias revision
```

In `trakt` mode, learned aliases remain diagnostic/persisted evidence but do not rewrite Trakt pull identity. Sub-1% playback events are recorded as ignored instead of surfacing as retrying Trakt 422 errors.

## Backup / disaster recovery

Back up together:

- `trakt_bridge_data` / `/app/data/bridge.db`
- `.env`
- compose definition
- nginx site configuration

The pull cache and recent bulk coverage markers are not DR-critical. Learned aliases are persisted in `bridge.db`. `BRIDGE_SECRET_KEY` remains DR-critical.

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
- **v0.3.4:** persistent playback-learned IMDb alias reconciliation
- **v0.3.5:** unfinished-stop alias-learning fix + immediate cache invalidation
- **v0.3.6:** configurable pull identity; default Trakt-preserving mode restores native-Trakt compatibility
- **v0.3.7:** ignore sub-1% scrobbles before Trakt to prevent deterministic 422 retry loops
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

v0.3.7 has no runtime npm dependencies; it uses Node built-ins including `node:sqlite`.
