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
production runs a published GHCR image
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
- at least one profile has usable Trakt tokens

A configured production instance should return HTTP `200` with `ready=true`.

If Trakt rejects a refresh token with OAuth `invalid_grant`, the bridge clears the unusable local credentials. Readiness then returns `setup_required` until the profile is reconnected.

### Authenticated status

```text
GET /status?key=<ADMIN_KEY>
```

This is the machine-readable operator diagnostic surface.

It exposes operational state but not OAuth tokens, bridge secrets or addon credentials.

Profile connection state is reported as:

```text
profile.connectionState = connected | disconnected | reconnect_required
profile.reconnectRequired = true | false
```

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

The database contains profiles, encrypted OAuth tokens, idempotency records, pull cache, learned identity evidence and event history.

`BRIDGE_SECRET_KEY` is required to decrypt stored OAuth tokens after restore.

## Release-image deployment

From v0.9.0 onward, production deployment should use the published GHCR artifact rather than rebuilding source on the server.

The normal flow is:

```bash
docker compose pull
docker compose up -d
```

The image is selected by:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge:0.9.0
```

For immutable deployment, replace the tag with the exact release digest:

```env
TRAKT_BRIDGE_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge@sha256:<digest>
```

After a pull, record the resolved image ID/digest before cutover:

```bash
docker image inspect "$TRAKT_BRIDGE_IMAGE" \
  --format '{{json .RepoDigests}}'
```

## Upgrade procedure

Before an upgrade:

1. verify the current service is healthy
2. back up DB + config
3. update `TRAKT_BRIDGE_IMAGE` to the target release tag or digest
4. pull the target release image while the old container is still running
5. inspect the pulled digest
6. recreate the service
7. verify `/health`
8. verify `/readiness`
9. verify DB schema, profile count and connection state
10. verify authoritative sync counts and dashboard
11. restart once during canary acceptance

Do not purge AIOStreams or Bridge cache unless the release notes explicitly require it.

### v0.5.0 -> v0.9.0

v0.9.0 keeps:

```text
DB schema              1
pull representation    watch-state-v0.3.6
pull cache namespace   pull-state:v5:*
PULL_IDENTITY_MODE     trakt (recommended)
```

No DB migration and no pull-cache purge is required.

The significant operational change is deployment parity: production consumes the same GHCR artifact that the release workflow publishes and smoke-tests.

### v0.4.0 -> v0.5.0

v0.5.0 kept schema 1 and added explicit `reconnect_required` handling for rejected OAuth refresh grants. No migration or cache purge was required.

### v0.3.7 -> v0.4.0

The first v0.4.0 startup migrated:

```text
PRAGMA user_version 0 -> 1
```

No watch-state representation change was introduced by that migration.

## v0.9.0 RC checklist

Before tagging v0.9.0, use the checklist in [`releases/v0.9.0.md`](releases/v0.9.0.md).

The important addition is a two-stage artifact acceptance:

1. GitHub Release workflow must pull and run the exact pushed image digest successfully.
2. HomeDocker must then pull and run the published GHCR artifact without a local build.

Only after both stages pass should the RC begin its v1.0 burn-in.

## Rollback

If a release changes DB schema, restore the matching pre-upgrade database when rolling back to an older binary.

v0.5.0 and v0.9.0 both use schema 1, so rollback between them can reuse the same database as long as no later schema-changing release has run.

For artifact-based rollback:

1. set `TRAKT_BRIDGE_IMAGE` to the previous release tag or exact digest
2. `docker compose pull`
3. recreate the service
4. verify `/health`, `/readiness` and profile connection state
5. verify authoritative sync counts and the client surface

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

Auth state transitions use the stable event id `auth|state`, so a reconnect can be represented as recovery from a prior reconnect-required state instead of a permanent unresolved error.

The DB retains historical failures even after the underlying bug is fixed. Use the dashboard's active-error window for current health.

## Logs

Container logs are appropriate for startup/migration information, uncaught upstream failures and runtime version/schema confirmation.

```bash
docker logs --tail 150 trakt-bridge
```

## Release process

Before creating a release tag:

1. CI passes syntax checks and unit/integration tests
2. CI validates production + development Compose configurations
3. CI builds and smoke-tests the source image
4. HomeDocker source canary passes when required
5. release notes / changelog are current

A `v*` tag triggers the release workflow. The workflow:

1. reruns checks/tests
2. builds and pushes the amd64 GHCR image
3. publishes semver tags and `latest` for stable releases
4. emits SBOM and provenance metadata
5. pulls the exact pushed digest back from GHCR
6. smoke-tests `/health` and `/readiness` from that digest
7. creates or reuses the GitHub Release

A release workflow is not considered successful unless the published-artifact smoke test passes.
