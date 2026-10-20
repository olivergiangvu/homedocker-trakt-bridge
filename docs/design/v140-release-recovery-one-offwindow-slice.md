# Release Recovery — one off-window real NextUp episode

**Release posture:** RC3 deep research stops at N6. NO merge of research PR #141
(90 commits), no production change, no custom client URL, no routing or Filter
enablement. RC2 production SHA 65ced8ab827c79f03bff7a7668c243338e62f317
remains unchanged. Draft release PR #142 is a **one-shot shadow proof**.

## HomeDocker ground truth

- One mapped AIO watch-sink has 906 raw history-series keys, 861 raw
  recentSeries candidate keys, 661 after the 200-candidate NextUp horizon.
- The source uses raw base_id equal to series_key suffix for all 861 examined
  anchors. 857 have canonical numeric/packed-a1-compatible syntax, four do not.
- 118 syntactic cross-namespace match hints are NOT verified equivalent shows.
- One verified alias show's eleven watched episode coordinates match the
  real twelve-episode per-series catalog.
- AIO /Shows/NextUp globally ignores StartIndex, caps 50 cards and considers
  200 candidates; per-series NextUp and per-series Episode Catalog are separate.

## First release approach was tried and FAILED safely

At SHA 8f307cc780536b971d0798943f14445e91ba2cef on HomeDocker:
R5SLICE_RESULT=STOP;
R5SLICE_FAILURE_PHASE=SEARCH;
R5SLICE_FAILURE_REASON=SEARCH_NO_SERVER_ISSUED_SERIES_ID.
Search/Hints did not establish an exact same-IMDb server-issued SeriesId for
ONE cached source-title sample. This result does not prove there is no usable
SeriesId or NextUp episode. Filter remained OFF and production changes=0.

## Release Recovery v2: one deterministic a1 protocol address, server-validated

The **pinned** AIO package core/src/jellyfin/ids.ts implements stateless
packed-a1 content IDs. For a canonical IMDb series source descriptor, its
pure deterministic SERIES codec is:
- 16 bytes / 32 lowercase hex digits.
- byte 0 = a1; byte 1 = 0x21 (series kind 2, imdb ID type 1)
- byte 2 = media code (series 2, anime 3, tv 4)
- bytes 3..8 = 6-byte big-endian numeric IMDb base
- bytes 9..12 = ff ff ff ff (no season or episode)
- bytes 13..15 = 00 00 00.

The codec is NOT random guessing and does not insert into jellyfin_id_map
or mutate any API state. BUT an internally constructed address remains
**untrusted** until the local authenticated AIO server confirms a real Series
and exact ProviderIds.Imdb match. An ID made by the client is not itself
proof of server issuance. This relaxes the earlier no-ID-computation research
guard, ONLY for an exact, source-proven, server-validated stateless codec.
It is never sent to a client, printed or stored.

One pinned read-only operator script:
scripts/shadow-v140-offwindow-nextup-slice-homedocker.py

1. Verify mapped Jellyfin /Users/Me, original RC2 Gateway alive and Filter OFF.
2. Reuse already installed root-only RC3-N2 read-only source audit to verify
   one exact AIO watch-sink (uuid+persona), Bridge graph, two identical complete
   raw history/anchor snapshots, no writes.
3. Select at most THREE canonical IMDb raw candidate series strictly beyond
   source rank 200, with no syntactic match_key redirect and supported media.
4. Compute the exact stateless a1 series address privately in memory. GET
   /Items/{id}, ignoring an expected 404 and requiring Type=Series, exact
   returned Id and ProviderIds.Imdb equal to the source. NO next request for
   an unverified address.
5. For a verified show GET /Shows/NextUp?SeriesId={id}&Limit=1. If empty,
   try the next bounded source series; if returned, require an unplayed
   Episode with exact SeriesId. GET /Shows/{id}/Episodes?StartIndex=0&Limit=200
   optionally scoping to the returned ParentIndexNumber=Season, require the
   complete returned <=200 rows and episode identity. GET /Items/{episodeId}
   once to confirm type/ID/parent.
6. Repeat the source snapshot gate, confirm RC2 Filter OFF, output
   aggregate-only PASS/STOP. No names, IMDb/Series/Episode IDs, history
   timestamps, credentials or raw JSON ever leave the script.

Hard caps: 3 off-window source candidates, 10 authenticated local GETs total,
18s per call, 125s global, 1MiB HTTP body per call; localhost fixed AIO,
proxies and redirects disabled. All SQLite operations use mode=ro,
PRAGMA query_only via the previously validated root-only N2 helper.
Only direct /Items, per-series NextUp, catalog and /Users/Me GETs are used.
No playback or Trakt calls. No docker restarts or API writes.

## GO/STOP gates

- Both Push and PR CI passing: only an **offline software quality gate**.
- HomeDocker PASS with one episode actually present: **proof of a single
  off-window backend-resolved episode**, not a complete NextUp, global paging
  or playable stream.
- STOP (wrong provider, empty NextUp, slow metadata, oversized season):
  record the safe reason and choose **one bounded shipping strategy**, not
  another RC3-N7 style general-purpose audit campaign. A failed one-series
  test must never be advertised as backend-wide impossibility.
- A later client-facing single-client opt-in canary with RC2 fallback is a
  different, explicit authorization gate. Do not enable Gateway Filter,
  change nginx/Odin, restart containers, merge PR #142 or rewrite watched
  state based solely on a shadow PASS.
