# RC7 Phase A — Dual IMDb Continue Watching collision audit

**Status:** read-only diagnostic; **not** a production identity rewrite or release candidate.
**Production baseline:** v1.3.0-rc.6 remains pinned and healthy. Keep PULL_IDENTITY_MODE=trakt and CANONICAL_ON_DEMAND_VERIFY=false.

## Historical regression and scope

The known test fixture describes one Trakt show, show ID 263102 / TMDB 276470, represented as IMDb tt44051354 in Trakt and tt44094505 in AIOStreams. Bridge v0.3.6 deliberately made Trakt IMDb the default pull spelling because rewriting it to AIO caused **two Continue Watching cards** for clients consuming native Trakt and AIO simultaneously.

RC6 pull output is already canonically Trakt-keyed; changing PULL_IDENTITY_MODE=aiostreams is **not** an acceptable speculative fix. AIO's local SQLite watch_state may contain separate item keys for these spellings, independent of Bridge pull. A client may additionally cache a second card through its native Trakt integration.

## Read-only diagnosis

Copy scripts/audit-dual-imdb.py to the Ubuntu host and run:

    sudo python3 ~/audit-dual-imdb.py

The script validates exactly one connected Bridge profile and one active AIO sink with the expected name and instance ID (e3fe3b0). It opens **both databases using SQLite mode=ro and PRAGMA query_only=ON**. It makes no HTTP/Trakt calls and performs no writes, container restarts, or migrations.

For learned Bridge alias pairs, and the documented show fixture, it groups unfinished episode resumes (played=0 and position_ms>0) within the exact AIO uuid/persona scope. It reports counts by IMDb, overlapping season/episode keys, and source/position/time for up to ten active rows per alias. User UUID and persona are never printed; only a short hashed scope identifier. Secrets and delivery bodies are not read or printed.

A collision candidate means two active AIO resumes exist for one confirmed Trakt show under two IMDb spellings. It **does not prove** a particular client showed duplicate cards. Conversely, zero candidates does not exclude client-side/native-Trakt duplicates, unknown alias pairs or old/pruned records.

## Phase B decision matrix

1. **Two active AIO aliases in the same scope**: collect provenance and investigate upstream AIO/Jellyfin Continue Watching identity normalization. Do not merge database keys.
2. **One active AIO alias but duplicates visible in client**: compare client-native Trakt and AIO item IDs/caches. Keep Bridge in Trakt mode.
3. **No matching alias evidence**: collect bounded provider-ID mapping, never infer equivalence from title text alone.
4. **Multiple Trakt history records after deliberate rewatch**: preserve legitimate individual plays. Require source/session/timing evidence before dedupe.

## Gates before an RC7 behavioral fix

- Real same-show collision backed by raw AIO record evidence;
- patch in the actual duplicate-producing layer;
- identity anchor by Trakt / TMDB / TVDB IDs, not title similarity;
- no AIO SQLite writes, historical bulk rewrite or replay;
- retain 90% AIO playback completion, RC6 scrobble and pinned production image;
- test native Trakt plus AIO and unrelated titles sharing display names;
- no uncontrolled Trakt GET/POST calls or 429 amplification.

**Phase A does not justify deployment of a new Bridge image.**
