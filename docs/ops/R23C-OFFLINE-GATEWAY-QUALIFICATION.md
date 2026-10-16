# Trakt Bridge v1.4 DRAFT — R23C offline identity and latency qualification

**Operator boundary (2026-10-15):** Source-only GitHub development and synthetic automated CI. No HomeDocker SSH operations, no actual AIO credential, no production watch-state/SQLite/Docker/nginx edits, no Gateway client cutover. The Draft PR #94 stays unmerged and v1.4 Stable is NOT ready.

## Host evidence — why the tests matter

Verified RC6 provenance: **15 historical show identity mappings and one verified canonical/alternate IMDb alias group**. It is scrobble-derived *identity evidence*, not live duplicate proof.

Actual HomeDocker authenticated AIO source audits observed:
- **NextUp:** First/capped 50 series all had metadata/IMDb. The verified canonical appeared **once**, known alternate **zero**. Direct GET duration **8013ms** and **10507ms** in two runs.
- **Resume:** Two independent username-authenticated 40-card reads covering offsets 0/20 and 40/60. In the first: **31 Episode/9 Movie**. In the second: **25 Episode/15 Movie**. All **80 returned rows across two different runs** had active playback positions, with **no canonical or alias of this known pair** in either sample. These two runs were not one atomic 80-item snapshot; no full history census is claimed. Resume page durations were 3018/2118ms and 6016/5900ms.

AIO's public library route source caps `/Shows/NextUp` Limit at 50 and does not implement StartIndex pagination in that handler. This is verified against current public source, NOT pinned to the HomeDocker floating latest Docker image digest. For Resume, StartIndex is supported, but reported TotalRecordCount may be a fetched-prefix count, NOT the global library total.

**Decision:** Stop scanning production history; do not manufacture duplicates or delete/merge active Resume rows.

## R23C-1 — Synthetic, exact-identity normalization

Automated suites:
- `test/bridge-jellyfin-nextup-filter.test.mjs` (pure projection)
- `test/jellyfin-gateway-http.test.mjs` (HTTP semantics/headers/user scope/metrics)
- `test/v140-gateway-offline-qualification.test.mjs` (explicit release scenarios)

Required behavior and tests:
1. Only the **same verified Trakt show's two distinct IMDb spellings** may normalize; never fuzzy title matching, episode/provider ID substitution, conflicting graphs, cross-user cards or unverified IDs.
2. Exactly one active playback position among verified twins selects the active card, preserving its original AIO `Id` and `UserData`; the input response and AIO state remain unchanged.
3. **Both twins active**: preserve both as-is even if LastPlayedDate differs; no arbitrary winner. Two equal spellings, 3+ candidates, missing winner evidence or metadata errors also fail open.
4. **Cold NextUp containing 50 unrelated series**: preserve all 50 cards exactly, even though Gateway may issue **50 series-detail GETs + /Users/Me + NextUp** for the first cache miss. This is important *performance evidence of potential request amplification*, **not** optimization completed.
5. Explicit wrong AIO user, no graph, synthetic metadata 429/503 or timeout: return unchanged source NextUp, never an invented empty shelf or partially filtered list.
6. No routes other than exact opt-in `GET /Shows/NextUp` and optional raw Resume GETs, no playback/watched POSTs, no Trakt dependency.

## R23C-2 — Timeout contract and cost

- Draft `JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS` defaults to **8000** and rejects values below **8000** or above **20000**. Strict integer parsing; invalid settings fail closed at startup.
- The optional extension applies only to the initial upstream `GET /Shows/NextUp` fetch and its abort signal. Follow-up `GET /Users/Me`, each `GET /Items/{id}`, and optional raw Resume remain at **8000ms** each.
- A deterministic *virtual duration* test matrix asserts that first-GET simulated durations **8000, 10507, 15000, 20000 and 20001ms** produce the modeled HTTP 200 or safe 502 according to the configured 8/15/20-second budget. This is a logical failure-contract test using injected fetch + abort-budget observation, **not a wall-clock wait**, real transport abort benchmark, AIO throughput, p95, or client SLA claim.
- New Gateway metrics in `gatewayMetricsSnapshot()` are aggregate-only: NextUp response samples, upstream response samples, summed wall milliseconds and max observed milliseconds. Only per-listener numeric aggregates, no usernames, user IDs, media IDs, URLs, auth tokens, watch history or position ticks. These are **not** exposed as an unauthenticated public HTTP endpoint.
- **Hard remaining performance risks:** the AIO NextUp source alone was seen taking ~8–10.5 seconds; series-detail metadata may require up to 50 additional requests in batches; different per-request 8s deadlines are NOT a single total Gateway budget; client and nginx timeouts remain unknown; fetch signal/body-streaming/502 behavior needs an actual canary test. A 15s option is **not yet an approved production setting**.

## Acceptance matrix and remaining gates

| Gate | Offline evidence | HomeDocker state |
| --- | --- | --- |
| Verified synthetic alias, exact active winner | Pure/HTTP test PASS | No real live duplicate observed |
| Competing active Resume preserved | Pure/HTTP regression PASS | 80 returned active rows across two independent audits |
| Unrelated NextUp stable / absent alias | 50-card synthetic unchanged | First/capped 50 AIO series observed; no verified pair |
| Metadata 429/503/timeout and wrong user | Synthetic fail-open tests | Not yet probed through real authenticated Gateway |
| Opt-in 8–20s first-GET deadline | Config validation + virtual duration tests | Default 8s; no host deploy |
| Safe aggregate latency counters | Draft Gateway local test | Not installed on production or original R23B image |
| End-to-end AIO ↔ Gateway authenticated relay | Not passed | **BLOCKED**: no selected same-user relay/network authorization |
| Real-world p95, concurrency, client/nginx deadlines | Not measured | **BLOCKED** |
| Staged NextUp-only client route & rollback | Not performed | **BLOCKED** |

**Next decision (R23D only after explicitly reviewed plan):** isolated, minimal, authenticated user-scoped Gateway canary with allowlisted GET-only AIO relay and rollback. Do not attach `media_core_net` or mount RC6 SQLite/Trakt credentials into a disposable canary. Keep original canary running and stopped R23A rollback intact unless a separate reviewed host command authorizes change. Real alias dedup can only be claimed when a duplicate is observed in a genuine client/source snapshot; otherwise prove unchanged passthrough, correct user scope and safe latency/error behavior.
