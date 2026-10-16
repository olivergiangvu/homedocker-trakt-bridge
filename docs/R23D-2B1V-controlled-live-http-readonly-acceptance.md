# R23D-2B1V — Controlled real-source HTTP acceptance, read-only

**State: IMPLEMENTED, LIVE HOST EVIDENCE NOT YET COLLECTED.** This is a deliberately small, operator-executed comparator for the already existing HomeDocker loopback AIO Jellyfin and opt-in Gateway canary. It does **not** deploy software, change nginx routing, attach any Docker networks, restart services, query Trakt, replay watched state, or modify SQLite. **Do not mark the real-client gate PASS based on CI alone.**

## Why reuse rather than duplicate

The repository already includes:
- `scripts/audit-v140-live-nextup-readonly.py`: the RC6 legacy verified-pair provenance + authenticated AIO *SOURCE* NextUp and series-detail probe.
- `scripts/audit-v140-live-resume-alias-readonly.py`: bounded, authenticated *SOURCE* Resume sample, including caution that two active positions cannot be deduplicated.
- `scripts/audit-v140-client-delta.py`: counts newly appended native-nginx Jellyfin client requests from a single operator-initiated action; user agents remain untrusted hints.

**B1V contributes only one missing acceptance axis:** a bounded, SAME-existing-session comparison of the *source* API with the already deployed, loopback-bound Gateway canary, protecting against false conclusions from non-atomic source snapshots.

## Locked targets and network limits

- AIO official Jellyfin API: `http://127.0.0.1:3240/jellyfin`.
- Existing standalone R23B/B1Q candidate canary listener: `http://127.0.0.1:17141`. **This is a test-only listener, not a production nginx route.** If not listening, preflight must STOP; no alternate IP, tunnel, published port, new container or network is created.
- Allowed GETs only: unauthenticated public AIO info and canary `/health`; with an existing Jellyfin session token: AIO `/Users/Me`, `/Shows/NextUp?Limit=20` (twice), Gateway same NextUp (once), and *optionally* `/UserItems/Resume?StartIndex=0&Limit=20` from AIO before+after and Gateway once.
- No login POST: the user enters an *existing* session token via a hidden SSH terminal prompt. The token is never saved or emitted. **Do not put it in command arguments, shell history, chat or a file.**
- Explicit `--compare --confirm LIVE_READONLY_GET_ONLY` is required before any session prompt or history-reading GETs.
- GET responses are capped at 2 MiB each; only aggregate counts, booleans and sanitized result enums may be printed. Redirects and proxies are rejected/bypassed.

## Operator commands — AFTER GitHub CI PASS, SSH on HomeDocker only

From an exact reviewed, immutable commit of this repository (use a detached checkout, never `curl | bash`):

```bash
# First inspect/test the pre-existing two localhost listeners, no login
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py

# Operator-approved read-only comparison using an ALREADY EXISTING token
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py \
  --compare --confirm LIVE_READONLY_GET_ONLY

# Optional ONLY if the canary intentionally has Resume passthrough enabled:
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py \
  --compare --confirm LIVE_READONLY_GET_ONLY --include-resume
```

If source changes between the two reads, print `INCONCLUSIVE_SOURCE_CHANGED` rather than creating a false PASS; the CLI exits code 2 for changing NextUp. If the Gateway changes item ID/`UserData`/watch position, adds cards, changes opaque envelope or mismatches collection count, STOP with no writes. Resume, when enabled, requires byte-for-byte equal source/Gateway representation for a stable source; if 404, report `GATEWAY_RESUME_DISABLED_404`, not an integration PASS.

`SOURCE_ITEM_SUBSET_CONTRACT_PASS` certifies only that a time-stable source returned the same item objects or a subset; it does **not** prove why a card was suppressed or that the Jellyfin user matched the Gateway's configured exact profile. Correlate with existing RC6 graph provenance and B1U isolated code tests. **This cannot certify app/device clients without an operator-triggered client request and separate nginx/request correlation.**

## Release GO/NO-GO still required

- Independent, production-authenticated AIO user and actual native Jellyfin/Remux/Trellis/Strand client acceptance with permissions, real data, careful privacy handling. If another user's legacy Resume is tested, it requires **explicit access to two controlled test users and permission**; B1V does not perform cross-user probing.
- No duplicate write authority: live Trakt timeline, watch-state writers, delayed CW/Trakt sync, history consistency and concurrent client scrobble need separate risk-controlled verification. B1V sends zero write API requests but cannot claim no background writer exists.
- Gateway app listener and genuine client ingress are different: 17141 localhost success does not prove nginx public route, TLS, or reverse-proxy behavior.
- Root-equivalent Docker API admission bypass reproduced in B1S, incomplete full non-internal tc/nft production enforcement, and intermittent B1P timed-lease renewal remain blockers.

**Absolute decision:** `V140_STABLE_RELEASE_HOLD`; PR #95 stays DRAFT and v1.3.0/RC6 host rollback remains protected. Read-only HTTP may still refresh the upstream application's normal caches; no configuration/data mutation is requested.
