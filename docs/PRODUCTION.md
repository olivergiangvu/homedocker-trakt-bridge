# Production operations — v0.4.0

## Service boundaries

Production assumptions:

```text
host nginx terminates TLS
container port 7000 binds to 127.0.0.1 only
/app/data is persistent
BRIDGE_SECRET_KEY is backed up
ADMIN_KEY is treated as a credential
PULL_IDENTITY_MODE=trakt for HomeDocker
```

Do not expose port 7000 directly to the Internet.

## Health surfaces

### Liveness

```bash
curl -fsS http://127.0.0.1:7000/health
```

Expected:

```json
{"status":"ok","app":"HomeDocker Trakt Bridge","version":"0.4.0"}
```

### Readiness

```bash
curl -sS -i http://127.0.0.1:7000/readiness
```

Ready production service:

```text
HTTP 200
status = ready
database = ok
schemaVersion = 1
connectedProfiles >= 1
```

A fresh install before Trakt OAuth remains alive but returns:

```text
HTTP 503
status = setup_required
```

Readiness does not call Trakt and therefore does not consume rate limit.

### Operator status

Authenticated JSON:

```bash
curl -fsS "http://127.0.0.1:7000/status?key=$ADMIN_KEY"
```

It exposes safe operational diagnostics only. Do not publish the resulting URL because the admin key is a credential.

## Backup

Back up together:

- Docker volume containing `/app/data/bridge.db`;
- `.env`;
- compose definition;
- host nginx site configuration.

DR-critical secret:

```text
BRIDGE_SECRET_KEY
```

Losing or changing it makes existing encrypted Trakt tokens unreadable and changes derived setup/addon credentials.

## Database migration

v0.4.0 introduces explicit SQLite schema version 1.

Before upgrade:

```bash
sqlite3 bridge.db 'PRAGMA user_version;'
```

A v0.3.x database normally reports `0`. First v0.4.0 start applies migration 1 in place and preserves profiles, tokens, aliases, processed events and cache.

After upgrade:

```bash
sqlite3 bridge.db 'PRAGMA user_version;'
```

Expected:

```text
1
```

A database with a schema newer than the binary fails closed rather than attempting a downgrade.

## Upgrade sequence

Recommended HomeDocker flow:

```text
Version Center detects update
        |
review release notes
        |
pre-update DR snapshot
        |
pull source / release image
        |
run tests when building locally
        |
recreate container
        |
/health
        |
/readiness
        |
operator UI/status check
        |
keep or rollback
```

Do not auto-update the bridge blindly.

## Rollback

Before every schema-changing release, preserve a consistent SQLite backup plus `.env` and compose.

If a v0.4.0 rollback to v0.3.7 is required, restore the **pre-v0.4.0 database backup** as well. v0.3.7 does not understand the formal schema-version lifecycle introduced in v0.4.0.

The AIOStreams pull representation itself did not change in v0.4.0, so no new AIOStreams watch-state cache purge is required.

## Production validation checklist

After deploy:

```text
[ ] /health = 200, version 0.4.0
[ ] /readiness = 200 ready
[ ] DB schema = 1
[ ] Trakt profile = connected
[ ] PULL_IDENTITY_MODE = trakt
[ ] last pull succeeds
[ ] unresolved errors = 0
[ ] dual-IMDb diagnostics effective pull = Trakt spelling
[ ] AIOMetadata Trackers = This server only
[ ] Strand native Trakt + AIOStreams shows no duplicate Continue Watching card
```

## Release images

Tagged releases publish to GHCR through `.github/workflows/release.yml`.

Production should pin an explicit version or digest. Do not deploy `latest`.
