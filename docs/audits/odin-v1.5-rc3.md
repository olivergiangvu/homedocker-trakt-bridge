# Odin Trakt Bridge v1.5 reverse-engineering audit for v1.2.0-rc.3

Date: 2026-10-04

## Purpose

Use the original Odin Trakt Bridge as a transport/recovery reference while preserving the correctness hardening already proven in HomeDocker Trakt Bridge.

Target:

- Odin-style lean Trakt I/O on the playback critical path.
- HomeDocker semantic history dedupe, stale-event protection, diagnostics, persistence, and rollback discipline.
- Faster and more reliable bidirectional VidHub <-> Strand handoff than Odin, including native-Trakt coexistence.

Odin is **not** treated as the correctness baseline. Real A/B testing showed:
- VidHub -> Strand can resume exactly (544787 ms -> 544786 ms in one control run).
- Strand -> VidHub is intermittently inconsistent.
- Odin still permits duplicate visible Trakt history in the real multi-writer setup.

## Confirmed A/B observations

### Stable Odin control

With the original Odin Trakt Bridge active:
- AIOStreams emitted the same redundant `unplayed` noise seen with HomeDocker.
- Odin accepted the events without the resolver 429 sequence seen in HomeDocker rc.1/rc.2.
- VidHub ended an episode at ~34.92% and Strand later started the same item at the same resume position (1 ms difference).

Conclusion: redundant AIOStreams `unplayed` is real, but it is not sufficient by itself to explain HomeDocker's failed cross-client resume.

### Odin limitations

Repeated handoff testing also showed:
- Strand -> VidHub is not perfectly stable.
- Duplicate history can still appear.

Likely reason for reverse-direction drift: Odin invalidates its pull cache only for writes that pass through Odin. Native Trakt writes performed directly by a client are external to Odin and cannot invalidate its 120-second pull cache.

## Static source comparison

### Odin v1.5 push path

For episodes Odin constructs:

```json
{
  "show": { "ids": { "imdb": "tt..." } },
  "episode": { "season": 2, "number": 5 },
  "progress": 34.92
}
```

and posts directly to:

- `/scrobble/start`
- `/scrobble/pause`
- `/scrobble/stop`

For history it uses the same provider IDs with nested show/season/episode payloads.

It does **not** pre-resolve each playback event through public metadata endpoints.

After a successful push Odin deletes its pull cache immediately.

### HomeDocker rc.2 hot path

HomeDocker currently resolves media before authenticated playback/history writes:

1. provider ID -> `/search/{provider}/{id}`
2. episode -> `/shows/{traktShow}/seasons/{season}/episodes/{episode}`
3. authenticated scrobble/history write

This adds public metadata calls before the write and created the real resolver 429 path observed during rc.1/rc.2 canaries.

### HomeDocker strengths to retain

Do not regress:

- source-identity playback watermark before upstream I/O;
- semantic history dedupe from v1.1;
- persistent SQLite state;
- managed write pacing;
- structured 429 diagnostics;
- pull stale-cache recovery;
- bulk-history handling;
- watchlist support;
- exact-digest release/rollback.

## Trakt capabilities relevant to rc.3

Current Trakt API contract supports:

- scrobble requests identified by provider IDs plus show season/episode;
- scrobble responses that return canonical movie/episode/show objects and IDs;
- `/sync/last_activities` includes:
  - `movies.paused_at`
  - `episodes.paused_at`

These allow two important improvements:

1. direct write first, with canonical IDs learned from the successful scrobble response instead of a blocking public lookup;
2. low-cost detection of native/external playback changes through `last_activities`.

## rc.3 staged design

### RC3-A: lean transport parity

Implement first:

1. Playback start/pause/stop:
   - source watermark first;
   - direct Trakt payload from AIO provider IDs;
   - zero public metadata lookups on the normal hot path.

2. played/unplayed:
   - direct movie or nested show/season/episode history payload;
   - keep HomeDocker semantic dedupe using stable source/provider media identity.

3. bulk played/unplayed:
   - direct show IDs + grouped season/episode numbers;
   - no show resolver on the normal path.

4. watchlist:
   - direct provider-ID payload where possible.

5. successful write:
   - preserve immediate pull-cache invalidation.

6. rate-limit handling:
   - a headerless POST 429 initially cools the write lane only;
   - a headerless GET 429 initially cools the read lane only;
   - if the other lane also receives 429 it independently cools, effectively producing a shared outage without pre-emptively disabling recovery reads.

7. Keep current HomeDocker 60-second pull freshness for the first canary. Do not increase the cache TTL until reverse-direction native-writer behavior is measured.

### RC3-B: adaptive pull efficiency

Only after RC3-A passes:

- add a cheap activity probe cadence using `last_activities`;
- compare playback activity using `movies.paused_at` and `episodes.paused_at`;
- refetch playback endpoints only when paused activity changes;
- refetch watched/watchlist only when their activity timestamps change;
- keep AIO poll hint short while allowing a longer full-state cache;
- optionally add write-through resume cache after successful scrobble.

This is intended to beat Odin's 120-second native-writer blind spot without returning to rc.2's high request amplification.

## Required invariants

For a normal episode pause/unfinished stop with a usable provider ID:

```text
public metadata requests = 0
authenticated Trakt writes = 1
```

A successful own write must invalidate the pull cache.

A failed newer playback event must still prevent an older retry from rewinding state.

An explicit played event following a successful completed scrobble must remain deduped.

An explicit Mark Unplayed must remain functional.

Native Trakt integrations on VidHub/Strand remain enabled.

## Canary matrix

Before rc.3 promotion:

1. VidHub -> Strand partial resume, repeated.
2. Strand -> VidHub partial resume, repeated.
3. Native Trakt enabled on both capable clients.
4. Partial stop 10-40%.
5. Partial stop 80-89% with AIO `played=false` must not be accidentally marked watched.
6. Completed stop with `played=true`.
7. Explicit played after completed stop: no duplicate bridge history.
8. Explicit Mark Unplayed still propagates.
9. Redundant AIO `unplayed` noise does not destroy eventual resume.
10. Headerless write 429 does not pre-emptively block a recovery GET.
11. If GET also receives 429, read lane cools independently.
12. No stale retry can rewind a newer state.
13. No public `/search/*` or `/shows/*/seasons/*/episodes/*` call during normal playback push.
