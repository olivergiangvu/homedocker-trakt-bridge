# v1.4 RC2 — Gateway UI activation and safe client routing (HomeDocker)

**State: Draft/operator guide, not a HomeDocker deployment authorization.**
Main operational invariant: ONE Bridge / ONE Trakt writer / unchanged AIOStreams primary. Do not create a second Bridge against `/app/data`, publish Gateway port 7101, retag GHCR, change native nginx or move Odin without a separate explicit operational review. `V140_STABLE_RELEASE_HOLD` stays ACTIVE.

## Precisely what is being switched

1. **Jellyfin-compatible source and Gateway listener (provision once)** — `JELLYFIN_GATEWAY_ENABLED=true` with one selected source: `JELLYFIN_GATEWAY_SOURCE_KIND=aiostreams` (primary), or separately validated `remux` / `jellyfin`. The selected `JELLYFIN_GATEWAY_UPSTREAM_URL` must be the *Jellyfin API root*, for AIO normally including `/jellyfin`. Exact `JELLYFIN_GATEWAY_USER_ID` and `JELLYFIN_GATEWAY_PROFILE_ID` are operator-configured. Container listener is internal only; being "listening" does **not** prove client routing, user correctness or authenticated reachability.
2. **Identity Filter ON/OFF (daily UI control)** — under existing authenticated `/setup` or matching `/u/<profile-id>/setup`. The initial source-scoped preference is **OFF**; ON/OFF persists in Bridge SQLite `media_cache` and changes filtering of eligible **future NextUp responses** immediately without Compose restart. OFF forwards upstream responses unchanged, including their count/pagination fields. Changing source, source URL, profile or user never inherits a previous ON state. The switch does not enable/disable the listener, rewrite nginx, edit AIO DB, modify Trakt or change playback.
3. **Client ingress route (separate, client-agnostic)** — only authorized client GET `/jellyfin/Shows/NextUp` requests should reach Gateway `/Shows/NextUp` through a *reviewed exact-path internal ingress*; the rest of Jellyfin FE API continues to AIO directly. Odin is merely a candidate client. Never point a full Jellyfin client directly to port 7101: Gateway is NOT a full Jellyfin reverse proxy. `/UserItems/Resume` and legacy Resume remain disabled by default.

## Operational order — do not skip

**R0 Baseline — read-only:** confirm exact running Bridge version and *one* writer, AIO health, existing named volume and backup/restic schedule, saved Compose rollback and current nginx routing. Do **not** dump `docker inspect Config.Env`, Trakt OAuth tokens, `ADMIN_KEY`, profile setup URLs or any full client-request paths into chat/CI logs.

**R1 RC2 single-Bridge promotion:** the existing `scripts/deploy-v140-rc2-homedocker.py` defaults to **read-only** attestation of CI artifact SHA-256, expected source head, RC1 baseline, AIO image continuity and disabled Gateway. Its `--apply` option is a distinct guarded RC1→RC2 cutover with RC1 automatic rollback; do not infer approval from CI PASS or this document. The archived RC2 image is not a stable GHCR release. See `docs/releases/v1.4.0-rc.2-unified.md`.

**R2 Listener provisioning (separate approved config change):** keep original AIO/Trakt credentials and media config. Configure source kind, upstream API root, exact same-user/profile mapping and `JELLYFIN_GATEWAY_ENABLED=true` only after verifying the selected backend's authenticated `/Users/Me`, `/Shows/NextUp` and `/Items/{SeriesId}` contracts. Preserve `JELLYFIN_GATEWAY_LEGACY_IMPORT=off` and `JELLYFIN_GATEWAY_RESUME_PASSTHROUGH=false`. Restart/recreate only existing Bridge with saved Compose rollback; **do not** publish port 7101 on the host or add a general public proxy location. Its listener uses an internal port. UI filter remains OFF after initial provisioning.

**R3 UI toggle smoke:** verify the current source and profile, Gateway listener and dashboard readiness. Record read-only counters. With no production client route, authenticated dashboard `Turn ON` then `Turn OFF` must persist state and not require service restart. Source-specific ON must not become ON for another selected source. No watch-event table writes or Trakt API calls occur from toggling.

**R4 Prove eligible response:** exercise a bounded authenticated `GET /Shows/NextUp` using the *same mapped Jellyfin account* only on an approved local/private transport. Observe raw OFF parity and ON with a verified show alias. Unknown users, two active resumes, failures, unverified aliases, incomplete counts, full Limit-only pages or later pages must leave the response intact. Do not manufacture Trakt watch events or repeatedly hunt duplicates. Existing historic alias evidence can be reused, but it is **not** proof of current live client-visible filtering.

**R5 Client ingress (separate opt-in):** only after R2–R4 PASS, route a *single approved client* via exact NextUp path and compare OFF / ON presentation and upstream 2xx behavior. Preserve every other AIO Jellyfin API endpoint (authentication, user/library lookups, Resume, playback, images, websockets, streaming and watch state) on its existing path. Roll back ingress before Bridge if unexpected 4xx/5xx or mismatched identity. Other clients remain untouched.

## Pagination and media scope

- **Implemented in RC2:** verified TV-series episode aliases in `GET /Shows/NextUp`, conditional on exact same-user mapping and unambiguous resume evidence.
- **Short initial page:** exact `StartIndex=0&Limit=N`, N=2..50, allowed only when the upstream page is provably complete, shorter than N and counts are coherent.
- **Unpaged:** no query or only `Limit=N`; newly guarded to require coherent `TotalRecordCount`/`Count`/`StartIndex`, and a bounded *short* complete result when `Limit` is present.
- **Fail-open view:** anything incomplete/ambiguous is returned as-is. Upstream transport failures can still return 502 (not a fabricated fallback response).
- **NOT implemented:** separate IMDb-alias normalization for Movies or movie Resume, generic all-client URL replacement, full pagination normalization, multi-upstream fan-out, client switching through UI.

## Operator acceptance and rollback table

| Condition | Expected outcome |
| --- | --- |
| Gateway listener OFF, filter preference OFF | Existing AIO/Trakt operation unchanged |
| Listener ON, UI filter OFF | Upstream NextUp returned unchanged, no metadata probes |
| Listener ON, UI filter ON, verified complete show aliases | Conditional one-card NextUp projection |
| Listener ON, UI filter ON, wrong user/missing graph/competing resumes | Original upstream response unchanged |
| Full Limit-only or incomplete NextUp page | Original upstream body and counts unchanged; aggregate passthrough counter increases |
| Switch backend `aiostreams`→`remux`/`jellyfin` | New scoped filter preference OFF until explicit UI action |
| Other non-NextUp Jellyfin route | Original AIO routing, not a 7101 Gateway handler |
| Error or unexpected regression in pilot | UI Turn OFF first, revert scoped ingress; Bridge RC1 rollback only if necessary |

CI/unit proof is **not** evidence that R2–R5 ran on HomeDocker. Never mark live source, Odin or G4 stable release as qualified solely from this Draft PR.
