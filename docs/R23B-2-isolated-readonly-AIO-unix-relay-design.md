# R23B-2 — temporary read-only AIO relay for the existing R23B offline canary

**STATUS: implementation + local unit/HTTP integration lab only. NO HomeDocker installation, no Docker or systemd deployment authorized yet.** The observed HomeDocker R23B canary is healthy on `homedocker_v140_r23b_local` with `com.docker.network.bridge.enable_ip_masquerade=false`, `Internal=false`, and a deliberately OFFLINE `JELLYFIN_GATEWAY_UPSTREAM_URL=http://127.0.0.1:9999/jellyfin`. The B1V authenticated source AIO login, `/Users/Me`, and initial `/Shows/NextUp` succeeded; the canary's `/Shows/NextUp` returned 502 `upstream_unavailable` as expected for its sentinel. This is **not an AIO account failure or a Trakt history failure**.

## Why a private Unix relay instead of changing the existing container

Recreating R23B would require modifying its originally audited immutable environment and perhaps its private network. Attaching it to the production `media_core_net` would expand its reach. Binding AIO 3240 to host 0.0.0.0 or reconfiguring nginx could expose private services unnecessarily.

The proposed **short-lived** read-only test uses two minimal listeners without any changes to R23B's image, environment, mounts or network membership:

```
existing R23B process → GET http://127.0.0.1:9999/jellyfin/Shows/NextUp
        │ within its existing Linux network namespace
        ▼
new temporary Python sidecar (127.0.0.1:9999 ONLY)
        │ Unix-domain-socket client over a read-only bind-mounted private socket directory
        ▼
host-only Unix socket /run/homedocker-r23b2-aio/readonly.sock
        │ fixed GET endpoint and header allowlist
        ▼
host Python relay → GET http://127.0.0.1:3240/jellyfin/Shows/NextUp
        │ same already operational AIO Jellyfin host-local listener
        ▼
existing AIOStreams — without exposing any new host TCP port
```

**Network topology:** only the *sidecar* would use `--network container:homedocker-v140-r23b-canary` so that R23B's hardcoded `127.0.0.1:9999` resolves to the sidecar in the SAME network namespace. The host Unix relay is **not** a Docker sidecar and has no TCP server: it forwards Unix-socket traffic to the fixed host-local AIO endpoint. No container is connected to production media networks or granted a Docker socket. The Unix socket directory would be owned by UID 65534 with mode 0700; socket mode 0600; both host and sidecar processes run without root privileges as UID 65534, with sidecar cap-drop ALL, read-only root filesystem, no-new-privileges and no restart policy. No automated host deployment script is supplied at this checkpoint.

## Explicit narrow API policy

The relay permits **GET only**, with limited non-credential request headers (accept) and explicit Jellyfin auth header allowlist. No header/body/URL logging. Allowed path forms: `/jellyfin/Shows/NextUp`, `/jellyfin/Users/Me`, and `/jellyfin/Items/{safe-id}`; query keys and values are capped. **Authentication POST is not relayed**; B1V's one AIO login POST goes to the existing host-local 3240 path as before. Trakt sync, playback progress, watch-state mutations, Docker APIs, arbitrary SSRF targets, follow-redirects and unknown routes are denied. Default Resume is OFF; it can be explicitly enabled only at BOTH Unix relay hops after independent approval, then the raw JSON response body must remain identical.

A failed or removed host Unix socket causes **502 fail-closed** at the sidecar; stopping/removing only the sidecar returns R23B to its proven offline-sentinel behavior. There is no new history writer, session cache, OAuth provider or media scraper. This prototype does not claim to protect against host root, Docker administrators, users with access to the sidecar's namespace, network namespace restart or an attacker possessing actual AIO tokens.

## Hard pre-deployment checks — not yet performed on HomeDocker

Before writing any deployment commands or proposing an actual temporary host service:
1. Inspect the existing canary's current immutable image/network namespace identity and verify `127.0.0.1:9999` is not already listening inside it, without printing its env/keys.
2. Verify an unused private `/run` socket directory name, correct UID 65534 permissions, sidecar image availability, and Docker `--network container:<name>` support in a disposable kernel/container lab.
3. Confirm GET response semantics for `/Users/Me`, NextUp and bounded `/Items/{safe-id}`, and optional Resume with two login sessions **without any real playback or scrobble requests**. Reject any cross-user projection or modified watch positions.
4. Include bounded temporary process lifetime, exact owned resources, named rollback and verifiable removal: stop sidecar, stop host relay, delete only its own Unix socket directory, verify original R23B intact; avoid broad Docker prune or nft flush.
5. Do not run alongside the root-equivalent Docker API bypass risk without acknowledging this is a strictly scoped acceptance probe, NOT a production Gateway.

## Gates and scope

The existing B1V source-user auth has passed on HomeDocker, and the offline canary URL is confirmed. **Live AIO→R23B HTTP forwarding is NOT YET VERIFIED.** After synthetic CI succeeds, the next step is a read-only namespace/socket capability audit on HomeDocker; only then consider operator-authorized ephemeral relay deployment. B1P intermittent lease renewal, B1S privileged Docker bypass, production-shaped tc/nft ingress, and actual Trakt history/scrobble remain OPEN. **`V140_STABLE_RELEASE_HOLD` must not be changed.**
