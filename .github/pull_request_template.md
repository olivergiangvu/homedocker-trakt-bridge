## What changed

<!-- Briefly describe the change. -->

## Why

<!-- What problem does this solve? -->

## Validation

- [ ] `npm run check`
- [ ] `npm test`
- [ ] Docker/Compose behavior checked when relevant
- [ ] `/health` and `/readiness` checked when relevant

## Compatibility

- DB schema impact: none / describe
- Watch-state or identity impact: none / describe
- Deployment/config impact: none / describe
- Migration or rollback notes: none / describe

## Security

- [ ] No credentials, tokens, manifest URLs, private hostnames or live database content are included

## Feature preservation / Stable promotion (required)

- [ ] Based on latest accepted Stable; compared with the previously accepted feature inventory
- [ ] No accepted functions, state contracts or runtime dependencies silently removed
- [ ] Existing regression suites and final Docker/Compose deployment artifacts validated
- [ ] Required live integration smoke tests documented; mock-only is not enough for Stable
- [ ] If releasing Stable: explicit owner acceptance, release notes, CI evidence and rollback plan
- [ ] Any intentional deprecation separately approved and documented

See [docs/RELEASE_POLICY.md](../docs/RELEASE_POLICY.md). Otherwise publish Beta/RC only.
