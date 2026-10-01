# HomeDocker Trakt Bridge

A self-hosted bridge between **AIOStreams `watch_state` v2** and **Trakt**.

It lets Jellyfin-compatible clients use AIOStreams as their playback/state surface while keeping Trakt as the canonical watched/resume history source.

**Current release-candidate line: v0.9.0.**

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
- retry-safe and restart-safe event processing
- restart-safe pull cache with bounded stale fallback
- Trakt rate-limit / transient-error recovery
- fail-closed authoritative pulls when upstream state is incomplete
- automatic OAuth reconnect state when Trakt rejects an invalid refresh grant
- `PULL_IDENTITY_MODE=trakt` for native-Trakt + AIOStreams coexistence
- operational health, readiness and authenticated status endpoints
- a compact operator dashboard

## HomeDocker authority model

Use one history read authority:

```text
Trakt                = canonical watched/resume history
Trakt Bridge         = Trakt <-> AIOStreams state bridge
AIOStreams           = Jellyfin-compatible state/playback surface
AIOMetadata          = metadata/catalog + secondary-tracker write fan-out
AIOMetadata Trackers = This server only
```

`AIOMetadata -> Trackers = This server only` prevents secondary tracker history from being read back as a second competing Jellyfin history source. Enabled Watch Tracking writes can still fan playback out to secondary trackers.

## Production quick start

Production deploys use the published GHCR image. Source builds are reserved for development.

```bash
cp .env.example .env
cp compose.example.yml compose.yml

# Required when the GHCR package is private.
docker login ghcr.io

docker compose pull
docker compose up -d
```

Set at least the Trakt credentials, bridge secrets and public HTTPS URL in `.env`.

Recommended HomeDocker setting:

```env
PULL_IDENTITY_MODE=trakt
```

For exact release reproducibility, `TRAKT_BRIDGE_IMAGE` can be pinned to an immutable GHCR digest instead of a semver tag.

The container listens on port `7000`; the production compose binds it to `127.0.0.1` and expects the host reverse proxy to provide HTTPS.

## Development build

To build the current source tree locally:

```bash
cp .env.example .env
docker compose -f compose.example.yml -f compose.dev.yml up -d --build
```

The development override changes the service to a local source build without changing the production Compose contract.

## Operator endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | process liveness |
| `GET /readiness` | DB/schema/profile readiness |
| `GET /status?key=<ADMIN_KEY>` | authenticated operational diagnostics |
| `/setup?key=<ADMIN_KEY>` | profile administration |

When Trakt rejects a refresh token with OAuth `invalid_grant`, the bridge clears unusable local credentials, reports `reconnect_required` through `/status`, and returns `setup_required` from `/readiness` until the profile is reconnected.

## Release artifact policy

The pre-1.0 RC line treats the published container as the release artifact:

- release workflow builds and pushes GHCR
- stable tags also publish `latest`
- the exact pushed image digest is pulled and smoke-tested before the release job completes
- release images include SBOM and provenance metadata
- the Node base image is digest-pinned and monitored by Dependabot

See [`docs/releases/v0.9.0.md`](docs/releases/v0.9.0.md) for the final v1.0 gate.

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
| [`SECURITY.md`](SECURITY.md) | credential handling and security reporting |
| [`CHANGELOG.md`](CHANGELOG.md) | release history |

## Security notes

- Treat `ADMIN_KEY`, `BRIDGE_SECRET_KEY`, Trakt OAuth material and the generated manifest URL as credentials.
- Do not expose container port `7000` directly to the Internet.
- Back up `bridge.db`, `.env`, compose configuration and reverse-proxy configuration before upgrades that change the DB schema.
- Prefer an immutable GHCR digest for long-lived production deployments.

## License / scope

This repository is maintained for the HomeDocker self-hosted environment and is intentionally optimized for that topology first.
