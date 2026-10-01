# Operations

This guide covers the routine tasks needed to run HomeDocker Trakt Bridge after setup.

## Health checks

Process health:

```bash
curl -fsS http://127.0.0.1:7000/health
```

Application readiness:

```bash
curl -fsS http://127.0.0.1:7000/readiness
```

A configured instance should report `ready=true` and at least one connected profile.

The web dashboard is the preferred human-readable view for connection status, sync health, recent events and current errors.

## Dashboard states

Common event states:

```text
ok         completed successfully
cached     served from a valid local cache
stale      bounded stale fallback was used
ignored    intentionally acknowledged without an upstream write
recovered  a previous failed attempt later succeeded
retrying   a recent retryable failure is still unresolved
error      an error event retained for diagnostics
```

Historical errors can remain in the database after the underlying problem is gone. Use the dashboard's recent active-error window to judge current health.

## Backup

Back up these items together:

```text
/app/data/bridge.db
.env
compose.yml
reverse-proxy configuration
```

`BRIDGE_SECRET_KEY` is required to decrypt stored Trakt OAuth tokens after restore, so losing `.env` can make an otherwise valid database backup unusable.

For a live SQLite database, prefer SQLite's online backup mechanism instead of blindly copying a WAL-active database.

## Update

If you use `latest`:

```bash
docker compose pull
docker compose up -d
```

For predictable production updates, pin `TRAKT_BRIDGE_IMAGE` to a release tag or immutable digest before pulling.

Recommended update flow:

1. Confirm `/health` and `/readiness` are healthy.
2. Back up the database and configuration.
3. Change the image tag/digest.
4. Pull the new image while the old container is still running.
5. Recreate the service.
6. Re-check `/health`, `/readiness`, dashboard connection state and sync counts.
7. Confirm the client still shows the expected watched/resume state.

Do not purge Bridge or AIOStreams state unless the release notes explicitly require it.

## Rollback

Set `TRAKT_BRIDGE_IMAGE` back to the previous known-good tag or digest, then:

```bash
docker compose pull
docker compose up -d
```

If a future release changes the database schema, restore the matching pre-upgrade database before running an older binary.

## Logs

Recent container logs:

```bash
docker logs --tail 150 trakt-bridge
```

Follow logs live:

```bash
docker logs -f trakt-bridge
```

Use logs mainly for startup, migration and unexpected upstream/runtime failures. The dashboard is easier for normal event inspection.

## Trakt rate limits

The bridge protects Trakt traffic with retry handling, cooldowns and stale-state fallback. If Trakt returns `429`, the dashboard records the affected endpoint and retry delay.

Avoid repeatedly forcing sync while a rate-limit cooldown is active. The bridge will recover automatically when the allowed window reopens.

## OAuth reconnect

If Trakt rejects the stored refresh token, the bridge moves the profile to `reconnect_required` instead of pretending it is connected.

Open the setup page and reconnect Trakt:

```text
https://your-domain/setup?key=<ADMIN_KEY>
```

## Useful files

- [Configuration](CONFIGURATION.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Integrations](INTEGRATIONS.md)
- [Architecture](ARCHITECTURE.md) — advanced
- [Development](DEVELOPMENT.md) — contributors/maintainers
