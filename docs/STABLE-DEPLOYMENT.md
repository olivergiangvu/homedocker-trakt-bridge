# HomeDocker Stable deployment — v1.3.0 (Bridge only)

**Release published:** [v1.3.0](https://github.com/olivergiangvu/homedocker-trakt-bridge/releases/tag/v1.3.0), immutable tag `8c505089e0c5ad2e7ce1514bd9c1acf1722bea95`, published 2026-10-14. The image release changes Bridge version metadata versus qualified RC6, **not application logic or DB schema**.

**Host status:** the latest independently evidenced HomeDocker application runtime was `v1.3.0-rc.6`. Do not infer stable deployment merely from the public GitHub Release, from CI or from pushing documentation. This runbook is only valid if the actual host preflight reconfirms the qualified RC6 digest.

**Latest operator tooling:** `main` at commit `ef336f4b730ec14bf995fa83cf170f034dc382a1`, which is one commit **after** the release tag. The post-tag difference is only corrected privileged Compose deployment tooling, tests and release notes; stable application image remains the original v1.3.0 tag. To avoid ref drift, **use this exact immutable tooling commit for the host procedure**, not an unqualified `main` or Draft v1.4 branch.

## Non-negotiable scope and safety

- **Only recreate the Trakt Bridge Compose service.** Do not run `docker compose up -d` without a service target, `docker compose pull` for the entire stack, or a Portainer stack redeploy.
- Preserve AIOStreams `ghcr.io/viren070/aiostreams:latest`, its running image ID, Jellyfin user/sink, data, and existing dropped-series preference. AIO upstream updates have their own rolling compatibility process.
- Preserve nginx, DNS/routers, CrossWatch, Trellis, Remux, Docker networks, RC6/R23A/R23B rollback artifacts, profiles, tokens, and Trakt watch history.
- Do **not** enable recovery writeback, history repair, bulk history deletion or new feature flags. Existing qualified HomeDocker `AIO_RECONCILER_MODE=compare` remains non-writing.
- Reject Compose/Portainer ownership ambiguity. `deploy-v1.3.0-homedocker.py` is designed for the qualified local root-protected Compose `.env` using `TRAKT_BRIDGE_IMAGE` interpolation, and intentionally STOPS when it cannot reproduce the exact stack.
- No v1.4 R23D gateway: Draft PR #95 cannot ship until the independent boot fence, kernel enforcement on the actual Docker bridge, guardian kill path and reboot tests are independently qualified.

## Step 1 — Read-only host preflight (operator via Linux SSH)

A fresh detached checkout avoids running a mutable branch or a script copied to an unknown location. The script runs privileged **read-only** inspection of Compose, original RC6 image, local Bridge health/readiness, exact flags and RC7 mitigation; it makes no Docker pulls, container restarts, Compose edits or schema migrations.

```bash
(
set -euo pipefail
SHA="ef336f4b730ec14bf995fa83cf170f034dc382a1"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
git clone --quiet --no-checkout \
  https://github.com/olivergiangvu/homedocker-trakt-bridge.git "$WORKDIR/repo"
git -C "$WORKDIR/repo" checkout --quiet --detach "$SHA"
test "$(git -C "$WORKDIR/repo" rev-parse HEAD)" = "$SHA"
test -z "$(git -C "$WORKDIR/repo" status --porcelain)"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s "$WORKDIR/repo/test" -p 'test_v130_stable_release.py' -q
sudo -v
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  "$WORKDIR/repo/scripts/deploy-v1.3.0-homedocker.py"
)
```

Required high-level result: `RC6_BASELINE=READY`, `SAFE_FLAGS=TRAKT_COMPARE_WITHOUT_ONDEMAND`, `RC7_IDENTITY=KNOWN_PAIR_SAFE_READONLY`, `MODE=PREFLIGHT_READ_ONLY`, `NO_CHANGES_MADE=true`. A STOP means stop: **never** bypass the checks by hand-editing `.env`, replacing images, restarting AIO or deleting data.

## Step 2 — Separately accepted Bridge-only deployment

Only if the actual fresh Step 1 output passes, and operator accepts the automatic Bridge-only backup/recreate/rollback behavior, repeat the exact checked-out script with `--apply`:

```bash
(
set -euo pipefail
SHA="ef336f4b730ec14bf995fa83cf170f034dc382a1"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
git clone --quiet --no-checkout \
  https://github.com/olivergiangvu/homedocker-trakt-bridge.git "$WORKDIR/repo"
git -C "$WORKDIR/repo" checkout --quiet --detach "$SHA"
test "$(git -C "$WORKDIR/repo" rev-parse HEAD)" = "$SHA"
test -z "$(git -C "$WORKDIR/repo" status --porcelain)"
sudo -v
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  "$WORKDIR/repo/scripts/deploy-v1.3.0-homedocker.py" --apply
)
```

**What apply does:** verify existing exact RC6 immutable digest and active Compose ownership; check release; pull only `ghcr.io/olivergiangvu/homedocker-trakt-bridge:1.3.0` and resolve its actual `RepoDigests` (never guess the new digest); perform SQLite online backup and copy root-protected Compose/`.env` to a restrictive operator backup directory; update only `TRAKT_BRIDGE_IMAGE` to the immutable stable digest; run `docker compose up -d --no-deps --no-build --pull never trakt-bridge` using the original Compose project; verify v1.3.0 health/readiness, unchanged AIO running image ID, safe compare-only flags and continuing RC7 mitigation. On qualifying failure after editing `.env`, attempt automatic RC6 image reference rollback and re-check readiness. A rollback attempt is **not proof** it succeeded. Backup may contain OAuth/Trakt secrets; never publish it to a GitHub issue or terminal transcript.

**Do not execute Step 2 merely because GitHub CI passed.** Step 1 is the host-level gate and may legitimately STOP if the current installation differs or has already been upgraded.

## Step 3 — Post-deployment acceptance evidence

Require `DEPLOYMENT=SUCCESS`, `BRIDGE_VERSION=1.3.0`, `BRIDGE_PINNED_IMAGE=ghcr.io/olivergiangvu/homedocker-trakt-bridge@sha256:...`, `AIOSTREAMS_LATEST_NOT_TOUCHED=true`, RC7 identity PASS and safe flags. Independently verify a normal client watched/resume case and existing Trakt/AIO compare diagnostics without manually forcing any history write. Preserve backup/rollback reference and monitor retained event pulse, 429, 422 and duplicate NextUp behavior. If the user's health output indicates STOP, keep or restore RC6 and investigate exactly that gate; do not mass-redeploy.

## Release/documentation policy

- Published image/tag: immutable v1.3.0, do not rewrite the tag or publish an ambiguous `latest` substitute.
- Post-tag main helper: source-only operator tooling; the helper's existence alone does not mutate a HomeDocker machine.
- Historic release notes `docs/releases/v*.md` and exact production evidence `docs/design/v1.3-stable-qualification.md` are archived **in place** for audit/rollback rather than deleted.
- `docs/README.md` is the canonical index for currently supported documentation. Experimental R23D audit notebooks remain isolated in Draft PR #95 and must not be promoted to stable docs.
