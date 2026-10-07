import test from 'node:test';
import assert from 'node:assert/strict';

import {
  canonicalHistoryState,
  mutateCanonicalBulkHistoryState,
  mutateCanonicalHistoryState,
  rememberCanonicalWatchedSnapshot,
} from '../src/canonical-history.mjs';

class MemoryDb {
  constructor() {
    this.values = new Map();
  }

  cacheGet(key) {
    return this.values.get(key) ?? null;
  }

  cacheSet(key, value) {
    this.values.set(key, JSON.parse(JSON.stringify(value)));
  }
}

function episodeEvent(overrides = {}) {
  return {
    scope: 'episode',
    metaId: 'tt1000000',
    videoId: 'tt1000000:1:2',
    season: 1,
    episode: 2,
    ...overrides,
  };
}

test('authoritative watched snapshot distinguishes watched and unwatched items', () => {
  const db = new MemoryDb();

  rememberCanonicalWatchedSnapshot(
    db,
    'p1',
    {
      movies: ['tt2000000'],
      episodes: ['tt1000000:1:2'],
    },
    {
      version: 'v1',
      nowMs: 1_000_000,
    },
  );

  const watched = canonicalHistoryState(
    db,
    'p1',
    episodeEvent(),
    {
      nowMs: 1_100_000,
      maxAgeSeconds: 900,
    },
  );

  assert.equal(watched.known, true);
  assert.equal(watched.watched, true);
  assert.equal(watched.itemKey, 'e|tt1000000:1:2');

  const unwatched = canonicalHistoryState(
    db,
    'p1',
    episodeEvent({
      videoId: 'tt1000000:1:3',
      episode: 3,
    }),
    {
      nowMs: 1_100_000,
      maxAgeSeconds: 900,
    },
  );

  assert.equal(unwatched.known, true);
  assert.equal(unwatched.watched, false);
});

test('local history mutation does not make an old pull snapshot look fresh', () => {
  const db = new MemoryDb();

  rememberCanonicalWatchedSnapshot(
    db,
    'p1',
    { movies: [], episodes: ['tt1000000:1:2'] },
    {
      version: 'v1',
      nowMs: 1_000_000,
    },
  );

  mutateCanonicalHistoryState(
    db,
    'p1',
    episodeEvent(),
    false,
    { nowMs: 2_000_000 },
  );

  const stale = canonicalHistoryState(
    db,
    'p1',
    episodeEvent(),
    {
      nowMs: 2_000_000,
      maxAgeSeconds: 300,
    },
  );

  assert.equal(stale.known, false);
  assert.equal(stale.reason, 'snapshot_stale');
});

test('bulk canonical mutation updates only the listed episodes', () => {
  const db = new MemoryDb();

  rememberCanonicalWatchedSnapshot(
    db,
    'p1',
    {
      movies: [],
      episodes: [
        'tt1000000:1:1',
        'tt1000000:1:2',
        'tt1000000:1:3',
      ],
    },
    {
      version: 'v1',
      nowMs: 1_000_000,
    },
  );

  const changed = mutateCanonicalBulkHistoryState(
    db,
    'p1',
    {
      metaId: 'tt1000000',
      videos: [
        {
          videoId: 'tt1000000:1:1',
          season: 1,
          episode: 1,
        },
        {
          videoId: 'tt1000000:1:3',
          season: 1,
          episode: 3,
        },
      ],
    },
    false,
    { nowMs: 1_100_000 },
  );

  assert.equal(changed, 2);

  assert.equal(
    canonicalHistoryState(
      db,
      'p1',
      episodeEvent({ videoId: 'tt1000000:1:1', episode: 1 }),
      { nowMs: 1_100_000 },
    ).watched,
    false,
  );

  assert.equal(
    canonicalHistoryState(
      db,
      'p1',
      episodeEvent({ videoId: 'tt1000000:1:2', episode: 2 }),
      { nowMs: 1_100_000 },
    ).watched,
    true,
  );
});
