# v1.4.0 Stable — HomeDocker operator guide

**Release:** [v1.4.0](https://github.com/olivergiangvu/homedocker-trakt-bridge/releases/tag/v1.4.0), published **2026-10-20** from exact source commit `33e97c9ffc52003f7f9ac11f37b7c092ef993207`.

**Immutable GHCR reference in the guarded HomeDocker helper:**

```text
ghcr.io/olivergiangvu/homedocker-trakt-bridge@sha256:61c28bd22155950cacfd7ac47e5d5259a74e55c5b99c3dc4b63d95be524f2c77
```

This guide describes **what was released** and **what must be checked on the host**. Publishing the tag does not prove a particular HomeDocker container is running it. Reported runtime evidence must be dated; re-check live health, configured image, source-specific Gateway activation and client ingress whenever making an operational claim.

## Release boundary

- **One process / one Trakt writer / one SQLite database.** No second Bridge deployment for the Gateway.
- Canonical Trakt `watched/resume` and AIOStreams `watch_state` v2 remain in place. No automatic history repair, AIO SQLite rewrite or reconciler writeback.
- Read-only Gateway normalization applies **only** to verified TV-series alias collisions for supported `GET /Shows/NextUp` request shapes. It fails open to an unmodified upstream result for unsupported/paginated/ambiguous cases.
- Gateway listener is **OFF by default** in public examples. An operator must enable it with exact upstream/source/user/profile mapping. The effective Identity Filter is AUTO ON for a valid provisioned mapping, subject to persistent manual OFF.
- Enabling the listener/filter does **not** change host nginx, QuickConnect, Odin, Strand, Remux, Trellis or AIOStreams Desktop routes. Do not route entire Jellyfin traffic to a NextUp-only listener. Gateway port `7101` must not be publicly exposed.

## Read-only production verification (Linux SSH)

Use the actual production container name if different from `trakt-bridge`. These commands do not deploy, restart or print environment credentials.

```bash
docker inspect trakt-bridge --format \
  'name={{.Name}} state={{.State.Status}} image={{.Config.Image}} id={{.Image}} restarts={{.RestartCount}}'
docker ps --filter name=trakt-bridge --format 'name={{.Names}} status={{.Status}}'
curl -fsS http://127.0.0.1:7000/health
curl -fsS http://127.0.0.1:7000/readiness
```

Check `version=1.4.0` and `ready=true` in the actual response, as applicable. The immutable image reference above should be the configured `.Config.Image`; do not infer digest equality from `:latest` or a tag name. Separately check only one running Bridge writer, AIOStreams continuity, dashboard auth, exact Gateway source/user/provisioning, Identity Filter effective state, and the actual authorized client NextUp route. A listener responding is not a client-ingress acceptance test.

## Original RC2 → Stable cutover helper (not a routine update tool)

The repository includes `scripts/deploy-v1.4.0-homedocker.py` for a **specific** protected HomeDocker Compose environment:

- Requires the original checksum-verified `v1.4.0-rc.2` image ID, exact Compose ownership, `compose.yml` **and** `compose.gateway.yml`, root-protected configuration and known Gateway flags.
- Default mode is **read-only preflight**. It refuses missing/changed prerequisites rather than adapting to an arbitrary installation.
- An explicitly authorized `--apply` performs verified immutable-image pull, SQLite online backup, protected Compose and `.env` copies, one-service `trakt-bridge` recreate, health/readiness/one-writer verification, and an RC2 rollback attempt on qualifying failure.
- It does **not** redeploy AIOStreams, rewrite Trakt history, alter host nginx/client ingress or delete old Docker images.
- **Never rerun `--apply` on a host already running v1.4 Stable.** The RC2-only preflight must reject it. Do not bypass the refusal with manual edits.

To inspect the preflight logic in a trusted checkout, the read-only invocation is:

```bash
sudo python3 -B scripts/deploy-v1.4.0-homedocker.py
```

If it reports STOP, treat that as a safety condition; do not follow it with a generic `docker compose up -d` or a Portainer whole-stack redeploy. Use the existing validated recovery procedure and protected backups for any future upgrade.

## Rollback and disaster recovery

Preserve the previous known-good image and the matching protected Compose/`.env` and **consistent SQLite online backup**. The documented HomeDocker RC2 rollback identity is:

```text
image ID sha256:91ac17af1edc15297d45caad466b76d07f2edb4091314e68260694b6b87c417c
```

A separate preserved RC6 fallback exists in operator records; keep confirmed rollback assets until deliberately retired. Do not start two Bridge containers sharing the same `/app/data` or OAuth credentials. Avoid routine database restore on a schema-compatible binary rollback; if a restore is required, use the matched protected snapshot under a controlled recovery decision.

**Backup scope:** `/app/data/bridge.db`, `.env`, `compose.yml`, `compose.gateway.yml` when used, and relevant nginx configuration. Never commit credentials, SQLite snapshots, or live Manifest URLs to GitHub.

## Safe post-test cleanup

Cleanup is a **separate host operation** from the v1.4 release. Use [read-only Docker inventory](../scripts/audit-homedocker-docker-cleanup.sh) and explicit image/container IDs to identify retired canaries and test artifacts. Keep all running production containers, bind mounts, networks, volumes and protected rollback images. Do **not** apply blanket `docker image prune -a`, `docker system prune --volumes`, `docker volume prune`, `docker compose down -v` or automatic deletion of untagged images. Docker disk space and Python RAM are separate concerns; do not modify CrossWatch sync state merely to reclaim Bridge test images.

## Remaining limitations / follow-up

- Normalization is bounded to eligible NextUp TV alias collisions, not universal deduplication.
- Missing/ambiguous source identity, conflicting Resume positions, full/paginated NextUp, unsupported Desktop/Odin query shapes, movies and `/UserItems/Resume` are **not** silently rewritten.
- Off-window/large-shelf correction is research only; do not promise shelf insertion or client playback fixes.
- Real client routing and 24-hour stability require separate evidence; GitHub CI cannot establish these.
- Older [R23D security experiments](V140-PHASE-CLOSURE-AND-RELEASE-HOLD.md) are historical out-of-scope evidence for the narrower practical v1.4 release, not current proof of host-wide kernel boot fencing.

See [Operations](OPERATIONS.md), [Architecture](ARCHITECTURE.md) and [v1.4 release notes](releases/v1.4.0.md).
