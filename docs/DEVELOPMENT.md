# Development

This document contains implementation-level material that is intentionally not shown on the operator dashboard.

## Scope

HomeDocker Trakt Bridge implements AIOStreams `watch_state` v2 push/pull semantics against Trakt.

Operator-facing behavior is documented elsewhere. This file is for protocol assumptions, identity edge cases, cache/idempotency rules and regression history.

## AIOStreams contract assumptions

The bridge relies on these behaviors:

- push event IDs are stable across retries
- `metaId`, `videoId` and shared provider IDs can describe media identity
- missing duration means unknown, not zero
- movie/show watchlist events are represented separately from playback
- pull responses use a stable `version`/`since` cursor model

The manifest advertises the bridge's `watch_state` push + pull endpoints.

## Trakt write mapping

Current mapping:

```text
start                  -> scrobble start when progress >= 1%
pause                  -> scrobble pause when progress >= 1%
unfinished stop        -> scrobble pause when progress >= 1%
completed stop         -> scrobble stop
played                 -> history add
unplayed               -> history remove
watchlisted            -> watchlist add
unwatchlisted          -> watchlist remove
bulk played/unplayed   -> nested history sync request
```

Sub-1% safety:

```text
progress < 1%
  start / pause / unfinished stop -> ignored locally + acknowledged
  explicit played stop           -> history add
```

The guard prevents deterministic Trakt `422` retry loops.

## Idempotency

`processed_events` stores successfully handled event IDs.

A retry of a processed event is acknowledged without repeating the upstream write.

Bulk history writes also record bounded coverage so redundant per-episode echoes can be ignored safely.

## Pull cache

Pull state is cached in memory and SQLite.

The current persisted prefix remains:

```text
pull-state:v5:<profileId>
```

v0.4.0 intentionally does not change the v0.3.6/v0.3.7 pull representation.

Bounded stale fallback is allowed for transient Trakt `429` / `5xx` failures when the requested cursor matches a safe cached state.

## Pull identity modes

```env
PULL_IDENTITY_MODE=trakt
PULL_IDENTITY_MODE=aiostreams
```

### `trakt`

Default HomeDocker mode.

Trakt's IMDb spelling remains the effective pull identity.

Use this when clients may consume both native Trakt and AIOStreams state.

### `aiostreams`

Legacy compatibility mode preserving the v0.3.5 learned-alias rewrite behavior.

A learned AIOStreams IMDb spelling can replace Trakt's IMDb spelling in future pull rows for the same stable Trakt show.

## Historical dual-IMDb regression fixture

A production title exposed the same show under two IMDb spellings:

```text
Trakt IMDb:      tt44051354
AIOStreams IMDb: tt44094505
TMDB:            276470
TVDB:            480791
```

The stable Trakt show ID used during diagnostics was:

```text
263102
```

### Regression sequence

v0.3.4 introduced persistent IMDb-to-IMDb alias reconciliation.

v0.3.5 made unfinished episode stops able to teach the alias.

With a client such as Strand using both native Trakt and AIOStreams:

```text
native Trakt path  -> tt44051354
Bridge pull path   -> learned rewrite -> tt44094505
```

The client then saw two identities for the same episode and rendered duplicate Continue Watching cards.

### Resolution

v0.3.6 introduced:

```env
PULL_IDENTITY_MODE=trakt
```

In this mode:

- alias evidence is still stored
- the stable Trakt/TMDB/TVDB relationships are preserved
- the learned alias does not rewrite pull identity
- native Trakt and Bridge pull converge on `tt44051354`

This production pair is covered by regression tests and should remain a permanent identity fixture.

## Learned alias store

Alias evidence is stored profile-scoped under:

```text
identity-alias:v1:<profileId>
```

Each show entry can contain:

```text
stable Trakt show ID
preferred AIOStreams IMDb
Trakt IMDb
updated timestamp
```

In `trakt` mode this is diagnostic evidence only; it must not change effective pull identity.

The operator UI intentionally does not expose these rows. Use `/status` or the DB when debugging identity behavior.

## Database schema

v0.4.0 introduces explicit migration tracking through:

```text
PRAGMA user_version
```

Current schema:

```text
1
```

Migration policy:

- migrations are ordered and explicit
- startup migrates supported older schemas forward
- a DB newer than the binary is rejected
- rollback across schema versions requires the matching backup

Do not manually edit `user_version` to bypass compatibility checks.

## Operational status model

`/status` can expose deeper diagnostics than the dashboard.

This is intentional:

```text
Dashboard = operator decisions
/status   = machine-readable diagnostics
DB        = forensic detail
```

Avoid promoting one-off debugging evidence into the normal UI unless it changes an operator decision.

## Tests

Every functional regression should receive a unit/integration fixture before merge.

Current CI expectations:

```text
npm run check
npm test
Docker production build
/health smoke test
/readiness smoke test
```

Node 24 is the supported test/runtime baseline for the container.

## Release engineering

A `v*` tag runs the release workflow.

The workflow:

1. runs checks/tests
2. logs into GHCR
3. builds the production amd64 image
4. publishes semver/SHA tags
5. smoke-tests the exact published digest
6. creates or synchronizes the GitHub Release

When `docs/releases/<tag>.md` exists, that versioned file is the canonical GitHub Release body. The workflow uses it both for a new release and to synchronize an existing release created with the tag. If no versioned notes file exists, GitHub-generated notes are used as the fallback.

Do not create a stable tag until source/RC tests, compatibility checks, and the intended production configuration have been qualified. The exact published-digest production canary and deterministic rollback test happen after the tag is published because that immutable artifact does not exist beforehand.
