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

The web dashboard is the preferred human-readable view for connection status, sync health, recent events and current errors. In v1.4, the unified dashboard can also report a separately provisioned Gateway listener and the effective AUTO ON/manual OFF Identity Filter; these indicators alone do not verify actual client ingress.

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
compose.gateway.yml (when a Gateway overlay is configured)
reverse-proxy configuration
```

`BRIDGE_SECRET_KEY` is required to decrypt stored Trakt OAuth tokens after restore, so losing `.env` can make an otherwise valid database backup unusable.

For a live SQLite database, prefer SQLite's online backup mechanism instead of blindly copying a WAL-active database.

## Update — v1.4.0 Stable

**Latest published stable:** [v1.4.0](https://github.com/olivergiangvu/homedocker-trakt-bridge/releases/tag/v1.4.0). Use the [v1.4 production operator guide](STABLE-V1.4-OPERATIONS.md), not the historic v1.3 RC6 upgrade helper. The original guarded v1.4 cutover script is **RC2-specific** and should not be re-applied after a successful stable cutover.

For each production change:

1. Read the target release notes and verify the exact immutable image digest.
2. Check which Compose project owns the live Bridge; preserve its Compose overlays, `.env`, Gateway source/user mapping and permissions.
3. Confirm one running Bridge writer and save the old image reference, image ID and restorable SQLite online backup.
4. Recreate **only** the Bridge service through the owner's qualified procedure (no full-stack `up -d`, `down`, Portainer redeploy or `-v`).
5. Verify health/readiness, Trakt identity, watched/resume and AIOStreams continuity. Gateway listener/filter state and any real client NextUp route must be checked **separately**.
6. Keep previous known-good image and backup until acceptance/rollback window closes.

For read-only image confirmation:

```bash
docker inspect trakt-bridge --format '{{.Config.Image}}'
curl -fsS http://127.0.0.1:7000/health
curl -fsS http://127.0.0.1:7000/readiness
```

A public release or passing CI is not live HomeDocker evidence. Do not purge state or force Trakt resync during an image update.

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

Use the **previous verified image** and the matching protected Compose/`.env` (including `compose.gateway.yml` where applicable). Prefer a qualified **Bridge-only** recreate under the original Compose project; never start a second Bridge with the same `/app/data` and Trakt OAuth credentials.

The original v1.4 RC2 fallback and exact image ID are documented in [v1.4 production operations](STABLE-V1.4-OPERATIONS.md). Verify `/health`, `/readiness`, configured image ID, single-writer status and ordinary watched/resume behavior after rollback. A schema-compatible binary rollback does not automatically require a database restore; if restoration is necessary, use an intentionally selected matching SQLite online backup.

## Historical RC7 housekeeping (safe, non-destructive)

The RC7 rolling compatibility phase passed its HomeDocker API contract checks on 2026-10-13 and experimental PRs #83 and #85 were closed **without merge**. The canonical read-only monitors are in main via PR #86; The production Bridge was on RC6 at the date of that RC7 qualification; this **historical** observation must not be read as the current v1.4 runtime. AIOStreams continues its independent rolling-upstream policy.

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
