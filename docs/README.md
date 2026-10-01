# Documentation map

HomeDocker Trakt Bridge documentation is organized by task rather than by implementation history.

## I want to install or reconnect the bridge

Read **[SETUP.md](SETUP.md)**.

It covers:

- required environment variables
- Docker Compose deployment
- Trakt connection
- the AIOStreams manifest URL
- recommended HomeDocker settings
- first-run verification

## I operate the service

Read **[OPERATIONS.md](OPERATIONS.md)**.

It covers:

- `/health`, `/readiness` and `/status`
- backups
- upgrades and schema migrations
- rollback
- logs and event diagnostics
- release/tag workflow

## I want to understand the topology

Read **[ARCHITECTURE.md](ARCHITECTURE.md)**.

It explains why HomeDocker uses:

```text
Trakt          = canonical watched/resume history
AIOStreams     = Jellyfin-compatible state surface
Trakt Bridge   = state bridge between them
AIOMetadata    = metadata/catalog + secondary-tracker write fan-out
```

## I am configuring AIOMetadata or client coexistence

Read **[INTEGRATIONS.md](INTEGRATIONS.md)**.

It explains:

- why AIOMetadata `Trackers = This server only` is recommended
- why that setting affects tracker readback, not enabled tracking writes
- native Trakt + AIOStreams coexistence
- the role of Jellyfin-compatible clients

## Something is broken

Read **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**.

It includes the main production failure classes already seen in HomeDocker, including duplicate Continue Watching identities, Trakt `422` at 0% progress, historical errors and stale client state.

## I am changing the code

Read **[DEVELOPMENT.md](DEVELOPMENT.md)**.

It contains:

- protocol assumptions
- identity edge cases
- the historical dual-IMDb regression case
- cache/idempotency rules
- CI and release expectations

Release history remains in **[../CHANGELOG.md](../CHANGELOG.md)**.
