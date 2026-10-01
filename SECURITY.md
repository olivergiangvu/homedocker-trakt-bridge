# Security Policy

HomeDocker Trakt Bridge stores and processes credentials. Treat security issues affecting OAuth tokens, bridge secrets, addon keys, admin access or release artifacts as high priority.

## Supported versions

During the pre-1.0 line, the latest published release is supported for production use. The immediately previous stable release is retained as the rollback baseline while an upgrade is being accepted.

## Sensitive material

Never publish or attach these values to issues, logs or screenshots:

- `TRAKT_CLIENT_SECRET`
- encrypted or decrypted Trakt OAuth tokens
- `BRIDGE_SECRET_KEY`
- `ADMIN_KEY`
- generated profile manifest URLs / addon keys
- production `.env`
- production `bridge.db`

The manifest URL is a credential because it contains a profile-scoped addon key.

## Public-release audit

The repository and GHCR package are public. Before each major public release or after introducing new deployment examples, run `scripts/pre-public-audit.sh` from a full local clone.

For deployment-specific checks, create a local `.pre-public-forbidden` file with one private hostname, IP, port, username or token fragment per line; the file is ignored by Git. Review every credential-shaped match before publishing new artifacts or screenshots.

A passing helper script is not a substitute for GitHub secret scanning or a dedicated secret scanner.

## Reporting

This repository is maintained primarily for the HomeDocker environment. Report a suspected vulnerability privately to the repository owner rather than opening a public issue containing exploit details or credentials.

Include only the minimum non-secret information needed to reproduce the issue: affected version, endpoint or component, expected behavior, observed behavior and sanitized logs.

## Deployment boundary

The supported production topology assumes:

- container port `7000` is bound to loopback only
- host nginx terminates public HTTPS
- Trakt OAuth redirect URI uses the configured public HTTPS origin
- `/app/data` is persistent and access-restricted
- backups include `bridge.db` and `BRIDGE_SECRET_KEY`
- release images are pulled from the public GHCR package and long-lived production installs are preferably pinned by release tag or exact digest

Do not expose port `7000` directly to the public Internet.
