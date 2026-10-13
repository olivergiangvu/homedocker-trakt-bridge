# RC7 Phase B — Jellyfin Continue Watching shelf comparison

**Status:** diagnostic PR only. No RC7 image release, production cutover, data migration, or automatic repair.

## Why this test exists

HomeDocker 2026-10-13: RC6 is healthy and pinned. AIO audit of 8,840 episode rows found no concurrent active IMDb resume alias rows. A later query found zero `tt44094505` rows and ten `tt44051354` episodes (S01E01–09 played, S01E10 local paused at 346626 ms); operator reports **two Continue Watching cards on all clients**.

AIOStreams (upstream `packages/server/src/routes/jellyfin/library.ts`) independently exposes:
- `GET /UserItems/Resume`: resumable movies/episodes
- `GET /Shows/NextUp`: next episodes; `EnableResumable` defaults to true

A single episode can legitimately occur on both shelves. Cross-shelf overlap alone does **not** prove duplicate cards, a Bridge bug, or bad Trakt history. The next step is to capture the actual *read model* for the same Jellyfin configuration/user as a client.

## Included tool

`scripts/audit-jellyfin-shelves.py` compares exact Jellyfin item ID, same SeriesId/season/episode, and verified common provider ID (IMDb/TMDB/TVDB/Trakt). It **never matches by title**. Raw Jellyfin IDs are SHA256-truncated in terminal output; names, credentials, full JSON, user UUID, and persona are never printed. The script never calls Trakt, never sends POST/PUT/DELETE, and never opens AIO/Bridge SQLite.

It reports:
- counts for Resume and NextUp;
- same-shelf identity collision candidates;
- cross-shelf overlaps and exact-ID count;
- a bounded listing with public provider IDs and hashed item IDs;
- a conservative classification.

### Mode 1: offline from existing authorized Jellyfin JSON captures (strictly no HTTP)

```bash
python3 scripts/audit-jellyfin-shelves.py \
  --resume-json /path/to/resume.json \
  --nextup-json /path/to/nextup.json
```

Only use trusted locally stored Jellyfin `Items` responses from the same authenticated user and a similar timestamp. JSON files may contain private history and must **not** be published or pasted into GitHub issues.

### Mode 2: explicit local HTTP GET (operational probe, not a frozen snapshot)

**Important:** AIOStreams' Jellyfin GET handlers call `refreshWatchState`. The audit program performs only two GETs, but **AIOStreams can schedule its normal pull/refresh and mutate its own state indirectly**. Live mode is not a guarantee of zero application writes. It is opt-in and must not be repeated rapidly. It never touches Trakt directly.

First use `docker port aiostreams` on the host and identify an existing loopback-reachable published HTTP port. Do not modify Compose, nginx, port forwarding or run new containers. Plain Jellyfin server mount is `http://127.0.0.1:<PORT>/jellyfin`. If no loopback endpoint exists, use offline mode; do not expose AIOStreams to the Internet for this audit.

From AIOStreams **Save & Install → Jellyfin apps → Connect → API keys**, create a temporary audit key for the same configuration as the affected client. A Jellyfin API key is **administrator-level** and cannot impersonate a user with PIN; if the affected user's scope differs, this audit alone cannot settle the issue. Revoke the key after use. Do not post the key anywhere.

Store the temporary key safely (avoid command-line token arguments or shell history):

```bash
umask 077
read -r -s -p 'Temporary AIO Jellyfin API key: ' AIO_KEY; echo
printf '%s' "$AIO_KEY" > "$HOME/.aio-rc7-audit-token"
unset AIO_KEY
chmod 600 "$HOME/.aio-rc7-audit-token"

python3 scripts/audit-jellyfin-shelves.py \
  --live \
  --base-url http://127.0.0.1:<PORT>/jellyfin \
  --token-file "$HOME/.aio-rc7-audit-token" \
  --limit 20

rm -f "$HOME/.aio-rc7-audit-token"
```

Replace `<PORT>` with the actual local published port. The script fails closed on non-loopback URLs, HTTPS/public hosts, embedded credentials, redirects, world-readable token files, invalid JSON and oversize responses. No proxy is used. Do not paste API response bodies or auth headers into issues.

## Interpretation

| Finding | Meaning | Next step |
| --- | --- | --- |
| Same-shelf exact duplicate | Two item rows with same Jellyfin ID in one endpoint | Capture a redacted fixture and investigate AIO read model |
| Same-shelf different IDs but same verified episode/provider | Possible identity projection bug | Compare provider ID crosswalk / scope before touching state |
| Only cross-shelf overlap | Normal, potentially one episode in both Resume and NextUp | Check client aggregation and which shelf generated each visible card |
| Neither endpoint duplicates | Not proof that UI is correct | Check native Trakt/client cache, different persona, other endpoints, paging |

A single capture is limited to `Limit<=50`; a result outside this page is inconclusive. The fixture `tt44051354` / `tt44094505` is a known crosswalk **only for the documented show**. Do not infer equivalence from text, or globally remap IMDb IDs.

## Non-goals and production protection

- Keep RC6 `PULL_IDENTITY_MODE=trakt`, `CANONICAL_ON_DEMAND_VERIFY=false`, 90% completion and current immutable digest.
- No AIO SQLite rewrite, import/replay, Trakt history cleanup, blanket native-Trakt disable, cache purge, or service restart.
- Do not change AIOMetadata's tracker read/write behavior until its actual contribution to the client response has been demonstrated.
- If both API shelves are clean but cards remain duplicated, a **single redacted client screenshot or network capture of card source/ID** is still required before proposing any behavioral patch.

Issue: #82. Phase A PR: #81.
