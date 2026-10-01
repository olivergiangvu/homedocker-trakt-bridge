# Contributing

Thanks for helping improve HomeDocker Trakt Bridge.

## Before opening a change

For bugs, open an issue with:

- Bridge version
- AIOStreams version
- relevant client name
- Docker/host platform
- steps to reproduce
- expected vs actual behavior
- redacted logs or dashboard event details

Do not post `ADMIN_KEY`, `BRIDGE_SECRET_KEY`, Trakt OAuth tokens, manifest URLs, or other credentials.

For feature requests, describe the user problem first. Changes that preserve Trakt as the canonical history authority and AIOStreams as the Jellyfin-compatible state surface are the easiest to evaluate.

## Development

Requirements:

- Node.js 24+
- Docker Engine + Docker Compose

Run checks locally:

```bash
npm run check
npm test
```

Build the development container:

```bash
cp .env.example .env
docker compose -f compose.example.yml -f compose.dev.yml up -d --build
```

Never commit `.env`, live databases, OAuth material, manifest URLs, or other local credentials.

## Pull requests

Keep PRs focused and include:

- what problem is being solved
- behavior changes, if any
- tests added or updated
- migration/compatibility impact
- operational impact for existing deployments

Runtime changes should preserve or explicitly document:

- DB schema compatibility
- restart/idempotency behavior
- Trakt rate-limit handling
- watch-state identity behavior
- `/health` and `/readiness`

CI must pass before merge.

## Security

Do not open a public issue for a vulnerability that could expose credentials or compromise a deployment. Follow [SECURITY.md](SECURITY.md) instead.
