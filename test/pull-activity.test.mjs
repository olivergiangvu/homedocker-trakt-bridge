import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activityDecision,
  activitySnapshot,
  reusablePlaybackItems,
} from '../src/pull-activity.mjs';

function activities(overrides = {}) {
  return {
    all: '2026-10-04T00:00:00.000Z',
    movies: {
      paused_at: '2026-10-04T00:00:00.000Z',
      watched_at: '2026-10-04T00:00:00.000Z',
      watchlisted_at: '2026-10-04T00:00:00.000Z',
      ...(overrides.movies || {}),
    },
    episodes: {
      paused_at: '2026-10-04T00:00:00.000Z',
      watched_at: '2026-10-04T00:00:00.000Z',
      ...(overrides.episodes || {}),
    },
    shows: {
      watchlisted_at: '2026-10-04T00:00:00.000Z',
      ...(overrides.shows || {}),
    },
    watchlist: {
      updated_at: '2026-10-04T00:00:00.000Z',
      ...(overrides.watchlist || {}),
    },
    ...Object.fromEntries(
      Object.entries(overrides).filter(([k]) =>
        !['movies', 'episodes', 'shows', 'watchlist'].includes(k)
      )
    ),
  };
}

test('unchanged activity reuses cached playback and needs no full state read', () => {
  const prev = activitySnapshot(activities(), 'mode:trakt');
  const now = activitySnapshot(activities(), 'mode:trakt');
  const d = activityDecision(prev, now);

  assert.equal(d.fetchPlayback, false);
  assert.equal(d.fetchState, false);
  assert.deepEqual(
    reusablePlaybackItems(
      { identityVersion: 'mode:trakt', items: [{ videoId: 'tt1:1:1' }] },
      now,
      d,
    ),
    [{ videoId: 'tt1:1:1' }],
  );
});

test('native playback pause refreshes playback without forcing watched state', () => {
  const prev = activitySnapshot(activities(), 'mode:trakt');
  const now = activitySnapshot(activities({
    all: '2026-10-04T00:01:00.000Z',
    episodes: { paused_at: '2026-10-04T00:01:00.000Z' },
  }), 'mode:trakt');
  const d = activityDecision(prev, now);

  assert.equal(d.playbackChanged, true);
  assert.equal(d.stateChanged, false);
  assert.equal(d.fetchPlayback, true);
  assert.equal(d.fetchState, false);
});

test('watched/watchlist activity refreshes both state and playback', () => {
  const prev = activitySnapshot(activities(), 'mode:trakt');
  const now = activitySnapshot(activities({
    all: '2026-10-04T00:02:00.000Z',
    episodes: { watched_at: '2026-10-04T00:02:00.000Z' },
  }), 'mode:trakt');
  const d = activityDecision(prev, now);

  assert.equal(d.fetchPlayback, true);
  assert.equal(d.fetchState, true);
});

test('unknown global activity change fails safe to a full refresh', () => {
  const prev = activitySnapshot(activities(), 'mode:trakt');
  const now = activitySnapshot(activities({
    all: '2026-10-04T00:03:00.000Z',
  }), 'mode:trakt');
  const d = activityDecision(prev, now);

  assert.equal(d.unknownChanged, true);
  assert.equal(d.fetchPlayback, true);
  assert.equal(d.fetchState, true);
});

test('identity revision invalidates cached playback even without Trakt activity', () => {
  const prev = activitySnapshot(activities(), 'mode:aiostreams:1');
  const now = activitySnapshot(activities(), 'mode:aiostreams:2');
  const d = activityDecision(prev, now);

  assert.equal(d.identityChanged, true);
  assert.equal(d.fetchPlayback, true);
  assert.equal(
    reusablePlaybackItems(
      { identityVersion: 'mode:aiostreams:1', items: [{ videoId: 'tt1:1:1' }] },
      now,
      d,
    ),
    null,
  );
});
