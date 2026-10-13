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

The GHCR package is public and supports anonymous pulls.

If you intentionally follow `latest`:

```bash
docker compose pull
docker compose up -d
```

For predictable production updates, pin `TRAKT_BRIDGE_IMAGE` to a release tag or immutable digest before pulling.

Recommended update flow:

1. Confirm `/health` and `/readiness` are healthy.
2. Back up the database and configuration.
3. Record the currently running image reference and image ID.
4. Change the image tag or digest.
5. Pull the new image while the old container is still running.
6. Recreate the service without a local source build.
7. Re-check `/health`, `/readiness`, dashboard connection state and sync counts.
8. Restart the container once and confirm readiness remains healthy.
9. Confirm the client still shows the expected watched/resume state.

To verify an exact-digest deployment:

```bash
docker inspect trakt-bridge --format '{{.Config.Image}}'
```

The result should be the immutable `ghcr.io/...@sha256:...` reference you intended to deploy.

Do not purge Bridge or AIOStreams state unless the release notes explicitly require it.

## AIOStreams upstream updates — independent rolling policy

AIOStreams is a separate upstream maintained as `ghcr.io/viren070/aiostreams:latest` in HomeDocker. **Do not pin its Compose reference to a digest for the sake of Bridge Stable.** The pinned image policy above applies to the **Trakt Bridge itself**, not to AIOStreams.

For an intentional AIO `latest` update:

1. Record currently running AIO image ID and locally available RepoDigests, Compose/config backup, and any safe rollback material. `scripts/audit-runtime-image-provenance.sh` is read-only and records provenance, **not a requirement to pin**.
2. Preserve existing AIO watch-state data; use the existing backup/disaster recovery procedure. Avoid copying live WAL SQLite files as an inconsistent backup.
3. Update AIO independently according to its own release procedure, without implicitly upgrading or rebuilding Bridge.
4. Re-check AIO health, exact connected Bridge sink, pending delivery state, Jellyfin Resume/NextUp behavior, canonical watched state, and known identity-drift fixture on real clients.
5. If a compatibility regression is proven, recover the prior known-good AIO image/config as a temporary rollback; then return to upstream `latest` when compatibility is restored.

Stable Bridge certification is **contract-based**. No exact AIO digest is a prerequisite, and a new AIO release does not in itself require a new Bridge release. Do not automatically mark history, drop aliases, or replay Trakt data when an AIO update happens.

## Rollback

Set `TRAKT_BRIDGE_IMAGE` back to the previous known-good tag or digest, then:

```bash
docker compose pull
docker compose up -d
```

If a future release changes the database schema, restore the matching pre-upgrade database before running an older binary.

For schema-compatible releases, still verify `/readiness`, profile connection state and client watched/resume state after rollback instead of assuming image replacement alone is sufficient.

## RC7 phase housekeeping (safe, non-destructive)

The RC7 rolling compatibility phase passed its HomeDocker API contract checks on 2026-10-13 and experimental PRs #83 and #85 were closed **without merge**. The canonical read-only monitors are in main via PR #86; Bridge production stays RC6 and AIOStreams continues upstream `latest`. This is **not** a v1.3 Stable promotion.

- Run `scripts/audit-homedocker-docker-cleanup.sh` to enumerate Docker CPU/RAM/disk, stopped containers, build cache, dangling volumes/images and RC7 local scratch filenames **without deleting anything**.
- Run `scripts/cleanup-github-branches.py` under an authenticated `gh` CLI for a dry-run list of historical merged branches and documented closed RC7 experiments. Use `--apply` only after inspecting the candidates; it never removes tags, active PR branches, Dependabot branches or main.
- Do not assume stopped containers, dangling images or unused volumes are safe to prune. They may contain rollback images, state or disaster recovery backups. Never run broad `docker system prune -a --volumes`, `docker volume prune`, `docker compose down -v` or delete `/var/lib/docker`.
- Preserve the one-off repair rollback reference in [RC7 closure and cleanup](RC7-CLOSURE-AND-CLEANUP.md), but never schedule automatic series-drop/Trakt history repairs.

Inspect exact Docker cleanup candidates before removal; removing a Github branch **does not free Docker RAM**.

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

The bridge protects Trakt traffic with write pacing, profile-wide `429` cooldown handling, retry metadata and stale-state fallback. If Trakt returns `429`, the dashboard records the affected endpoint and retry delay.

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
