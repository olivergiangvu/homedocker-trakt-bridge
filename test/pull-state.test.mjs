import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPlaybackItems,
  buildWatchedState,
  buildWatchlistState,
  includeChangedStateForSince,
  preferredMetaId,
  stateVersionFromActivities,
} from '../src/pull-state.mjs';

test('prefers IMDb, then TMDB, then TVDB ids', () => {
  assert.equal(preferredMetaId({ imdb: 'tt1234567', tmdb: 42, tvdb: 7 }), 'tt1234567');
  assert.equal(preferredMetaId({ tmdb: 42, tvdb: 7 }), 'tmdb:42');
  assert.equal(preferredMetaId({ tvdb: 7 }), 'tvdb:7');
});

test('maps Trakt movie playback into an AIOStreams pull item with duration', () => {
  const [item] = buildPlaybackItems([
    {
      progress: 25,
      paused_at: '2026-09-30T04:00:00.000Z',
      movie: { runtime: 120, ids: { imdb: 'tt1234567', tmdb: 42 } },
    },
  ], []);
  assert.deepEqual(item, {
    type: 'movie',
    metaId: 'tt1234567',
    videoId: 'tt1234567',
    progressPercent: 25,
    played: false,
    at: 1790740800,
    durationMs: 7200000,
    positionMs: 1800000,
  });
});

test('maps Trakt episode playback into standard AIOStreams episode ids', () => {
  const [item] = buildPlaybackItems([], [
    {
      progress: 50,
      paused_at: '2026-09-30T04:00:00.000Z',
      show: { ids: { imdb: 'tt7654321' } },
      episode: { season: 2, number: 6, runtime: 40, ids: { trakt: 999 } },
    },
  ]);
  assert.equal(item.metaId, 'tt7654321');
  assert.equal(item.videoId, 'tt7654321:2:6');
  assert.equal(item.positionMs, 1200000);
  assert.equal(item.durationMs, 2400000);
});

test('state version is stable and changes with watched or watchlist activity', () => {
  const base = {
    movies: { watched_at: '2026-09-30T01:00:00Z', watchlisted_at: '2026-09-30T01:30:00Z' },
    episodes: { watched_at: '2026-09-30T02:00:00Z' },
    shows: { watchlisted_at: '2026-09-30T02:30:00Z' },
  };
  const a = stateVersionFromActivities(base);
  const b = stateVersionFromActivities(structuredClone(base));
  const watchedChanged = stateVersionFromActivities({ ...base, episodes: { watched_at: '2026-09-30T03:00:00Z' } });
  const watchlistChanged = stateVersionFromActivities({ ...base, shows: { watchlisted_at: '2026-09-30T03:30:00Z' } });
  assert.equal(a, b);
  assert.notEqual(a, watchedChanged);
  assert.notEqual(a, watchlistChanged);
  assert.equal(includeChangedStateForSince(a, a), false);
  assert.equal(includeChangedStateForSince(null, a), true);
});

test('maps authoritative Trakt watched movies and show episodes', () => {
  const watched = buildWatchedState(
    [{ movie: { ids: { imdb: 'tt1111111' } } }],
    [{
      plays: 3,
      last_watched_at: '2026-09-30T03:00:00Z',
      show: { aired_episodes: 10, ids: { imdb: 'tt2222222' } },
      seasons: [
        { number: 1, episodes: [
          { number: 1, plays: 1, last_watched_at: '2026-09-29T01:00:00Z' },
          { number: 2, plays: 0 },
          { number: 3, plays: 2, last_watched_at: '2026-09-30T03:00:00Z' },
        ] },
      ],
    }],
  );
  assert.deepEqual(watched.movies, ['tt1111111']);
  assert.deepEqual(watched.episodes, ['tt2222222:1:1', 'tt2222222:1:3']);
  assert.deepEqual(watched.counts.tt2222222, { watched: 2, total: 10, at: 1790737200 });
});

test('maps Trakt movie and show watchlist into AIOStreams watchlist rows', () => {
  const watchlist = buildWatchlistState(
    [{ listed_at: '2026-09-30T02:00:00Z', movie: { ids: { imdb: 'tt1111111' } } }],
    [{ listed_at: '2026-09-30T03:00:00Z', show: { ids: { tmdb: 222 } } }],
  );
  assert.deepEqual(watchlist, [
    { type: 'series', metaId: 'tmdb:222', at: 1790737200 },
    { type: 'movie', metaId: 'tt1111111', at: 1790733600 },
  ]);
});

test('watchlist mapping drops rows that cannot be represented in AIOStreams id space', () => {
  const watchlist = buildWatchlistState(
    [{ listed_at: '2026-09-30T02:00:00Z', movie: { ids: { trakt: 123 } } }],
    [],
  );
  assert.deepEqual(watchlist, []);
});

test('refuses incomplete watched-show payload instead of returning destructive empty history', () => {
  assert.throws(
    () => buildWatchedState([], [{ show: { ids: { imdb: 'tt3333333' } } }]),
    /seasons missing/,
  );
});
