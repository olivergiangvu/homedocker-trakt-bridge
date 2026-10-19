# RC3 research — NextUp pagination-aware shadow qualification

**Status: Draft; code+synthetic tests only. No production activation.**
**Branch:** feat/v140-rc3-nextup-pagination-shadow-20261019.
**Parent:** RC2 Draft PR #139, with Safari UI fixes already on HomeDocker. RC2 image and V140_STABLE_RELEASE_HOLD remain in place.

## Real HomeDocker evidence (19 Oct 2026)

- R3 authenticated browser ON -> OFF is persisted and verified. End state Filter **OFF**.
- R4-A, mapped AIO Jellyfin session: GET /Shows/NextUp?StartIndex=0&Limit=20 returned 20/20; local internal Gateway OFF response had byte and JSON parity with the upstream.
- R4-B, same user: GET /Shows/NextUp?StartIndex=0&Limit=50 returned 50/50, TotalRecordCount=50. Both full pages are deliberately ineligible for RC2 filtering.
- These are source observations; they do not prove duplicates exist on those pages or prove a true global total. There is **zero live positive NextUp alias-suppression proof** and no qualified Odin ingress.
- R4-C live shadow audit, mapped-user token and Filter OFF: STOP:STARTINDEX_NOT_HONORED. Source inspected: the RC3-P1 script required `page.get("StartIndex") == offset`; a **missing StartIndex field** also triggers that result. **Do not claim the upstream ignores offset yet.** The follow-up diagnostic separates absent echo, wrong echo, repeated page content, overlap and snapshot drift without exposing raw episode IDs.


## Critical distinction

AIO may use a **fetched-prefix** TotalRecordCount rather than a true global total. A full Limit=20 or Limit=50 page is therefore insufficient to prove completeness. Raising Limit alone is not a fix. A single lookahead request does not prove the second page has consistent indexes or that the list will remain stable across client requests.

src/nextup-pagination-shadow.mjs contains a **research-only auditor**, imported by tests, **not** by src/jellyfin-gateway.mjs or src/index.mjs:

1. Fetch up to four consecutive pages at a fixed StartIndex stride, Limit 2..50 and at most 100 aggregate episode cards. At most 8 total requests including the replay pass, with a deadline <=20 seconds (12 seconds by default), each fetch <=8 seconds.
2. Require expected StartIndex echoes, valid Episode/Id/SeriesId, bounded JSON, nonoverlapping episode IDs and coherent Count. Do not treat TotalRecordCount alone as proof; allow ONLY a stable global total or coherent prefix totals.
3. Require a **nonempty short final page** inside the budget. Empty tail after a full page is inconclusive; full cap is unqualified, not a rejection of AIO.
4. Re-fetch every observed page and compare its complete JSON serialization for a stable second sweep. A change in episode, order, progress, counts or metadata invalidates the candidate.
5. Optionally run the existing **verified Trakt-to-IMDb** pure normalization in memory to report only a candidate removal count. Two active resumes, unverified IDs, metadata failure or ambiguous groups preserve both items.

**IMPORTANT:** The second pass provides observational stability only, **not** a snapshot transaction, a frozen cursor, or a proof that AIO cannot change between two independent client requests. The module must never be used as a drop-in live pagination transformer. Its output includes the sampled Items in memory for private analysis; never log them or send them to CI.

## Test matrix and expected fences

| Source condition | RC3 shadow result |
|---|---|
| 20/20, 20/20, 7/20 and identical second sweep | Candidate observational window |
| 50/50, 10/50 with consistent static/global counts | Candidate observational window |
| 20/20 or 50/50 continually full until cap | FULL_PAGE_BUDGET_EXHAUSTED or ITEM_BUDGET |
| Ignores StartIndex, missing/invalid counts | Reject with safe generic reason |
| Overlaps episode IDs across pages | OVERLAPPING_PAGES |
| Episode/progress/order/count drifts on second sweep | SNAPSHOT_CHANGED |
| Missing auth, unavailable upstream or deadline | Reject, no payload/secret in diagnostics |
| Two simultaneous competing resumes | No candidate removal |
| Verified alias + decisive resume evidence | Shadow-only numeric candidate removal; source untouched |

All tests use synthetic pages and synthetic tokens/IDs. They do not access Trakt or AIO. Run npm test on Node >=24 and npm run check.

## Scope: RC3-P0 vs RC3-P1 vs R5

**RC3-P0 (this PR):** Independent read-only research module, tests and design; no runtime import, no image promotion, no new endpoints or env vars. Existing Docker Compose, nginx, Trakt writer, media_cache, AIO watch_state and clients are untouched.

**RC3-P1 (tool included; live execution requires separate operator step):** scripts/audit-v140-rc3-nextup-shadow-homedocker.py is an optional host-side authenticated, bounded **GET-only** read of the current mapped AIO user. It checks the Bridge Gateway Filter is OFF before and after reading; enforces Users/Me equality; reads an already-provisioned root-only session token from /run; requests up to 4 pages at Limit=20 with <=8 NextUp calls (and one Users/Me); emits only numeric counts and generic reasons. It does not try a global IMDb projection, change the Filter, write data or enter client routing. If the temporary user token has been removed, STOP without rebuilding it automatically. Executing the script is NOT part of GitHub CI and is not authorized by merging the PR. Revoke the temporary admin API key and unused test sessions afterwards.

**RC3-P1 diagnostic:** `scripts/diagnose-v140-rc3-nextup-offset-homedocker.py` performs exactly three 20-item NextUp GETs (offsets 0, 20, 0 replay) plus one authenticated Users/Me GET. It requires the same mode-600 root-only user token, Bridge listener ACTIVE and Filter OFF both before and after. It prints only presence/match of StartIndex, item counts, overlap count and page-replay stability. Non-overlap or an exact offset echo is **observational**, not transactionally stable pagination. A missing echo remains an unqualified contract until separately reviewed. The original RC3-P1 live audit has been patched to classify absent StartIndex as STARTINDEX_FIELD_ABSENT, distinct from genuine STARTINDEX_NOT_HONORED. No production change.

**RC3-P2 (new design review, NOT implemented):** True client-facing pagination consistency would require a coherent snapshot/cursor, scope to user+query, stable page slicing/continuation, explicit expiry/invalidation, upper bounds and failure-recovery semantics. Without that, independently rewritten StartIndex=0&Limit=20 and StartIndex=20&Limit=20 could omit or duplicate items as earlier rows are suppressed. Source-side pagination itself may ignore offsets. Never count one short-page success as authorization to move Odin.

**R5** remains HOLD. No public Gateway port 7101, no blanket Jellyfin reverse proxy and no switch of any playback client. If a later pilot regresses, UI Filter OFF and rollback the separately approved *exact-path ingress* first.

## Operational release status

RC3 source/tests only. RC2 remains active on HomeDocker with one Bridge/one Trakt writer and Gateway UI Filter OFF. The RC2 Safari and Referrer-Policy fixes are separately validated; they are **not** a dependency requiring another HomeDocker cutover for RC3-P0.
