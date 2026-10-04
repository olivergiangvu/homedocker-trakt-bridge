import test from 'node:test';
import assert from 'node:assert/strict';

import { observePlaybackWatermark } from '../src/playback-watermark.mjs';

function fakeDb() {
  const rows = new Map();
  return {
    rows,
    cacheGet(key) { return rows.get(key) ?? null; },
    cacheSet(key, value) { rows.set(key, structuredClone(value)); },
  };
}

const episode = {
  kind: 'episode',
  episode: { ids: { trakt: 73482 } },
};

test('newer playback event advances the watermark', () => {
  const db = fakeDb();
  const first = observePlaybackWatermark(db, 'p1', episode, {
    id: 'start-old', event: 'start', at: 1000,
  });
  const second = observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-new', event: 'pause', at: 1100,
  });

  assert.equal(first.stale, false);
  assert.equal(second.stale, false);
  assert.equal(second.newestAt, 1100);
});

test('older retry is stale even when its progress could be higher', () => {
  const db = fakeDb();
  observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-new', event: 'pause', at: 1100,
  });
  const stale = observePlaybackWatermark(db, 'p1', episode, {
    id: 'stop-old', event: 'stop', at: 1000,
  });

  assert.equal(stale.stale, true);
  assert.equal(stale.newestEventId, 'pause-new');
  assert.equal(stale.deltaSeconds, 100);
});

test('equal timestamps are allowed for legitimate edges in one second', () => {
  const db = fakeDb();
  observePlaybackWatermark(db, 'p1', episode, {
    id: 'start-1', event: 'start', at: 1000,
  });
  const edge = observePlaybackWatermark(db, 'p1', episode, {
    id: 'stop-1', event: 'stop', at: 1000,
  });
  assert.equal(edge.stale, false);
});

test('newer event remains valid even after seeking backwards', () => {
  const db = fakeDb();
  observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-80', event: 'pause', at: 1000, positionMs: 800,
  });
  const seekBack = observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-30', event: 'pause', at: 1100, positionMs: 300,
  });
  assert.equal(seekBack.stale, false);
  assert.equal(seekBack.newestAt, 1100);
});

test('watermark survives reconstruction through persistent cache', () => {
  const db = fakeDb();
  observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-new', event: 'pause', at: 1100,
  });

  const stale = observePlaybackWatermark(db, 'p1', episode, {
    id: 'start-old', event: 'start', at: 1000,
  });
  assert.equal(stale.stale, true);
});

test('non-playback and missing timestamps do not create a watermark', () => {
  const db = fakeDb();
  assert.equal(observePlaybackWatermark(db, 'p1', episode, {
    id: 'played-1', event: 'played', at: 1000,
  }), null);
  assert.equal(observePlaybackWatermark(db, 'p1', episode, {
    id: 'pause-1', event: 'pause',
  }), null);
  assert.equal(db.rows.size, 0);
});
