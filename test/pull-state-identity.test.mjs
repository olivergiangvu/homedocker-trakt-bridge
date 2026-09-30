import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWatchedState } from '../src/pull-state.mjs';

test('watched counts are keyed by every representable show alias', () => {
  const watched = buildWatchedState([], [{
    last_watched_at: '2026-09-30T08:00:00Z',
    show: {
      aired_episodes: 20,
      ids: { imdb: 'tt0903747', tmdb: 1396, tvdb: 81189 },
    },
    seasons: [{ number: 1, episodes: [{ number: 1, plays: 1 }] }],
  }]);
  const expected = { watched: 1, total: 20, at: 1790755200 };
  assert.deepEqual(watched.counts.tt0903747, expected);
  assert.deepEqual(watched.counts['tmdb:1396'], expected);
  assert.deepEqual(watched.counts['tvdb:81189'], expected);
  assert.deepEqual(watched.episodes, ['tt0903747:1:1']);
});

test('uses a Trakt-provided next_episode without guessing one', () => {
  const watched = buildWatchedState([], [{
    last_watched_at: '2026-09-30T08:00:00Z',
    show: { aired_episodes: 20, ids: { imdb: 'tt0903747' } },
    seasons: [{ number: 1, episodes: [{ number: 1, plays: 1 }] }],
    next_episode: { season: 1, number: 2 },
  }]);
  assert.deepEqual(watched.nextUp, [{
    type: 'series',
    metaId: 'tt0903747',
    videoId: 'tt0903747:1:2',
    season: 1,
    episode: 2,
    at: 1790755200,
  }]);
});

test('does not fabricate nextUp when Trakt did not provide a next episode', () => {
  const watched = buildWatchedState([], [{
    last_watched_at: '2026-09-30T08:00:00Z',
    show: { aired_episodes: 20, ids: { imdb: 'tt0903747' } },
    seasons: [{ number: 1, episodes: [{ number: 1, plays: 1 }] }],
  }]);
  assert.equal('nextUp' in watched, false);
});
