# Architecture

HomeDocker Trakt Bridge exists to keep **one canonical watched/resume authority** while still exposing state through AIOStreams to Jellyfin-compatible clients.

## Canonical topology

```text
                         READ / HISTORY AUTHORITY

                               Trakt
                                 ^
                                 |
                        HomeDocker Trakt Bridge
                                 ^
                                 |
                              AIOStreams
                    Jellyfin-compatible state surface
                                 |
              +------------------+------------------+
              |                  |                  |
            Strand              Odin          Remux / Trellis


                         METADATA / FAN-OUT

                            AIOMetadata
                      metadata + local playstate
                                 |
                   optional secondary tracker writes
                                 |
                     Simkl / MDBList / others
```

## Responsibility split

| Component | Responsibility |
| --- | --- |
| Trakt | canonical watched/resume history |
| Trakt Bridge | translate/synchronize AIOStreams watch state and Trakt; optionally serve a read-only NextUp Gateway in the same process |
| AIOStreams | Jellyfin-compatible playback/state surface |
| AIOMetadata | metadata/catalog, local playstate, optional secondary-tracker write fan-out |
| Clients | playback and presentation |

The core design rule is simple:

> Do not configure multiple external tracker-history sources to write competing watched/resume state back into the same AIOStreams surface.

## Read path

Trakt -> AIOStreams:

```text
Trakt playback/history/watchlist
        |
        v
Trakt Bridge pull
        |
        v
AIOStreams watch_state
        |
        v
Jellyfin-compatible clients
```

Bridge pull provides:

- paused/resume items
- watched movies
- watched episodes
- movie/show watchlist state
- exact Next Up only when upstream data supplies it

## Write path

AIOStreams -> Trakt:

```text
client playback/state event
        |
        v
AIOStreams watch_state push
        |
        v
Trakt Bridge
        |
        v
Trakt scrobble / history / watchlist
```

The bridge handles:

- scrobble start/pause/stop
- watched/unwatched history
- season/show bulk history
- watchlist add/remove
- retry-safe idempotency

## AIOMetadata boundary

For the HomeDocker owner Jellyfin user:

```text
Trackers = This server only
```

This keeps external tracker history from being read back into AIOMetadata's Jellyfin Continue Watching / watched / Next Up surface.

Enabled Watch Tracking writes can still fan playback out independently.

See [INTEGRATIONS.md](INTEGRATIONS.md).

## Native Trakt + AIOStreams coexistence

HomeDocker uses:

```env
PULL_IDENTITY_MODE=trakt
```

This preserves Trakt's provider spelling on pull, allowing clients that also use native Trakt to converge on the same identity namespace.

The learned-alias implementation and historical regression fixture are developer concerns and are documented in [DEVELOPMENT.md](DEVELOPMENT.md).

## v1.4 read-only NextUp Identity Gateway

The v1.4 Gateway is an **optional read-only HTTP projection within the existing Bridge process**. It is not a second Trakt writer, a second database, an alternate watched/resume read authority or a general Jellyfin proxy.

```text
authorized client's approved NextUp-only route (requires explicit operator routing)
             |
       NextUp Gateway (inside Bridge; listener separately provisioned)
             |
    authenticated same-user upstream AIOStreams GET /Shows/NextUp
             |
    verified series identity graph + conservative filter
             |
    normalized eligible TV alias collision or original response unchanged
```

- Listener defaults OFF; configure exact upstream/source/user/profile mapping explicitly.
- When validly provisioned, the Identity Filter defaults AUTO ON. An authenticated per-source manual OFF override persists across restarts.
- Filter eligibility is limited to verified **TV-series aliases in supported short first-page NextUp responses**. Invalid, full/paginated, ambiguous or unsupported requests pass through unmodified; no speculative hiding or shelf insertion.
- Movies, `/UserItems/Resume`, general Jellyfin endpoints, playback, Trakt history and AIO SQLite are **not** rewritten.
- Gateway `7101` is an internal listener, never a public port or a wholesale replacement for Jellyfin/AIOStreams base URLs. Actual client routing is an independent acceptance gate.

Read the [stable operator guide](STABLE-V1.4-OPERATIONS.md) before enabling or routing a Gateway. Historical R23D host-fencing experiments are archived, not bundled as claims of general kernel/boot-time enforcement.

## Runtime boundary

```text
Internet
   |
 host nginx / HTTPS
   |
 127.0.0.1:7000
   |
 Trakt Bridge container
   |
 /app/data/bridge.db
```

The bridge does not terminate TLS itself in the HomeDocker deployment.

## Persistence

The persistent SQLite database stores:

- profiles
- encrypted Trakt tokens
- OAuth state
- processed event IDs
- pull/media cache
- learned identity evidence
- event history

The Bridge uses explicit schema tracking (the initial schema remains version 1):

```text
PRAGMA user_version = 1
```

The binary fails closed if the DB schema is newer than it supports.

## Cache model

There are two pull-cache layers:

```text
memory
SQLite media_cache
```

Fresh matching pulls can be served from cache.

Bounded stale fallback is allowed only when it is safe and the Trakt failure is transient, such as rate limiting or upstream 5xx.

The current persisted pull namespace remains:

```text
pull-state:v5:<profileId>
```

## Operator vs developer surfaces

Architecture intentionally separates normal operations from forensic diagnostics:

```text
Dashboard      = connection, manifest, sync health, active errors, events
/status        = structured operational + identity diagnostics
SQLite / tests = forensic/developer evidence
```

The web UI should answer operator questions, not duplicate developer documentation.
