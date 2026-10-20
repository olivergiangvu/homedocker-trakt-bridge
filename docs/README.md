# Documentation

Versioned guidance for **HomeDocker Trakt Bridge**.

**Current published stable:** [v1.4.0](https://github.com/olivergiangvu/homedocker-trakt-bridge/releases/tag/v1.4.0) (2026-10-20), with the integrated, opt-in **read-only Jellyfin NextUp Identity Gateway**. The earlier v1.3.0 stable, v1.4 release candidates, experimental R23D security studies and abandoned client-route experiments remain available as **historical evidence**, not as current release blockers.

GitHub publication does **not** automatically install a release on HomeDocker, enable its listener or route a client through the Gateway.

## Everyday guides

| Document | Purpose |
| --- | --- |
| [Setup](SETUP.md) | New deployment, OAuth, AIOStreams Manifest URL |
| [Configuration](CONFIGURATION.md) | Environment, safe defaults, immutable pin, Gateway flags |
| [Operations](OPERATIONS.md) | Health/readiness, secure backup, updates, rollback |
| [Integrations](INTEGRATIONS.md) | Canonical Trakt authority, AIOStreams, AIOMetadata, native clients |
| [Troubleshooting](TROUBLESHOOTING.md) | Playback, history, 429, identity and Gateway eligibility |
| [Architecture](ARCHITECTURE.md) | Single Trakt writer and optional read-only Gateway boundary |

## Current release and HomeDocker operations

| Record | Status and role |
| --- | --- |
| [v1.4.0 GitHub release](https://github.com/olivergiangvu/homedocker-trakt-bridge/releases/tag/v1.4.0) | **Published Stable**; tag at `33e97c9` |
| [v1.4.0 release notes](releases/v1.4.0.md) | Shipped behavior, limitations, publication details |
| [v1.4 production operations](STABLE-V1.4-OPERATIONS.md) | Safe checks, original RC2→Stable guarded helper, operational boundaries and rollback |
| [v1.4 source qualification](releases/v1.4.0-release-qualification.md) | **Historical pre-tag** approval; not evidence of later live host/client acceptance |
| [CHANGELOG](../CHANGELOG.md) | Chronological release improvements |

### State model

```text
Trakt        = canonical watched/resume history
Bridge       = one writer, AIOStreams watch_state v2 ⇄ Trakt
AIOStreams   = Jellyfin-compatible playback/state surface
Gateway      = optional read-only NextUp identity projection within Bridge
AIOMetadata  = metadata + optional secondary tracker writes, not a second history read authority
```

**Gateway activation is three separate checks:** (1) operator provisions listener and exact same-user source/profile mapping, (2) Identity Filter is effectively AUTO ON unless manually OFF, (3) a client actually reaches an approved NextUp-only route. Full-page/paginated NextUp, Resume, movies and ambiguous cases do not gain automatic normalization.

## Historical and research archive — do not use as current runbooks

- [v1.3.0 Bridge-only deployment](STABLE-DEPLOYMENT.md) — exact RC6→v1.3 instructions **archived**; not a v1.4 upgrade.
- [v1.3 qualification](design/v1.3-stable-qualification.md) and [v1.3 release notes](releases/v1.3.0.md) — historical evidence.
- [v1.4 R23D phase closure / HOLD](V140-PHASE-CLOSURE-AND-RELEASE-HOLD.md) — **historical R23D research risk register**. Its old NO-GO publication checkpoint does not describe the later reviewed practical v1.4 stable scope.
- [RC7 closure and cleanup](RC7-CLOSURE-AND-CLEANUP.md) — archived experiment, rollback and Docker inventory policy.
- [v1.4 gateway RC2 activation](operations/v140-rc2-gateway-ui-activation.md) — RC-specific, not a general-purpose stable client-routing procedure.
- [Release archive](releases/) and [design history](design/) — preserve immutable release/qualification traceability.
- [Development](DEVELOPMENT.md), [Security](../SECURITY.md), [Contributing](../CONTRIBUTING.md).

## Documentation maintenance policy

Current operating instructions live in these concise guides. Keep release-specific and experimental evidence in versioned historical files; do not erase security findings, older deployment SHA/digests, release tags or rollback references simply to shorten the docs.

**For operations:** verify the live Docker image, Compose ownership, database backup and client routing independently. Never infer live state from a README, release page or green CI alone.
