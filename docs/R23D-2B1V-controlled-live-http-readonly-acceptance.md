# R23D-2B1V — Controlled real-source HTTP acceptance, read-only

**State: IMPLEMENTED, LIVE HOST EVIDENCE NOT YET COLLECTED.** This is a deliberately small, operator-executed comparator for the already existing HomeDocker loopback AIO Jellyfin and opt-in Gateway canary. It does **not** deploy software, change nginx routing, attach any Docker networks, restart services, query Trakt, replay watched state, or modify SQLite. **Do not mark the real-client gate PASS based on CI alone.**

## Why reuse rather than duplicate

The repository already includes:
- `scripts/audit-v140-live-nextup-readonly.py`: the RC6 legacy verified-pair provenance + authenticated AIO *SOURCE* NextUp and series-detail probe.
- `scripts/audit-v140-live-resume-alias-readonly.py`: bounded, authenticated *SOURCE* Resume sample, including caution that two active positions cannot be deduplicated.
- `scripts/audit-v140-client-delta.py`: counts newly appended native-nginx Jellyfin client requests from a single operator-initiated action; user agents remain untrusted hints.

**B1V contributes only one missing acceptance axis:** a bounded, SAME-AIO-login comparison of the *source* API with the already deployed, loopback-bound Gateway canary, protecting against false conclusions from non-atomic source snapshots.

## Locked targets and network limits

- AIO official Jellyfin API: `http://127.0.0.1:3240/jellyfin`.
- Existing standalone R23B/B1Q candidate canary listener: `http://127.0.0.1:17141`. **This is a test-only listener, not a production nginx route.** If not listening, preflight must STOP; no alternate IP, tunnel, published port, new container or network is created.
- Default `--compare` makes **exactly ONE authentication POST** to `http://127.0.0.1:3240/jellyfin/Users/AuthenticateByName`, body `{"Username":"…","Pw":"…"}`, then uses the resulting access token in memory for GETs only. **AIO/Jellyfin account**, NOT the operator/dashboard account: username is the *configuration UUID or alias* shown in AIOStreams' **Save & Install → Jellyfin apps → Connect**; a household user may use `alias/name`. Password is the *configuration password* (append `/PIN` for a PIN-protected household account). The POST may create an AIO server-side login session and is **not strictly read-only**; it does **not** write viewing history.
- Allowed GETs: unauthenticated public AIO info and canary `/health`; after login (or explicit existing-token fallback): AIO `/Users/Me`, `/Shows/NextUp?Limit=20` (twice), Gateway same NextUp (once), and optionally `/UserItems/Resume?StartIndex=0&Limit=20` from AIO before+after and Gateway once. AIO `/Users/Me` must return the same ID as the login result.
- The password is entered via a **hidden SSH terminal prompt**; access token and user ID are never printed or saved. Username is entered at the terminal, **never put passwords/tokens in shell commands, chat, flags or files**. Python cannot guarantee physical zeroization of immutable in-memory strings. AIO may retain the newly created server-side session; the script does not attempt an unverified logout API.
- **Username/password default:** `--compare --confirm LIVE_AUTH_LOGIN_GET_ONLY`. **Fallback** to the earlier prompt only with `--compare --existing-token --confirm LIVE_READONLY_GET_ONLY`. No session or password is requested by the default no-flag health preflight.
- GET responses are capped at 2 MiB; auth response to 16 KiB, credential POST body to 8 KiB. Redirects and HTTP proxies are disabled; fixed loopback targets only, no fallback URL or remote login.

## Operator commands — AFTER GitHub CI PASS, SSH on HomeDocker only

From an exact reviewed, immutable commit of this repository (use a detached checkout, never `curl | bash`):

```bash
# First inspect/test the pre-existing two localhost listeners, no login
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py

# Recommended: username + hidden configuration password;
# creates ONE server-side login session, then reads NextUp via GET only
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py \
  --compare --confirm LIVE_AUTH_LOGIN_GET_ONLY

# Optional ONLY if the canary intentionally has Resume passthrough enabled:
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py \
  --compare --confirm LIVE_AUTH_LOGIN_GET_ONLY --include-resume

# Legacy fallback: only when user already possesses a Jellyfin session token
python3 -B scripts/audit-v140-r23d2b1v-real-client-readonly.py \
  --compare --existing-token --confirm LIVE_READONLY_GET_ONLY
```

If source changes between the two reads, print `INCONCLUSIVE_SOURCE_CHANGED` rather than creating a false PASS; the CLI exits code 2 for changing NextUp. If the Gateway changes item ID/`UserData`/watch position, adds cards, changes opaque envelope or mismatches collection count, STOP with no writes. Resume, when enabled, requires byte-for-byte equal source/Gateway representation for a stable source; if 404, report `GATEWAY_RESUME_DISABLED_404`, not an integration PASS.

`SOURCE_ITEM_SUBSET_CONTRACT_PASS` certifies only that a time-stable source returned the same item objects or a subset; it does **not** prove why a card was suppressed or that the Jellyfin user matched the Gateway's configured exact profile. Correlate with existing RC6 graph provenance and B1U isolated code tests. **This cannot certify app/device clients without an operator-triggered client request and separate nginx/request correlation.**

## Release GO/NO-GO still required

- Independent, production-authenticated AIO user and actual native Jellyfin/Remux/Trellis/Strand client acceptance with permissions, real data, careful privacy handling. If another user's legacy Resume is tested, it requires **explicit access to two controlled test users and permission**; B1V does not perform cross-user probing.
- No duplicate write authority: live Trakt timeline, watch-state writers, delayed CW/Trakt sync, history consistency and concurrent client scrobble need separate risk-controlled verification. B1V sends one session-creating authentication POST in username/password mode and **zero playback/history-write API requests**; it cannot claim no background writer exists.
- Gateway app listener and genuine client ingress are different: 17141 localhost success does not prove nginx public route, TLS, or reverse-proxy behavior.
- Root-equivalent Docker API admission bypass reproduced in B1S, incomplete full non-internal tc/nft production enforcement, and intermittent B1P timed-lease renewal remain blockers.

**Absolute decision:** `V140_STABLE_RELEASE_HOLD`; PR #95 stays DRAFT and v1.3.0/RC6 host rollback remains protected. Authentication POST may create a Jellyfin/AIOStreams login session; subsequent data comparisons are GET-only, which may refresh normal caches. No playback/history changes are requested.

**CI note (2026-10-16):** Login-mode safety tests use an in-memory fake opener, no real credentials, and validate the exact single POST method/path/body, auth 401 sanitization, bounded response, ID consistency, hidden terminal input and output redaction. Both prior explicit-token and new password modes require distinct confirmation strings. Only promote this section to CI PASS after the dedicated GitHub-hosted run reports SUCCESS; HomeDocker live comparison is still OPEN.
