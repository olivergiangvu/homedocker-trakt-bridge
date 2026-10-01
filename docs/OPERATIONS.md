# Operations

This guide is for running HomeDocker Trakt Bridge after setup.

## Runtime boundaries

Production assumptions:

```text
host nginx terminates TLS
container port 7000 binds to 127.0.0.1 only
/app/data is persistent
bridge.db is backed up
BRIDGE_SECRET_KEY is backed up
ADMIN_KEY is treated as a credential
PULL_IDENTITY_MODE=trakt
```

## Health endpoints

### Liveness

```text
GET /health
```

Use this to answer: **is the process alive?**

It intentionally does not depend on live Trakt network access.

### Readiness

```text
GET /readiness
```

Readiness checks:

- SQLite query succeeds
- DB schema is supported by the running binary
- at least one profile has Trakt tokens

A configured production instance should return HTTP `200` with `ready=true`.

### Authenticated status

```text
GET /status?key=<ADMIN_KEY>
```

This is the machine-readable operator diagnostic surface.

It exposes operational state but not OAuth tokens, bridge secrets or addon credentials.

The web dashboard intentionally shows less information than `/status`.

## Dashboard interpretation

The operator dashboard prioritizes:

- Trakt connection status
- manifest URL
- active errors in the recent operational window
- last authoritative sync counts
- latest poll source
- recent event summaries

Historical errors are retained for diagnostics but should not by themselves mark the service unhealthy.

## Backup

Back up these together:

```text
/app/data/bridge.db
.env
compose.yml
host nginx configuration for the bridge
```

For SQLite, use an online SQLite backup rather than copying an actively written WAL database blindly.

The database contains:

- profiles
- encrypted OAuth tokens
- idempotency records
- pull cache
- learned identity evidence
- event history

`BRIDGE_SECRET_KEY` is required to decrypt stored OAuth tokens after restore.

## Upgrade procedure

Before an upgrade:

1. verify the current service is healthy
2. back up DB + config
3. sync the new source
4. build the new image while the old container is still running
5. recreate the service
6. verify `/health`
7. verify `/readiness`
8. verify DB schema and profile count
9. verify the dashboard

Do not purge AIOStreams or Bridge cache unless the release notes explicitly require it.

### v0.3.7 -> v0.4.0

The first v0.4.0 startup migrates:

```text
PRAGMA user_version 0 -> 1
```

No watch-state representation change is introduced by this migration.

`PULL_IDENTITY_MODE=trakt` remains unchanged.

## Rollback

If a release changes DB schema, restore the matching pre-upgrade database when rolling back to an older binary.

For v0.4.0 -> v0.3.7 rollback:

1. stop v0.4.0
2. restore the pre-v0.4.0 `bridge.db`
3. restore matching `.env` / compose if needed
4. start the v0.3.7 image
5. verify health and Trakt connection

Do not run an older binary against a DB schema newer than it understands.

## Event history

The dashboard displays compact summaries. Raw event detail is collapsed behind **Raw**.

Useful statuses:

```text
ok         completed successfully
cached     pull served from cache
stale      bounded stale fallback was used
ignored    intentionally acknowledged without upstream write
recovered  a previous failed attempt later succeeded
retrying   recent unresolved retry chain
error      raw historical error row
```

The DB retains historical failures even after the underlying bug is fixed. Use the dashboard's active-error window for current health.

## Logs

Container logs are appropriate for:

- startup and migration information
- uncaught request/upstream failures
- runtime version and schema confirmation

Example:

```bash
docker logs --tail 150 trakt-bridge
```

## Release process

Before creating a release tag:

1. CI passes syntax checks and unit tests
2. CI builds the production container
3. CI smoke-tests `/health` and `/readiness`
4. production deployment passes a short burn-in
5. dashboard and manifest UX are verified
6. release notes / changelog are current

A `v*` tag triggers the release workflow, which builds the amd64 GHCR image and creates a GitHub Release.
