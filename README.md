# HomeDocker Trakt Bridge

A self-hosted bridge between **AIOStreams `watch_state` v2** and **Trakt**.

It lets Jellyfin-compatible clients use AIOStreams as their playback/state surface while keeping Trakt as the canonical watched/resume history source.

**Current development release: v0.4.0.**

## What this project does

```text
Jellyfin-compatible clients
(Strand / Odin / Remux / Trellis / others)
               |
               v
          AIOStreams
               |
               v
      HomeDocker Trakt Bridge
               |
               v
             Trakt
```

The bridge supports:

- playback scrobbling (`start`, `pause`, `stop`)
- watched / unwatched history
- watched-state pull back into AIOStreams
- movie/show watchlist sync
- bulk season/show watched updates
- retry-safe event processing
- restart-safe pull cache
- Trakt rate-limit / transient-error fallback
- `PULL_IDENTITY_MODE=trakt` for native-Trakt + AIOStreams coexistence
- operational health, readiness and status endpoints
- a small operator dashboard with recent-event diagnostics

## HomeDocker authority model

For the HomeDocker deployment, use one history read authority:

```text
Trakt                = canonical watched/resume history
Trakt Bridge         = Trakt <-> AIOStreams state bridge
AIOStreams           = Jellyfin-compatible state/playback surface
AIOMetadata          = metadata/catalog + secondary-tracker write fan-out
AIOMetadata Trackers = This server only
```

`AIOMetadata -> Trackers = This server only` prevents secondary tracker history from being read back as a second competing Jellyfin history source. Enabled Watch Tracking writes can still fan playback out to secondary trackers.

## Quick start

1. Copy the example environment and compose files.
2. Set the required Trakt credentials and bridge secrets.
3. Start the service with Docker Compose.
4. Open the setup page and connect the Trakt profile.
5. Copy the highlighted **AIOStreams manifest URL** from the dashboard into AIOStreams.

```bash
cp .env.example .env
cp compose.example.yml compose.yml

docker compose up -d --build
```

Recommended HomeDocker setting:

```env
PULL_IDENTITY_MODE=trakt
```

The container listens on port `7000`; the production compose binds it to `127.0.0.1` and expects the host reverse proxy to provide HTTPS.

## Operator endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | process liveness |
| `GET /readiness` | DB/schema/profile readiness |
| `GET /status?key=<ADMIN_KEY>` | authenticated operational diagnostics |
| `/setup?key=<ADMIN_KEY>` | profile administration |

The dashboard keeps operational information visible and leaves low-level diagnostics in the status API and developer documentation.

## Documentation

Start here: **[`docs/README.md`](docs/README.md)**.

| Guide | Use it for |
| --- | --- |
| [`docs/SETUP.md`](docs/SETUP.md) | install, configure and connect AIOStreams |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | health, backup, upgrade, rollback and release operations |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | authority model, data flow and runtime boundaries |
| [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) | AIOMetadata and client coexistence rules |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | common production failure patterns |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | protocol contracts, identity edge cases and contributor notes |
| [`CHANGELOG.md`](CHANGELOG.md) | release history |

## Security notes

- Treat `ADMIN_KEY`, `BRIDGE_SECRET_KEY`, Trakt OAuth material and the generated manifest URL as credentials.
- Do not expose container port `7000` directly to the Internet.
- Back up `bridge.db`, `.env`, compose configuration and reverse-proxy configuration before upgrades that change the DB schema.

## License / scope

This repository is maintained for the HomeDocker self-hosted environment and is intentionally optimized for that topology first.
