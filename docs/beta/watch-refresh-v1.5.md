# HomeDocker v1.5 beta — AIO Web/Desktop watch-state refresh

Status: **beta source candidate only**. No host install, no GHCR publication, no
AIOStreams patch, no Trakt writes, and no stable promotion are implied by this PR.

## Focus / non-goals

Fix the observed gap: AIOStreams imports a watched row from HomeDocker Trakt
Bridge but Web/Desktop stays stale until navigation/reload. The upstream
AIOStreams import path does not notify the local provider's
`UserDataChanged` listeners.

**Preserve everything else.** The canonical Trakt source, Bridge pull
identity `trakt`, 60-second Bridge cache, scrobble transport, dedupe,
anti-echo, read-only `compare` reconciler, AIOMetadata `This server only`
and existing v1.4 NextUp Gateway remain unchanged. No replay, database rewrite,
new writer, second container, or bulk repair.

## Minimal beta mechanism

1. The **unchanged AIOStreams** scheduler imports watch state from Bridge.
2. HomeDocker's existing Gateway handles *only* a separately mapped
   `/jellyfin/socket` WebSocket from a signed-in AIO Web/Desktop client.
3. Gateway authenticates the presented AIO session via same-upstream
   `GET /Users/Me` and matches the configured exact Jellyfin user.
4. Gateway transparently forwards the original WebSocket to AIO; remote
   commands/keepalives still travel on that connection.
5. When **one exact connected Bridge sink** in AIO SQLite changes an imported
   row (`origin=import`), a read-only 5s sampler sends an additional
   `UserDataChanged` event with an empty `UserDataList`. AIO Web/Desktop
   already treats this event as a watch-state query invalidation; the original
   upstream WebSocket stays authoritative.

Sampling runs **only while one or more proxied clients are connected**.
Missing/ambiguous sink, mismatched user/persona, unreadable database and a
fresh connection baseline never trigger notifications. No credentials,
watched IDs, raw history or content metadata are logged or stored. AIO SQLite
is opened `readOnly` and `PRAGMA query_only=ON`.

This is **not** a universal Jellyfin push enhancement. Native third-party
clients may ignore a `UserDataChanged` with an empty item list.

## Canary prerequisites (do not auto-apply)

- Existing Bridge v1.4.0 Stable is healthy, one writer, one connected exact
  AIO user/sink; preserve the immutable production image/Compose and Bridge
  SQLite online backup for rollback.
- AIOStreams `ghcr.io/viren070/aiostreams:latest` stays upstream-owned.
- Gateway listener exists and is bound via Docker to host loopback only; no
  direct public exposure of port 7101.
- The AIO SQLite read-only volume is already mounted at the configured
  `AIO_DB_PATH`; the running config must use the same exact source/user/profile.
- Capture the actual AIO Web/Desktop socket path (without its `api_key`
  query). The exact nginx example below assumes `/jellyfin/socket`. A
  different mounted prefix needs its own **exact** path mapping.

**Beta opt-in**, in the Bridge Compose/env only:

```dotenv
JELLYFIN_GATEWAY_ENABLED=true
JELLYFIN_GATEWAY_SOURCE_KIND=aiostreams
JELLYFIN_GATEWAY_WATCH_REFRESH_RELAY=true
# Already configured and validated for the existing Gateway:
# JELLYFIN_GATEWAY_UPSTREAM_URL=http://aiostreams:3000/jellyfin
# JELLYFIN_GATEWAY_USER_ID=<exact-Jellyfin-user-id>
# JELLYFIN_GATEWAY_PROFILE_ID=<existing-exact-Bridge-profile-id>
# AIO_DB_PATH=/aio-data/db.sqlite
# Strongly preferred: set this to the *current exact* connected Bridge
# addon_instance_id after verifying it in AIO watch_sinks.
# AIO_RECONCILE_SINK_INSTANCE_ID=<current-exact-instance-id>
```

The beta flag is **false by default**; it neither enables nor modifies
the existing identity filter. Never change the `compare` reconciler mode
to writeback.

**NGINX exact route only**, add inside the *existing AIO HTTPS server*,
with the actual mapped hostname and `/jellyfin` path:

```nginx
location = /jellyfin/socket {
    proxy_pass http://127.0.0.1:7101/socket;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Leave **all AIO REST**, stream links, Remux, AIOMetadata, NextUp and every
other Jellyfin route pointed at their preexisting destinations. Validate
with `nginx -t` before a controlled reload. If the socket route fails,
**revert that one location immediately**; do not change entire client login
URLs. Never print websocket `api_key` tokens in screenshots/logs.

## Pull frequency — separate config-only canary, not a beta code change

Observed AIO defaults are background `WATCH_STATE_PULL_INTERVAL=1800` (30m),
on-demand `WATCH_STATE_PULL_TTL=300` (5m), sweep 60s. Bridge's manifest
`PULL_HINT_SECONDS=60` does **not** override AIO's scheduler.

A later isolated AIO **configuration-only** canary may use:

```dotenv
WATCH_STATE_PULL_INTERVAL=300
WATCH_STATE_PULL_TTL=60
```

Change the AIO service **only** under its existing Portainer/Compose owner,
capture its current config/image and restart safely if AIO requires it.
Do not blindly redeploy the entire stack, edit upstream code, or make this
a prerequisite to testing UI notifications. A previously persisted
`next_pull_at` may remain on its old schedule until the next completed pull
or a safe manual scheduled-task invocation.

The 5-minute interval **cannot guarantee a 15-second
Trakt-to-Desktop update**. It bounds only the regular background read cadence;
once a change is imported, the relay checks locally about every 5 seconds.

## Acceptance gates for promotion to Stable

1. Node 24 CI unit tests pass, including baseline suppression, exact user/sink,
   disabled-by-default behavior and synthetic end-to-end WebSocket refresh.
2. With AIO background interval unchanged, an observed external Trakt watched
   change is imported into AIO and appears in already-open Web/Desktop without
   user navigation/reload **after the import**.
3. The unmodified upstream WebSocket continues carrying normal AIO events;
   no regression to local playback, remote commands or login.
4. Real AIO Web/Desktop reconnect after a socket outage succeeds; disabling
   the nginx exact route restores original upstream socket behavior promptly.
5. All Bridge invariants remain intact: no duplicate History, no false
   played/unplayed, no native-Trakt echo, no 429/422 amplification, `compare`
   remains read-only, NextUp behavior unchanged, identity-drift monitor PASS.
6. After optional 300s scheduler canary, measure 24-hour Trakt authenticated
   GET/POST, 429/Retry-After, Bridge pull `ok/stale/error`, AIO pull errors
   and observed import-to-UI latency; promote only if stable.

**Rollback:** remove the one nginx `location` and reload nginx; set
`JELLYFIN_GATEWAY_WATCH_REFRESH_RELAY=false` in Bridge env, restore the
previous immutable Bridge image/Compose by the qualified Bridge-only procedure
if needed, and separately revert AIO pull settings if tested. No database
restore or history edit is part of routine rollback.

## Known limitations

- The AIO SQL marker detects changes in currently retained Bridge-imported
  rows via `MAX(updated_at)` and count; it does not guarantee every deletion
  or same-millisecond overwrite causes a distinct marker.
- An AIO Web/Desktop browser tab that has not connected its WebSocket cannot
  receive a push. Refresh on next app open remains the upstream client's job.
- When the AIO source/user/sink is ambiguous, **no injected events are sent**.
- This beta does not optimize Trakt's low-cost `last_activities` probe, change
  Bridge TTL, or attempt sub-15-second full-history pulling.
