# Release Recovery: paired canary clients (Odin + AIOStreams Desktop)

Decision: Odin and AIOStreams Desktop are the ONLY two initial client
targets. They are *two integration contracts*, not two watch-state
databases or two NextUp implementations. This document supersedes any
assumption that AIOStreams Desktop has a separate native watch-history API.

## Pinned upstream source contract (Viren070/AIOStreams)
Source SHA: 6ca32ed21e3036d87c35c49b24f2cf507d065ed1.

1. packages/docs/content/docs/apps/index.mdx and
   packages/docs/content/docs/apps/media-server.mdx say the native desktop
   app signs into the configuration's **Jellyfin-compatible media server**.
   UUID/share alias and Quick Connect are configuration/authentication
   ingress. Native mpv playback does not mean a separate NextUp backend.
2. packages/web/src/lib/queries.ts, useNextUp() explicitly requests:
   GET /Shows/NextUp?userId={sessionUserId}&Limit=24&
   EnableResumable=false (URL parameter order may vary).
   Continue Watching separately calls GET /UserItems/Resume with
   userId, Limit=24, MediaTypes=Video. The Home page may MERGE
   Next Up with Continue Watching based on the app's display preference.
3. packages/web/src/lib/session.tsx authenticates Quick Connect using
   POST /Users/AuthenticateWithQuickConnect. Packages/web/src/pages/
   sign-in.tsx may first POST /QuickConnect/Initiate.
4. packages/web/src/lib/client.ts calls the Jellyfin-compatible API with
   a MediaBrowser Authorization header and stores client/session user ID.
   Do not log or copy the configuration UUID, encrypted sign-in picker URL,
   Quick Connect secret, auth token, PIN or private host name.
5. Odin is a Jellyfin-compatible client (operator-confirmed). Its exact
   GET /Shows/NextUp request query, ordering, pagination behavior and
   user-agent contract are NOT yet captured. Do not guess that it matches
   the AIOStreams Desktop limit=24 query.

## Scope and safety: two isolated client routes / ONE source of truth

              AIO Streams configuration + watch_state
                              |
                  Same mapped Jellyfin user
                              |
            bounded canonical episode resolver (RC3 offwindow)
                              |
         +--------------------+-------------------+
         |                                        |
   Odin Jellyfin canary                        Desktop canary
  (actual GET shape TBD)                  (GET NextUp limit=24)
         |                                        |
  Jellyfin login passthrough                UUID/QuickConnect auth
         |                                        |
             full AIO fallback on any exception

- Both test clients may use the same config only if upstream GET /Users/Me
  and Bridge profile mapping confirm the EXACT same uuid/persona. A
  configuration UUID is never accepted as the Jellyfin userId by itself.
  Separate household users may have distinct NextUp progress; do not blend.
- AIOStreams is authoritative for episode metadata and watch state.
  Trakt/AIO writes and all stream/playback endpoints remain untouched.
- A **new test-only ingress base** is REQUIRED for real client UI canary,
  allowing all login, QuickConnect, sessions, libraries, images, streams,
  WebSocket and Resume traffic to pass through unchanged to AIO.
  The current RC2 NextUp-only gateway returns 404 on unrelated routes,
  so it cannot be used directly as the AIOStreams Desktop server URL.
  The ingress must only intercept GET /Shows/NextUp **after same-user
  verification** and an explicit device/client allowlist; it must not
  discriminate on User-Agent alone.
- No test ingress DNS/nginx/ports, token, config URL or app connection is
  changed in this Draft PR. No existing session or user preferences are
  changed. A client will need a SEPARATE opt-in profile/server entry so the
  existing Desktop/Odin sessions continue using the original URL.
- Changing the Desktop base URL risks its login picker/QuickConnect being
  tied to a different origin. Pass through full auth unchanged and re-login
  only in the new canary profile, never overwrite the existing connection.

## Request qualification and UI-specific test expectations

Desktop (verified upstream contract):
- Must receive the same user's GET NextUp query with exact
  userId, Limit=24 and EnableResumable=false.
- Test Next Up row on Home and optional user setting which merges Next Up
  into Continue Watching. Resume must remain byte-for-byte unchanged.
- QuickConnect/picker login and switching household users must never
  accidentally select someone else's Bridge watched graph.

Odin (pending observed client request):
- Login as usual using the AIO Jellyfin-compatible server address. Confirm
  GET /Users/Me identity maps to the intended Bridge profile.
- Only once a real request is captured in a PRIVATE and REDACTED local
  transcript can its NextUp parameters be qualified. Never display auth
  headers, config UUID, full URL/query with identifiers or episode titles.
- Verify Home/Continue Watching page, episode detail and proper series
  targeting. Stream playback remains out of scope without separate testing.

## What a safe one-client response must NOT do

- A full 24-card Desktop page is NOT grounds to append card 25 or drop
  current card 24. Do not falsify TotalRecordCount as a global total.
- Source rank >200 is historical candidate rank, not content ranking.
  One real offwindow episode confirmed by the backend does not itself
  qualify sorting into the user's NextUp Home page.
- A short page with a single qualified candidate is a research opportunity,
  still not an authorized production response rewrite.
- Do not mutate upstream (especially non-GET) requests, scopes, pagination,
  Resume, favorites, watched marks or playback, or alter AIO desktop UI.
- On stale snapshots, invalid IDs, duplicate SeriesId, unknown totals,
  timeout, auth mismatch or route mismatch, serve original AIO response.
  The prototype policy module ONLY RETURNS A CLASSIFICATION STRING and
  never creates an HTTP response.

## Specific acceptance gates

1. [x] HomeDocker shadow proof-of-concept one off-window, catalog-bound
   episode: PASS (one candidate, five GETs, seven catalog episodes).
2. [x] Static Desktop native-app query/auth source inspection: verified.
3. [x] Pure offline request eligibility tests for Desktop and explicit Odin
   pending-state, user mismatch, quickconnect passthrough and full page.
4. [ ] Observe the real Odin GET request shape via safe redacted read-only
   private inspection; record fields only.
5. [ ] Compare actual 24-slot Desktop baseline and target one scope;
   decide ordering, visibility and paging semantics without dropping cards.
6. [ ] Build separate isolated test ingress with explicit device allowlist,
   identical auth passthrough, feature switch, per-request time/request
   budget, fail-open original AIO bytes, audit counters and rollback.
7. [ ] Request owner's explicit authorization before adding a test ingress,
   changing any test-client URL or enabling response rewriting.

**Do not advance client ingress on green CI alone.** Production RC2 pinned,
Filter OFF, Odin/nginx as-is, Trakt/AIO histories unmodified, R5 HOLD.
