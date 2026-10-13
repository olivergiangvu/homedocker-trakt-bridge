# Documentation

This is the versioned user manual for HomeDocker Trakt Bridge.

The repository and GHCR package are public. `v1.3.0` promotes the established RC6 runtime without behavior/schema changes. Publication and on-host cutover require separate release and deployment acceptance; AIOStreams remains independently rolling on `latest`.

## User guides

| Guide | Use it for |
| --- | --- |
| [Setup](SETUP.md) | Install the bridge, connect Trakt and add the manifest to AIOStreams |
| [Configuration](CONFIGURATION.md) | Image version, identity mode, freshness, reverse proxy and AIOMetadata settings |
| [Operations](OPERATIONS.md) | Health checks, backups, updates, rollback and logs |
| [Integrations](INTEGRATIONS.md) | Native Trakt, AIOStreams, AIOMetadata and Jellyfin-compatible clients |
| [Troubleshooting](TROUBLESHOOTING.md) | Common sync, OAuth, rate-limit, identity and duplicate-history diagnostics |

If you are installing the bridge for the first time, start with **[Setup](SETUP.md)**.

## How the system fits together

```mermaid
flowchart LR
    C["Jellyfin-compatible clients"] --> A["AIOStreams"]
    A <--> B["Trakt Bridge"]
    B <--> T["Trakt"]
```

The recommended authority model is:

```text
Trakt        = canonical watched/resume history
Trakt Bridge = sync layer
AIOStreams   = Jellyfin-compatible playback/state surface
AIOMetadata  = metadata/catalog + optional secondary-tracker write fan-out
```

## Advanced / maintainer docs

These pages are intentionally kept out of the main README because most users do not need them:

- [Architecture](ARCHITECTURE.md) — protocol boundaries and authority model
- [Development](DEVELOPMENT.md) — contributor notes, identity edge cases, tests and release engineering
- [Release acceptance records](releases/) — canary, published-artifact and HomeDocker acceptance history
- [RC7 closure and cleanup](RC7-CLOSURE-AND-CLEANUP.md) — operational acceptance, archived PRs, branch housekeeping and Docker safe inventory
- [v1.3 Stable qualification](design/v1.3-stable-qualification.md) — remaining release gates, separate from completed RC7 tooling
- [Security policy](../SECURITY.md)
- [Changelog](../CHANGELOG.md)

`/docs` is the canonical versioned documentation and changes together with the code. A GitHub Wiki can be added later for tutorials/FAQ if the public project grows, without moving release-specific or version-sensitive instructions out of the repository.
