# Setup

This guide covers a fresh production deployment.

## Requirements

- Docker Engine + Docker Compose
- a Trakt API application
- an HTTPS reverse proxy
- AIOStreams with `watch_state` v2 support

The container listens on port `7000`; the provided Compose file binds it to `127.0.0.1` so HTTPS can be terminated by the host reverse proxy.

## 1. Prepare the files

```bash
git clone https://github.com/olivergiangvu/homedocker-trakt-bridge.git
cd homedocker-trakt-bridge

cp .env.example .env
cp compose.example.yml compose.yml
```

## 2. Configure the bridge

Edit `.env` and set at least:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:latest
PUBLIC_BASE_URL=https://trakt.example.com
TRAKT_CLIENT_ID=...
TRAKT_CLIENT_SECRET=...
BRIDGE_SECRET_KEY=...
ADMIN_KEY=...
PULL_IDENTITY_MODE=trakt
```

Generate strong local secrets with:

```bash
openssl rand -hex 32
```

Keep `BRIDGE_SECRET_KEY` with your database backup; it is required to decrypt stored Trakt OAuth tokens after a restore.

The GHCR package is public, so a normal install does not require `docker login ghcr.io`.

For long-lived production installs, pin `TRAKT_BRIDGE_IMAGE` to a release tag or immutable digest instead of `latest`. See [Configuration](CONFIGURATION.md).

## 3. Start the container

```bash
docker compose pull
docker compose up -d
```

Verify the process:

```bash
curl -fsS http://127.0.0.1:7000/health
```

## 4. Configure HTTPS

Point your reverse proxy at:

```text
http://127.0.0.1:7000
```

`PUBLIC_BASE_URL` must match the externally reachable HTTPS URL exactly enough for Trakt OAuth callbacks and AIOStreams access.

Do not expose port `7000` directly to the Internet.

## 5. Connect Trakt

Open:

```text
https://your-domain/setup?key=<ADMIN_KEY>
```

Create a profile if needed and connect Trakt through OAuth.

After connection, verify:

```bash
curl -fsS http://127.0.0.1:7000/readiness
```

A configured instance should return HTTP `200` with `ready=true`.

## 6. Add the bridge to AIOStreams

Open the profile dashboard. The **AIOStreams Connection** card shows a profile-specific **Manifest URL**.

1. Click **Copy**.
2. Add the URL to AIOStreams.
3. Allow the first authoritative sync to complete.

Treat the manifest URL as a credential.

## 7. Recommended authority model

For clients that may also use native Trakt:

```env
PULL_IDENTITY_MODE=trakt
```

If AIOMetadata is connected to the same Jellyfin-compatible ecosystem, set its Jellyfin user to:

```text
Trackers = This server only
```

This keeps Trakt as the single external watched/resume read authority while still allowing enabled secondary-tracker writes.

## 8. Verify the client experience

Check the dashboard for:

- Trakt connected
- Healthy status
- a completed authoritative sync
- no current active errors

Then open your Jellyfin-compatible client through AIOStreams and confirm that watched/resume state appears correctly.

For production, also record the image tag or exact digest you deployed so rollback remains deterministic.

Next: [Configuration](CONFIGURATION.md) · [Operations](OPERATIONS.md) · [Troubleshooting](TROUBLESHOOTING.md)
