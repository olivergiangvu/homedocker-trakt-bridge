# HomeDocker-only Jellyfin NextUp Gateway — opt-in canary

**NOT DEPLOYED. PR #94 remains DRAFT.** The existing AIOStreams upstream continues on its own latest image. Never modify AIO source, fork, database, or Docker digest; never automatically re-drop an alias. Current Bridge production RC6 is left untouched.

## What the new Bridge-owned gateway actually does

The Bridge has an additional HTTP listener, disabled by default. It implements only the exact GET /Shows/NextUp route. All other Jellyfin routes (playback, streaming, images, /Users/Me, /Items details, WebSockets and /UserItems/Resume) remain on the existing nginx-to-AIO path.

For NextUp only, the listener passes the original Jellyfin auth headers to AIO. It validates the exact authenticated AIO user using AIO GET /Users/Me and requires an explicit one-to-one map to a Trakt Bridge profile. It then uses the **same token** to call AIO GET /Items/{opaqueSeriesId} for show-level ProviderIds.Imdb, compares with the Bridge-owned proven Trakt/IMDb alias graph, and returns one NextUp card only when non-conflicting resume/recency evidence proves a winner. It preserves the original Jellyfin episode IDs, watched history, play states and AIO SQLite. No speculative Trakt reads, direct AIO writes, automatic Drop Show, title matching or opaque ID parsing.

On an unknown ID, unverified user, metadata error, competing active resumes, incomplete graph or upstream failure, it passes the original NextUp data through. The gateway is read-only and cannot fix the underlying AIO 10+8 watched-state row split by itself. It does not normalize users who keep connecting directly to AIO.

## Configuration on the NEXT Bridge build — defaults off

Use these settings only in a separately approved Bridge canary Compose file after checking actual network and nginx routing. They are Bridge settings, NOT AIO settings.

- JELLYFIN_GATEWAY_ENABLED=false (set true only for canary)
- JELLYFIN_GATEWAY_PORT=7101 (inside the Bridge container)
- JELLYFIN_GATEWAY_UPSTREAM_URL: actual AIO Jellyfin API base URL REACHABLE FROM BRIDGE container (do not guess port or prefix)
- JELLYFIN_GATEWAY_AIO_USER_ID: exact authorized AIO /Users/Me Id
- JELLYFIN_GATEWAY_PROFILE_ID: exact connected 24-hex Trakt Bridge profile

The gateway listens on port 7101 inside the Bridge container only when enabled. If the container port is published, bind **only** localhost on host, e.g. 127.0.0.1:17131:7101. Confirm this port is unused. No new public router port forward or change to existing Bridge :7000.

## Native nginx — example ONLY, not yet installed

Once the real nginx server block and Jellyfin path are audited, use an **exact-path** proxy ONLY for GET /Shows/NextUp. The nginx exact location normally matches other HTTP methods too; the gateway answers 405 for non-GET. Existing AIO nginx location remains in place for all other paths, preventing streaming/WS disruption.

Example when current public Jellyfin API is mounted at root:

```nginx
location = /Shows/NextUp {
    proxy_pass http://127.0.0.1:17131;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header X-Emby-Authorization $http_x_emby_authorization;
    proxy_set_header X-Emby-Token $http_x_emby_token;
    proxy_set_header X-MediaBrowser-Token $http_x_mediabrowser_token;
    proxy_pass_request_headers on;
    proxy_buffering off;
    proxy_read_timeout 20s;
}
```

If your live nginx uses /jellyfin or another prefix, do NOT paste this root-path example. Audit the exact existing client URL and adapt request path with an explicit rewrite. Never publicly expose :17131.

## Confirmed HomeDocker topology — operator audit 2026-10-14

The owner ran a **read-only** audit confirming: Bridge RC6 configured by pinned digest and attached ONLY to `homedocker-trakt-bridge_default`; AIOStreams `ghcr.io/viren070/aiostreams:latest` attached ONLY to `media_core_net`; AIO listens on container TCP/3000 exposed as host loopback `127.0.0.1:3240`; Bridge listens on container TCP/7000 exposed as `127.0.0.1:7000`; no host listener on proposed port 17131 or container port 7101; host nginx config test succeeds.

**Implications**:

- `http://127.0.0.1:3240` **inside the Bridge container is incorrect**: it addresses the Bridge container's own loopback, NOT the host or AIO.
- The gateway can connect directly to `http://aiostreams:3000` **only after** the operator confirms `aiostreams` DNS alias is available on `media_core_net` and a **new canary Bridge** is joined to that existing external network.
- Do **not** connect/recreate the existing RC6 production Bridge during this audit, and do **not** disconnect, restart, modify or pin AIOStreams. The gateway canary must preserve the existing Bridge default network and add the existing media network; never replace its current network.
- Adding a shared network expands Bridge's lateral reach to media services. Treat this as an explicitly approved security change. If the user does not approve this additional attachment, return to a sidecar/network design that doesn't alter either production container.
- `JELLYFIN_GATEWAY_UPSTREAM_URL=http://aiostreams:3000` is a **candidate** only, conditional on alias + container reachability, not a previously tested fact.
- The current RC6 persisted `identity-alias:v1:<profile>` evidence may exist while the **new** `identity-graph:v1:<profile>` registry does not. Do not presume the gateway can merge 10+8 rows until the exact owner-scoped legacy evidence is audited and either safely imported with conflict checks or freshly learned from a verified Trakt scrobble. Never silently seed aliases by matching titles or AIO row counts.

### Required read-only second audit

From the draft PR, `scripts/audit-gateway-readiness.py` uses Docker inspection and Bridge SQLite `mode=ro` to produce **only** boolean/aggregate evidence for network/alias and the known Trakt fixture. It does not expose the Bridge profile ID, tokens, `.env` or SQLite payload:

```bash
sudo python3 "$HOME/audit-gateway-readiness.py"
```

See the accompanying tests `test/test_gateway_readiness.py`. Don't attempt the network/Compose/nginx change until the exact read-only result is reviewed.

### Future (NOT DEPLOYED) Compose overlay shape

For an existing Compose project already owning `homedocker-trakt-bridge_default`, a potential **additional** overlay is:

```yaml
services:
  trakt-bridge:
    networks:
      - default
      - media_core_net
    ports:
      - "127.0.0.1:17131:7101"

networks:
  media_core_net:
    external: true
```

This is **not an instruction to apply now**. It requires reviewing the actual root-owned Compose stack (preserving all existing services, labels, volumes and ports), testing `docker compose config`, available port + AIO alias, a qualified own-code canary image, safe Bridge SQLite backup and a verified RC6 rollback. The Gateway feature flag must remain off until canary. If the existing Compose project has nondefault networks, adapt its network list rather than copying this literal example. An opt-in preview should be staged without recreating RC6.

## Read-only HomeDocker discovery BEFORE canary

```bash
docker inspect trakt-bridge aiostreams --format '{{.Name}} {{.Config.Image}} {{.State.Status}}'
sudo nginx -t
sudo ss -lntp | grep -E ':(7000|7101|17131)\b' || true
docker inspect trakt-bridge aiostreams --format '{{.Name}} networks={{range $n,$v := .NetworkSettings.Networks}}{{$n}} {{end}}'
```

Do **not** paste or log root .env contents, full Config.Env from docker inspect, Trakt tokens, or authenticated Jellyfin URLs.

## Acceptance and release boundary

1. Verify real clients use the nginx hostname and NextUp path being intercepted. Any client reaching AIO directly bypasses the gateway.
2. Test multiple accounts: only the explicitly mapped user is normalized; all others retain the exact AIO response.
3. Test real AIO latest Jellyfin series-detail IDs, RC7 10+8 row split, alias playback after undrop, canonical resume and legitimate rewatches. No watch-state changes/deletions.
4. Check AIO streaming, playback, native Trakt scrobble and all other routes remain unchanged via original nginx location.
5. Test error, 429 and metadata timeout. Ensure no auth/token/PII logging and bounded metadata load.
6. Roll back by removing the single nginx exact-path location and disabling the extra Bridge listener; Bridge RC6 pinned digest/SQLite online backup remain available.

**NOT YET DONE:** actual nginx cutover, real hardware/client smoke, multi-user mappings, pagination-safe Resume normalization, AIO upstream upgrade resilience after new alias events, and production release. PR #94 remains DRAFT; Issue #93 remains OPEN. No deployment is authorized by this document.
