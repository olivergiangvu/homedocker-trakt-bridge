# Setup

This guide covers a fresh HomeDocker Trakt Bridge deployment and reconnecting an existing profile.

## 1. Requirements

- Docker Engine + Docker Compose
- a Trakt API application
- HTTPS reverse proxy on the host
- persistent storage for `/app/data`
- AIOStreams with `watch_state` v2 support

Production HomeDocker exposes the bridge through host nginx while the container itself binds to `127.0.0.1:7000`.

## 2. Prepare configuration

```bash
cp .env.example .env
cp compose.example.yml compose.yml
```

Set at least:

```env
PUBLIC_BASE_URL=https://your.example.com
TRAKT_CLIENT_ID=...
TRAKT_CLIENT_SECRET=...
BRIDGE_SECRET_KEY=...
ADMIN_KEY=...
PULL_IDENTITY_MODE=trakt
```

Use long random values for `BRIDGE_SECRET_KEY` and `ADMIN_KEY`.

`BRIDGE_SECRET_KEY` is part of credential derivation and encrypted token storage. Back it up with the database.

## 3. Start the service

```bash
docker compose up -d --build
```

Verify liveness:

```bash
curl -fsS http://127.0.0.1:7000/health
```

Expected shape:

```json
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.4.0"}
```

## 4. Create or open a profile

Open:

```text
https://your.example.com/setup?key=<ADMIN_KEY>
```

Create a profile if needed, then open it and connect Trakt through OAuth.

Once connected, `/readiness` should return HTTP `200` with:

```json
{
  "status": "ready",
  "ready": true,
  "database": "ok",
  "schemaVersion": 1,
  "connectedProfiles": 1
}
```

## 5. Copy the AIOStreams manifest

The profile dashboard highlights the **Manifest URL** near the top of the page.

Use the **Copy** button and paste that URL into AIOStreams.

Treat the manifest URL as a credential. It contains a profile-scoped addon key.

## 6. Recommended HomeDocker authority settings

Bridge:

```env
PULL_IDENTITY_MODE=trakt
```

AIOMetadata Jellyfin user:

```text
Trackers = This server only
```

This keeps Trakt as the only external watched/resume read authority for the AIOStreams state surface.

AIOMetadata can still send playback writes to enabled secondary trackers independently.

See [INTEGRATIONS.md](INTEGRATIONS.md) for the read/write distinction.

## 7. First-run verification

Check:

```bash
curl -fsS http://127.0.0.1:7000/health
curl -fsS http://127.0.0.1:7000/readiness
```

Then verify in the dashboard:

- Trakt connected
- status is Healthy
- active errors are `0`
- authoritative pull has completed
- manifest is visible and copyable

Finally open a Jellyfin-compatible client through AIOStreams and confirm watched/resume state appears as expected.

## Reverse proxy note

Do not expose port `7000` directly to the Internet. Terminate HTTPS at the host reverse proxy and forward only to loopback.

The bridge itself does not manage certificates.
