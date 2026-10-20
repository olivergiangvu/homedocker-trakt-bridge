# Release Recovery — One real off-window NextUp vertical slice

**Release decision:** RC3-N6 research is CLOSED. Do not continue N7/N8, merge
research PR #141 wholesale or turn on production Gateway filtering. Keep
production RC2 SHA `65ced8ab827c79f03bff7a7668c243338e62f317`,
Odin/nginx, AIO/Trakt watched history, and R5 HOLD unchanged.

## What is already known (live HomeDocker)

- One exact AIO mapped-user scope: 906 episode-history source keys,
  861 raw watched/resume candidate source keys, 661 beyond upstream
  per-request NextUp candidate cap 200.
- 857/861 candidate raw source descriptors have theoretical packed-a1
  syntax, and all 861 inspected anchor base IDs match source series keys.
- 118 directed cross-namespace match_key hints, not validated duplicates.
- One canonical show's exact 11 history episode positions bind to 12
  actual AIO catalog episodes. The observed duplicate alias is not co-visible
  in the current 50-card NextUp window.
- AIO /Shows/NextUp ignores global StartIndex and returns up to 50 cards;
  **per-series** /Shows/NextUp?SeriesId and /Shows/{SeriesId}/Episodes
  are independent endpoints with usable source-side contracts.
- None of the above is proof that all 661 beyond-200 candidates have a
  next episode, that streams play, or that a global total can be advertised.

## One vertical slice, not an inventory audit

Script: `scripts/shadow-v140-offwindow-nextup-slice-homedocker.py`.
**This is intentionally a separate tiny PR from RC2**, not stacked atop
the 90-commit RC3 research PR.

From the root-only, previously installed and tested RC3-N2 source helper:
1. Verify mapped AIO user and production Gateway Filter OFF.
2. Verify exact AIO sink, Bridge graph, two consistent complete SQLite
   source/anchor snapshots. Choose one raw candidate at rank >=201 with
   canonical IMDb source key and **no source anchor match_key**.
3. Inspect at most **16** matching, same-scope watch-state row snapshots,
   using a single read-only SELECT. Select a cached series title from a
   snapshot, internally only. No titles or IMDb IDs leave the audit.
4. Exactly one `Search/Hints` GET with that title. This is a SERVER
   search, not an algorithm inventing SeriesIds. Examine at most three
   server-returned 32-hex Series IDs with direct GET /Items/{id}; require
   exact ProviderIds.Imdb = selected source IMDb and matching returned ID.
   **Title similarity alone never establishes identity.**
5. GET per-series /Shows/NextUp?SeriesId=**server-returned** ID;
   require a single unplayed Episode with an ID returned by AIO, then
   check one complete <=100-episode /Shows/{SeriesId}/Episodes page and
   GET /Items/{EpisodeId} identity. Print count-only success/STOP codes.
6. Recheck the exact source/title sample and Gateway Filter OFF.
   Never generate an a1/b2 ID, write UserData or mutate Jellyfin client
   routing.

### Bounded resources and known failure conditions

Maximum **8 authenticated local GETs**, each <=18s, overall <=135s;
1 MiB response limit, redirects/proxy disabled; no foreign hosts;
fixed 16 source sample rows; a single <=100 episode catalog page.
If no cached series title, search yields no matching server-issued ID,
search response is slow, no NextUp, or catalog exceeds 100 episodes,
the output **STOPs**. It does not search more titles, create an ID,
interpret a source key as a Jellyfin SeriesId, or silently fabricate a
next episode.

The root-only RC3-N2 helper is a verified **operator audit dependency**,
installed in /run/homedocker-r4 from the previously pinned SHA
`4cdde72bbd10605911fa95e85faaa5f3895de460`. Neither the
running Gateway nor this RC2-based release branch imports it. A
future production module must eliminate this dependency and have its own
first-party bounded read adapter, separately reviewed.

## Definition of done / next decision

- Code/test CI PASS means **shadow vertical slice prepared**, not
  evidence of a valid off-window episode.
- Operator STOP with `NO_VALID_OFFWINDOW_TITLE` or
  `SEARCH_NO_SERVER_ISSUED_SERIES_ID`: title search cannot bootstrap
  a server-issued ID from the selected small sample. Choose a
  **different bounded backend issuance strategy**, not an unbounded
  metadata probe and not another generic identity audit.
- Operator PASS means **exactly one real off-window NextUp episode** was
  returned from per-series AIO and found in a same-user catalog. It
  establishes an actionable vertical slice, **not** global pagination or
  verified stream playback.
- Client Canary is a **separate explicit authorization gate**: opt-in
  exactly one device and a private URI, a bounded feature flag, baseline
  comparison, zero watched writes, fail-open RC2 passthrough and
  rollback metrics. Only after PASS and owner approval consider changing
  an HTTP route. The RC2 production Filter remains OFF now.

Do not claim a release based solely on green CI, pinned syntax or this
unrouted shadow test.
