# Stable Release Governance — Feature Preservation

**Effective 12 November 2026. Applies to all future releases, including hotfixes.**

A Stable must preserve every previously user-accepted capability. This policy follows the Episode Info Plus regression in which an already verified Now Playing poster feature was lost in a newer Stable (v1.3.4), then restored and protected by CI in v1.3.5.

## Mandatory gates

1. Base new work on the latest accepted Stable; compare all older-feature contracts when merging parallel branches.
2. Never remove a shipped and accepted feature without explicit owner-approved deprecation, documented impact and migration.
3. Maintain a protected capability inventory with regression tests and an acceptance checklist. A fix for one bug is not permission to delete unrelated features.
4. CI must pass the full existing suite and test the **actual deliverable**, including runtime files, Docker image/configuration and deployment assets; code-only checks are insufficient.
5. Validate persistent data, authentication, secrets handling, service APIs, Docker Compose integration and backward compatibility.
6. For integration changes, require real-environment smoke tests (SSH/Docker/Trakt/AIOStreams/IINA as applicable); mocks are not sufficient.
7. Do not mark a release Stable/latest without green CI, final artifact validation and explicit acceptance. Otherwise keep Beta/RC.
8. Preserve tags and historical commits. Demote a noncompliant Stable to historical pre-release once a verified replacement ships.
9. Record version, release notes, rollback plan, tested capabilities and CI evidence for every Stable.
10. New repositories must adopt this policy before their first Stable.

## Release acceptance

- [ ] Latest stable baseline and protected features compared
- [ ] All existing features preserved or formally deprecated
- [ ] Existing unit and integration tests pass
- [ ] Full, installable Docker/plug-in/app artifact and dependencies verified
- [ ] Required real-world smoke tests documented
- [ ] No regression to state, security or integration interfaces
- [ ] Rollback is available and release notes are accurate
- [ ] Explicit user acceptance obtained before promoting Stable

This is an owner-mandated policy. Repository-specific automated checks must be implemented in CI; a document alone does not enforce a release gate. **Do not promote Stable until those checks and acceptance evidence exist.**

Reference: https://github.com/olivergiangvu/iina-episode-info-plus/blob/main/docs/RELEASE_GATES.md
