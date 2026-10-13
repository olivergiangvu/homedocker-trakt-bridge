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

**Important:** AIOStreams' Jellyfin GET handlers call `refreshWatchState`. The audit program performs only two GETs, but **AIOStreams can schedule its normal pull/refresh and mutate its own state indirectly**. Live mode is not a guarantee of zero application writes. It is opt-in and must not be repeated rapidly. Optional Phase B6 adds one GET with the opposite NextUp filter. It never touches Trakt directly.

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

## Phase B6: Test the read-filter hypothesis in one controlled probe

AIOStreams upstream implements `GET /Shows/NextUp?EnableResumable=false`: it filters out resumable episodes in the NextUp response (not necessarily replacing them with the subsequent episode). Default is `true`. The first HomeDocker operator capture returned 20 Resume and 20 NextUp items, zero same-shelf identity matches, but **three cross-shelf exact Jellyfin ID overlaps**: S03E05, S01E10, S02E01. This is consistent with AIO's API design; it is **not yet proof of duplicate rendered client cards**.

To test whether this overlap is created by the default resumable behavior, run **once** with the same authorized Jellyfin scope and temporary key:

```bash
python3 scripts/audit-jellyfin-shelves.py \
  --live \
  --base-url http://127.0.0.1:<PORT>/jellyfin \
  --token-file "$HOME/.aio-rc7-audit-token" \
  --limit 20 \
  --compare-nextup-modes
```

This issues exactly **three** loopback GETs: Resume, NextUp(`EnableResumable=true`), NextUp(`EnableResumable=false`). It prints the original two-shelf report plus a `NEXTUP_MODE_COMPARISON` block. AIO normal GET-triggered refresh may still run, and sequential API responses are not atomic snapshots. Delete the key file locally and revoke temporary API key afterwards.

Interpret `FLAG_REMOVED_OBSERVED_CROSS_SHELF_OVERLAPS=true` as **the overlap disappeared from the observed API response when the flag was false**. It does not show which API the client actually calls, nor prove the root cause of visible double cards. Neither a Bridge patch nor a blanket nginx query rewrite is warranted on that evidence alone. A specific client request/response trace is the gate before any behavioral change.

## Interpretation

| Finding | Meaning | Next step |
| --- | --- | --- |
| Same-shelf exact duplicate | Two item rows with same Jellyfin ID in one endpoint | Capture a redacted fixture and investigate AIO read model |
| Same-shelf different IDs but same verified episode/provider | Possible identity projection bug | Compare provider ID crosswalk / scope before touching state |
| Only cross-shelf overlap | Normal, potentially one episode in both Resume and NextUp | Check client aggregation and which shelf generated each visible card |
| Neither endpoint duplicates | Not proof that UI is correct | Check native Trakt/client cache, different persona, other endpoints, paging |

A single capture is limited to `Limit<=50`; a result outside this page is inconclusive. The fixture `tt44051354` / `tt44094505` is a known crosswalk **only for the documented show**. Do not infer equivalence from text, or globally remap IMDb IDs.

## Phase B7: Isolate the single recurring show

**Scope correction from operator:** the visible *double Continue Watching cards* affect only **Ok! Let's Get Divorced** across clients. The 3 cross-shelf exact-ID overlaps found in Phase B5 and their removal under `EnableResumable=false` in Phase B6 are **normal generic API behavior**, not evidence that all 3 shows have duplicated client cards. Do not disable resumables globally, and do not rewrite NextUp requests in nginx.

The permanent single-title fixture from `docs/DEVELOPMENT.md` is:

| Role | ID |
| --- | --- |
| Trakt IMDb | `tt44051354` |
| AIOStreams IMDb | `tt44094505` |
| Trakt show | `263102` |
| TMDB | `276470` |
| TVDB | `480791` |

To trace the two spellings **within only the already-fetched API shelf responses**, add `--trace-known-show` to the Phase B6 command:

```bash
python3 scripts/audit-jellyfin-shelves.py \
  --live \
  --base-url http://127.0.0.1:3240/jellyfin \
  --token-file "$HOME/.aio-rc7-audit-token" \
  --limit 50 \
  --compare-nextup-modes \
  --trace-known-show
```

The script decodes **only** the public `a1` packed Jellyfin IDs directly according to AIOStreams `packages/core/src/jellyfin/ids.ts` and checks already-present `ProviderIds` for these exact providers. A `b2` hashed ID is opaque and must never be interpreted as an IMDb alias. Each matching row prints a hashed item ID and episode number; no raw item IDs, private names, or credentials. If matching rows were excluded by a 20-row page, the larger 50-row sample might locate them, but not all history is guaranteed to be represented.

`SHOW_DUAL_IMDB_SAME_EP_CANDIDATES` counts instances where two *different* packed/provider-backed Jellyfin IDs for the **same SxxExx** show both documented IMDb aliases across captured shelves. It does not prove either card is displayed; when zero, native Trakt IDs, opaque AIO IDs or uncaptured catalog entries remain possibilities. This is a **bounded evidence collector**, not a deduplication feature. In particular, do not change `PULL_IDENTITY_MODE=trakt`.

## Phase B7 result: production NextUp emits two episode cards for one show

Operator's scoped 50-row Jellyfin API capture:

```text
RESUME_ROWS=50
NEXTUP_ROWS=50
RESUME_SAME_SHELF_MATCHES=0
NEXTUP_SAME_SHELF_MATCHES=0
CROSS_SHELF_MATCHES=8
SHOW_ROW SHELF=RESUME KIND=episode EP=S01E10 REF=TRAKT_IMDB
SHOW_ROW SHELF=NEXTUP_TRUE KIND=episode EP=S01E10 REF=TRAKT_IMDB
SHOW_ROW SHELF=NEXTUP_TRUE KIND=episode EP=S01E09 REF=AIO_IMDB
SHOW_DUAL_IMDB_SAME_EP_CANDIDATES=0
SHOW_TRACE_CLASS=DUAL_IMDB_IN_CAPTURE
```

The **specific double-card problem** is the NextUp list containing **S01E10 keyed to Trakt IMDb and S01E09 keyed to AIO IMDb**, not generic Resume/NextUp shelf overlap. Two distinct episodes of the same series under two identity spellings are not detected by an *exact episode* collision test. Updated auditor now prints `SHOW_NEXTUP_DUAL_IMDB_DISTINCT_ITEMS=true` on this pattern.

**Source-grounded probable producing layer:** AIOStreams `packages/core/src/db/repositories/watch-state.ts` `recentSeries()` aggregates rows by `series_key` and has a limited canonical `match_key`-based dedupe. In `packages/server/src/routes/jellyfin/library.ts` `/Shows/NextUp`, a final `shown` set dedupes by built `SeriesId`, which can differ for two packed Jellyfin IDs with distinct IMDb bases. In `packages/server/src/routes/jellyfin/items.ts` `watchingPosition()`, absent/incorrect mappings to watched state can make S01E09 look unplayed even when a Trakt-spelled S01E09 is watched. Exact source rows / series keys are not established until Phase B8; do **not** assert the cause as proven.

## Phase B8: Query provenance under the exact Bridge-connected AIO scope

`scripts/audit-known-show-nextup-origin.py` uses **only** a local SQLite `mode=ro` connection and `PRAGMA query_only=ON`. It requires exactly one active Bridge sink `homedocker-trakt-bridge` / `e3fe3b0`, reads only that sink's `uuid/persona` (prints a hash) and searches `item_key/match_key/base_id/series_key/video_id` for the documented immutable fixture IDs only. It does not read tokens or make HTTP requests.

```bash
sudo python3 scripts/audit-known-show-nextup-origin.py
```

Expected diagnostic fields: `SERIES_KEY_GROUP_COUNT`, `SERIES_GROUP KEY=... ROWS=... ANCHOR_ITEM=... ANCHOR_EP=...`, scoped `ROW KEY=... MATCH=... BASE=... SERIES=... VIDEO=... EP=... PLAYED=...`. It will distinguish two separate source series-key groups, alternate TMDB/TVDB source mapping, or a case where the second rendered NextUp identity arises from metadata projection rather than an additional local `item_key`.

No repair is attempted. Do not change `EnableResumable` globally, remove S01E09 watched history, or switch Bridge pull to AIO IMDb. A fix must be scoped to verified Trakt/TMDB/TVDB show anchors, dedup the **show-level NextUp candidates**, preserve the furthest watched/resume point, and retain unrelated shows / rewatches.

## Non-goals and production protection

- Keep RC6 `PULL_IDENTITY_MODE=trakt`, `CANONICAL_ON_DEMAND_VERIFY=false`, 90% completion and current immutable digest.
- No AIO SQLite rewrite, import/replay, Trakt history cleanup, blanket native-Trakt disable, cache purge, or service restart.
- Do not change AIOMetadata's tracker read/write behavior until its actual contribution to the client response has been demonstrated.
- If both API shelves are clean but cards remain duplicated, a **single redacted client screenshot or network capture of card source/ID** is still required before proposing any behavioral patch.

Issue: #82. Phase A PR: #81.
