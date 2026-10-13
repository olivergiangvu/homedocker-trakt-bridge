# RC7 Phase C — HomeDocker-side resolution, **no changes to AIOStreams code**

## Scope and decision

Owner requirement: **do not fork, patch or rebuild AIOStreams**. Issue #82 established a single bad show, *Ok! Let's Get Divorced*, under two IMDb identities. Upstream-code patch draft PR #84 was **closed unmerged**. RC6 remains pinned. This HomeDocker repo provides a narrowly scoped **operator-side tool** using AIOStreams' existing, documented-by-source Jellyfin API behavior.

Verified same AIO scope (`b140f061a0b9`), 18 episode rows:

- `s|tt44051354`: 9 imported watched episodes and local S01E10 resume at 346626 ms.
- `s|tt44094505`: 8 local watched episodes, keys `e|tmdb:276470:1:1` to `...:8`; no S01E09 watched state.
- The NextUp HTTP API returns Trakt S01E10 plus stale AIO S01E09, two different Jellyfin SeriesIds.

## Supported AIO behavior — why owner-side intervention can work

At **Viren070/AIOStreams**, `packages/server/src/routes/jellyfin/playstate.ts` routes:

```text
POST /UserItems/:itemId/Rating?Likes=false
  -> rate(ctx, series, false)
  -> setDropped(ctx, series, true)

DELETE /UserItems/:itemId/Rating
  -> rate(ctx, series, null)
  -> setDropped(ctx, series, false)
```

AIOStreams' `WatchStateRepository.recentSeries()` excludes a series with `dropped_at` newer than its last `sort_at`. A **series-level drop** therefore hides the obsolete AIO IMDb NextUp branch without deleting watched episodes. Playback `start` on the dropped series may undrop it (designed AIO behavior); such a later reappearance is not fixed by this one-time action.

Our Bridge manifest's advertised push events are only start/pause/stop/played/unplayed/watchlisted/unwatchlisted, **not** `dropped`; the owner-side action is not a Trakt history delete and is not a Bridge `PULL_IDENTITY_MODE` change.

**Operational nuance:** This is a *supported API call* that writes **one AIO-owned series preference** (`dropped`). It does not modify AIO source or image, and never directly writes SQLite. Calling this "no change to AIO data" would be incorrect: one series preference is intentionally changed. The existing 18 episode rows remain unchanged, and rollback is supported.

## Script safeguards

`scripts/repair-stale-aio-nextup.py`:
- defaults to a **dry run** (two bounded GETs); prints a plan, never POST/DELETE;
- opens existing local SQLite using `mode=ro` and `PRAGMA query_only=ON`;
- requires exactly one Bridge-connected AIO watch sink, the exact historical 18-row fixture, and no previous series-level drop/rating;
- requires authenticated Jellyfin `Resume S01E10` and `NextUp S01E10 + S01E09` under decoded packed SeriesIds, and ensures S01E09 is **not resumable**;
- uses loopback HTTP only (production local port `3240`); no redirects, proxies, raw API output, or Trakt call;
- `--apply --confirm-stale-imdb tt44094505` executes exactly one native AIO Jellyfin `POST .../Rating?Likes=false`, then verifies the drop row, preserved 18-episode histories and correct NextUp response;
- `--undo --confirm-stale-imdb tt44094505` uses official `DELETE .../Rating` only after verifying the drop-only preference has no numeric rating/likes. If stored state differs, **fails closed**.

Both apply/undo write **AIO preference state through its own API**. Read-only preflight can schedule normal AIO refresh via GET, but has no script-driven DB writes. AIO history/watched/resume is never removed, and no container/image/reverse-proxy changes occur.

## Operator procedure via SSH (HomeDocker only)

Create a fresh temporary **AIO Jellyfin API key** in AIOStreams `Save & Install → Jellyfin apps → Connect → API keys`. This key is administrator-level; never paste it into issues/chats or shell history. Verify it corresponds to the same AIO configuration as the active Bridge sink.

From the HomeDocker shell, fetch the script from the specific passing PR commit (or check out this feature branch), then:

```bash
(
  set -euo pipefail
  umask 077
  KEY_FILE=$(mktemp "$HOME/.aio-rc7-owner.XXXXXX")
  trap 'rm -f "$KEY_FILE"' EXIT

  IFS= read -r -s -p "Temporary AIO Jellyfin API key: " KEY
  echo
  printf '%s' "$KEY" > "$KEY_FILE"
  unset KEY

  sudo python3 "$HOME/repair-stale-aio-nextup.py" --token-file "$KEY_FILE"
  # Review DRY_RUN output. Does NOT change AIO state.

  # After confirming the preflight:
  # sudo python3 "$HOME/repair-stale-aio-nextup.py" \
  #   --token-file "$KEY_FILE" --apply \
  #   --confirm-stale-imdb tt44094505
)
```

Revoke the temporary key and save AIO configuration after use.

Expected dry-run: `AIO_CONNECTED_SCOPE_VERIFIED=true`, `AIO_HTTP_NEXTUP_DOUBLE_CONFIRMED=true`, `REPAIR_TARGET=one_stale_series_only`, `NO_CHANGES_MADE=true`.

Expected apply: `OFFICIAL_AIO_SERIES_DROP_POST_ACCEPTED=true`, `POST_DB_VERIFY=DROP_ROW_SET_EPISODE_HISTORY_UNCHANGED`, `POST_VERIFY=PASS_STALE_SERIES_ABSENT_CANONICAL_RETAINED`.

If the owner wants to roll back **only this drop** (not watch history):

```bash
sudo python3 "$HOME/repair-stale-aio-nextup.py" \
  --token-file "$KEY_FILE" --undo \
  --confirm-stale-imdb tt44094505
```

Undo is only allowed while the series preference row still meets the exact drop-only precondition. The owner must ensure the temporary key still exists at the time of rollback or create a new one.

## Follow-up / recurring prevention

Do not create an always-on repair daemon yet. If playing under old `tt44094505` causes AIO's native undrop behavior to make double cards reappear, investigate a **conditional owner-side reconciliation** with exact scope/identity evidence and explicit opt-in. Never disable `NextUp` universally, wipe the AIO 18 episode rows, rewrite Trakt history, or change `PULL_IDENTITY_MODE=trakt`.

No production operation is performed merely by merging this script. The user manually runs `--apply` when its dry-run result has been reviewed.
