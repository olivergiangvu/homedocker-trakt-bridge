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
- An isolated gateway-only canary can connect to AIO's `http://aiostreams:3000/jellyfin` API base after the media-network DNS alias and authenticated Jellyfin route are verified. **Do not attach production RC6 to media_core_net** (the standalone sidecar supersedes an in-place second Bridge).
- Do **not** connect/recreate the existing RC6 production Bridge during this audit, and do **not** disconnect, restart, modify or pin AIOStreams. The gateway canary must preserve the existing Bridge default network and add the existing media network; never replace its current network.
- Adding a shared network expands Bridge's lateral reach to media services. Treat this as an explicitly approved security change. If the user does not approve this additional attachment, return to a sidecar/network design that doesn't alter either production container.
- `JELLYFIN_GATEWAY_UPSTREAM_URL=http://aiostreams:3000/jellyfin` is the correct **candidate** for AIO's official Jellyfin API base, conditional on the running image's public unauthenticated `/jellyfin/System/Info/Public` probe. The former bare `http://aiostreams:3000` URL is the AIO web-server root and causes false `/Shows/NextUp` 404 responses.
- The current RC6 persisted `identity-alias:v1:<profile>` evidence may exist while the **new** `identity-graph:v1:<profile>` registry does not. Do not presume the gateway can merge 10+8 rows until the exact owner-scoped legacy evidence is audited and either safely imported with conflict checks or freshly learned from a verified Trakt scrobble. Never silently seed aliases by matching titles or AIO row counts.

### Required read-only second audit

From the draft PR, `scripts/audit-gateway-readiness.py` uses Docker inspection and Bridge SQLite `mode=ro` to produce **only** boolean/aggregate evidence for network/alias and the known Trakt fixture. It does not expose the Bridge profile ID, tokens, `.env` or SQLite payload:

```bash
sudo python3 "$HOME/audit-gateway-readiness.py"
```

See the accompanying tests `test/test_gateway_readiness.py`. Don't attempt the network/Compose/nginx change until the exact read-only result is reviewed.

### Third gate: verified RC6 legacy import (code implemented, **not run on host**)

The operator's second audit has now PASSed (2026-10-14):

- `media_core_net` is a non-internal Docker bridge network, AIO is attached with Docker DNS alias `aiostreams`; Bridge RC6 is **not attached** to it.
- Host AIO `127.0.0.1:3240` → container 3000; hostname `aiostreams:3000` remains an **untested reachability candidate** for a future separate gateway canary.
- Bridge media_cache identity records scanned = 1: legacy show `263102` contains both the known canonical `tt44051354` and AIO alias `tt44094505`; the new verified identity graph is absent.
- Zero Docker, AIO or SQLite mutations.

**This is enough to build a safe migration, but not enough to apply it yet.** Before any canary or database copy, check the legacy entry's `updatedAt`, its `expires_at`, and that the cache key belongs to the exact still-connected Trakt profile. The extended audit `scripts/audit-gateway-readiness.py` reports three new *boolean-only* values: `legacy_alias_timestamp_valid`, `legacy_cache_unexpired`, `legacy_owner_connected`. Never print the profile ID, Trakt credentials or raw cache JSON.

Migration is implemented entirely inside OUR Bridge code:

- `src/identity-legacy-import.mjs` copies the verified **exact-profile** RC6 `identity-alias:v1` record to a new `identity-graph:v1` record. It validates all legacy rows, safe IDs and timestamp, verifies the connected Trakt profile, rejects ambiguous cross-show ID claims atomically, and **never overwrites an existing graph**.
- `src/db.mjs::cachePeek` does not delete expired/corrupt rows, making the default preview **actually read-only**. `cacheInsertIfAbsent` atomically inserts the entire new graph only if missing; `identity-alias:v1` remains intact.
- Migration config is **OFF by default**. `JELLYFIN_GATEWAY_LEGACY_IMPORT=preview` logs only import status/counts with no database mutation. `apply` additionally requires `JELLYFIN_GATEWAY_ENABLED=true` and can be used **only against the approved canary's backed-up/copy-of-production Bridge SQLite**, not the live RC6 database. Tests exercise fake DB and real in-memory SQLite atomicity.
- Migration never touches AIOStreams data or API and never triggers scrobbles, Trakt GET/POST or rewatch writes.

A successful gateway canary requires one actual known verified alias in `identity-graph:v1` *after import*; a running HTTP listener without such a graph does **not** prove NextUp normalization.

## Preferred architecture after successful ownership audit: *isolated gateway sidecar*

**This supersedes the earlier suggestion to add `media_core_net` to the running production Bridge.** Do not change RC6 networks, Compose, volumes or its pinned digest; no need to restart or recreate it. The operator's read-only audit now confirms:

- `legacy_alias_timestamp_valid=true`
- `legacy_cache_unexpired=true`
- `legacy_owner_connected=true`
- `legacy_canonical_matches=true`, `legacy_alias_matches=true`
- AIO's `aiostreams` DNS alias exists on `media_core_net`.
- `verified_graph_present=false` (expected before canary).

The canary now has **no dependency on a Bridge database copy**:

1. `scripts/export-canary-identity-proof.py` reads the current pinned RC6 `bridge.db` in SQLite `mode=ro`, verifies **exactly one profile-scoped legacy cache record** and the already-confirmed show mapping `263102: tt44094505 → tt44051354`, checks every other show in the SAME record for claims on either protected IMDb ID (cross-show collision aborts), validates expiry and timestamp, then writes a **new 0600 file** `identity-proof.json` in a caller-prepared 0700 directory. It includes ONLY the exact IMDb pair, a profile-scoped reference and a conflict-free revision. **No Trakt OAuth token, encrypted token, watched/resume history, SQLite database or `.env` goes into the sidecar.** Never print the file contents in chat.
2. `src/gateway-standalone.mjs` loads that sanitized proof via `GATEWAY_PROOF_FILE` and supplies a **read-only in-memory** `cacheGet` facade to `src/jellyfin-gateway.mjs`. No `BridgeDb`, `ManagedTraktClient`, `src/index.mjs`, scrobble, reconciler, DB migration or Trakt HTTP calls are started. `src/gateway-canary-entry.mjs` is the standalone Node entrypoint.
3. Run only this **separately named** canary container on existing `media_core_net`, publishing the NextUp listener solely to host `127.0.0.1:17131`. Do **not** attach existing RC6 to media network, mount production Bridge DB/volume, copy secrets, or modify AIO `latest`. Use the **reviewed canary image built at an exact commit** after CI, never floating source.
4. The canary is isolated from all public Jellyfin requests until an explicitly reviewed native nginx exact-path location change. Testing its `/health` does not require passing any real Jellyfin token or modifying live clients.

### RC6 multi-show cache correction (2026-10-14)

The first proof-export attempt returned `STOP: Unexpected legacy shows; refuse unsafe export`. The original exporter incorrectly assumed the ONE `media_cache` row confirmed by `audit-gateway-readiness.py` held exactly ONE logical show. In RC6, the existing single `identity-alias:v1:<profile>` JSON can legitimately contain **many learned show IDs**. That exception occurred **before** any proof-file creation; Bridge/AIO SQLite were read-only.

The corrected exporter selects only the verified Trakt show **263102**, checks its canonical+alias IDs/expiry/timestamp/connected profile, and examines all other bounded show rows to reject **any conflicting ownership of either `tt44051354` or `tt44094505`**. Unrelated show rows are not exported or printed. A malformed unrelated row or a collision aborts before creating any proof. Regression tests cover multiple valid shows, unknown canonical IDs on unrelated shows, malformed rows, and conflicting IMDb ownership. Continue to refuse pre-existing proof files; never delete a file merely to bypass the guard.

### Actual on-host canary checkpoint — running, still NOT normalized (2026-10-14)

The operator successfully exported private 0600 proof (owner UID/GID 1000:1000), built the immutable-source canary image at commit `96f229afcfcd...`, started `homedocker-nextup-canary` on `media_core_net` with host loopback `127.0.0.1:17131`, and got `/health` 200. Production RC6 and AIO image IDs remained **unchanged**. The unauthenticated Jellyfin NextUp comparison incorrectly returned `404/404` because the canary was given upstream base `http://aiostreams:3000` (AIO *web root*) rather than the official AIO Jellyfin API base `http://aiostreams:3000/jellyfin`. **Matched 404s were only evidence of HTTP reachability, not NextUp API compatibility.**

Official AIO app mounts Jellyfin API at `/jellyfin`, optionally beneath picker/user addresses `/jellyfin/u/:alias`, `/jellyfin/p/:code` or `/jellyfin/:uuid/:encryptedPassword`. The gateway itself intentionally listens for `GET /Shows/NextUp` on its **local** root. Do not confuse inbound gateway URL with the **prefixed upstream** AIO Jellyfin URL. A synthetic authenticated regression covers the `/jellyfin` base prefix.

**Correct next safety gate:** without credentials, probe only HTTP status for `http://127.0.0.1:3240/jellyfin/System/Info/Public` (expect 200) and `http://127.0.0.1:3240/jellyfin/Shows/NextUp` (authorization-specific, may return 401/403 or another error). If public info probe is not 200, stop and inspect AIO configuration; do not recreate the sidecar blindly.

**Then** recreate **only** `homedocker-nextup-canary` with its verified current local image and corrected upstream base `http://aiostreams:3000/jellyfin`. Do not change nginx, RC6, AIO, proof file or image. Keep the intentionally unmatched `JELLYFIN_GATEWAY_AIO_USER_ID=CANARY_PREVIEW_NO_MATCH_USER`; no user-specific normalization is authorized yet. Repeat unauthenticated `GET /Shows/NextUp` status parity against `/jellyfin/Shows/NextUp`, not AIO web root.

Public AIO docs: https://docs.aiostreams.viren070.me/apps/jellyfin/ and https://docs.aiostreams.viren070.me/reference/jellyfin-extensions/

### Real Jellyfin API connectivity PASS; next: authenticated preview, still NO nginx change

Owner's actual on-host command (2026-10-14) confirmed:

| Request | Status |
| --- | --- |
| AIO `http://127.0.0.1:3240/jellyfin/System/Info/Public` | 200 |
| AIO `/jellyfin/Shows/NextUp` without credentials | 401 |
| Loopback canary `/Shows/NextUp` without credentials | 401 |
| Canary `/health` | 200 |

`BRIDGE_UNCHANGED=true`, `AIOSTREAMS_UNCHANGED=true`, `JELLYFIN_API_PREFIX=VERIFIED`, `GATEWAY_CANARY=PASS`. The only recreated container was **the separate** `homedocker-nextup-canary`, still with deliberately unmatched `JELLYFIN_GATEWAY_AIO_USER_ID=CANARY_PREVIEW_NO_MATCH_USER`. This proves real Jellyfin API routing and equivalent unauthenticated authorization behavior, **not** same-user identity normalization.

New **read-only and privacy-preserving** audit `scripts/audit-jellyfin-auth-preview.py` exercises the real authenticated AIO user, the unmodified canary response and a bounded (default 8) sample of SeriesId details. It never prints/saves the session token, username, Jellyfin user ID, series or episode identifiers, titles, watch timestamps or raw API responses. It only contacts loopback `127.0.0.1:3240` and `127.0.0.1:17131`; no Trakt requests; no AIO configuration/DB writes; no user watched-history mutations.

Operator can choose:

```bash
# AIO Jellyfin **session** token from an existing client, entered hidden:
python3 "$HOME/audit-jellyfin-auth-preview.py" --auth token

# Or use the *existing official Jellyfin AuthenticateByName* login API,
# prompting for both username/alias and password invisibly:
python3 "$HOME/audit-jellyfin-auth-preview.py" --auth login
```

The `login` option creates a **new Jellyfin login session**, which may be recorded in AIO's own authentication/session state; it is not a strict zero-write audit of all AIO state, although it does not change playback/watch history, AIO config, the Docker image or SQLite schema. Prefer an existing session token where practical. Jellyfin **API keys are not equivalent to user session tokens**: AIO `/Users/Me` explicitly rejects keys without user identity.

Results are aggregate: /Users/Me accepted, direct vs canary NextUp HTTP and bounded item IDs/positions parity, number of SeriesId metadata lookups that successfully resolve `Type=Series` plus whether either protected IMDb spelling appears **in the small sample**. Absence of either spelling in this sample is NOT evidence that the historical watched-state rows are gone, especially with prior Drop preference applied.

The audit's optional `--save-user-env /opt/homedocker/gateway-canary/jellyfin-user.env` creates a new root-private **0600** file with only `JELLYFIN_GATEWAY_AIO_USER_ID`, not a token; it refuses overwrite. Do **not** request this or change the running preview canary's configured user until exact AIO scope and requested Bridge/Trakt profile ownership are reviewed. Keep all session tokens strictly local; never paste them into ChatGPT, GitHub, scripts or terminal logs.

### Authenticated audit PASS on actual HomeDocker — 2026-10-14

The operator ran the hidden-input official Jellyfin login audit against the *correct* AIO `/jellyfin` base and isolated canary. Results:

- `api_user_session_verified=true`
- AIO authenticated NextUp `200`, gateway authenticated NextUp `200`
- `direct_items=50`, `gateway_items=50`, `preview_opaque_item_signature_equal=true`
- 8/8 SeriesId detail GETs returned `200`, `Type=Series` and a valid IMDb provider ID.
- Neither known canonical `tt44051354` nor alias `tt44094505` appeared in **the first 8** series; this says nothing about the other 42 entries or local historical AIO rows.
- `new_login_session_created=true`, `session_token_saved=false`, `normalization_applied=false`. No watched history, AIO config, nginx or Docker changes.

**Next gate before enabling actual user filtering:** sample up to the *entire existing 50-card NextUp response* through the same official AIO /Items endpoint, limited to four concurrent metadata requests, while keeping results aggregate-only. `scripts/audit-jellyfin-auth-preview.py` now allows `--max-series-details 50` (the default remains 8). The optional `--save-user-env` writes only one private 0600 local User ID, not a token. It is operator-controlled, never printed, and must not be posted to GitHub or chat. If the target pair is not in the 50-card shelf, treat the isolated filter as NOT LIVE-VERIFIED, not as a normalization success.

**Traffic performance safeguard** in `src/jellyfin-gateway.mjs` now caches only the validated, single-user `SeriesId → IMDb` metadata mapping for five minutes with a maximum of 128 entries after /Users/Me was checked, never caching passwords, auth tokens or watch rows. This requires building a **new exact source-commit canary image** before enabling any real user. The previously built `canary-96f229afcfcd` image is still in preview-only mode and does not include this performance patch. No AIO source change.

### Read-only proof export prerequisites (do NOT export until operator acceptance)

Example commands after reviewing the actual host ownership and mounted paths:

```bash
# Preparation for separate nonsecret proof only. Creates NO Docker containers.
sudo install -d -m 0700 -o root -g root /opt/homedocker/gateway-canary
sudo python3 "$HOME/export-canary-identity-proof.py" \
  --output /opt/homedocker/gateway-canary/identity-proof.json
sudo stat -c '%a %u %g %n' /opt/homedocker/gateway-canary/identity-proof.json
```

Exporter refuses an existing proof rather than overwrite it, and sets file mode 0600 / owner UID 1000 for the nonroot Node image. Keep canary directory private. A root-only directory is safe when its **file** is mounted directly as a read-only bind mount via Docker; never mount that directory or an OAuth/Trakt data volume into the canary.

### Standalone canary container execution design (NOT YET AUTHORIZED)

Use the reviewed `v1.3.1` feature branch source's Dockerfile as a **separately tagged local image** (e.g. `localhost/homedocker-nextup-canary:<reviewed-sha>`) while passing `node src/gateway-canary-entry.mjs` as the command. The container may use AIO's `media_core_net` and loopback-only host port, but does not need `homedocker-trakt-bridge_default` at all. It requires:

- `GATEWAY_PROOF_FILE=/run/identity-proof.json` (single-file readonly bind mount);
- `JELLYFIN_GATEWAY_UPSTREAM_URL=http://aiostreams:3000/jellyfin` **only after verifying `/jellyfin/System/Info/Public` is accessible**;
- `JELLYFIN_GATEWAY_AIO_USER_ID`: operator-verified ID from AIO existing `/Users/Me` (never paste Jellyfin auth tokens);
- `JELLYFIN_GATEWAY_PORT=7101` (container), loopback-only host mapping `127.0.0.1:17131:7101`.

The container must have `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, restart policy `no`, no `docker.sock`, no writable bind mounts, no Trakt secrets and no public router forwarding. It is a **read-only view adapter**, not a second Trakt Bridge.

**Rollback** before nginx: stop/remove the standalone canary container; nothing else changed. After nginx canary: remove the exact-path location and verify `nginx -t`, then stop the standalone canary. No watch-state rollback is necessary because this path never writes it.

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

This **legacy in-place Compose overlay is NOT the preferred canary plan**: the isolated gateway sidecar above avoids changing the production RC6 Compose/network and avoids copying its full SQLite/OAuth secrets. Do not apply this overlay to the production Bridge.

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
