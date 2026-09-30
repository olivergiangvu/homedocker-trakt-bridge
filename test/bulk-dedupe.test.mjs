import test from 'node:test';
import assert from 'node:assert/strict';
import { coveredByRecentBulk, rememberBulkCoverage } from '../src/bulk-dedupe.mjs';

function fakeDb() {
  const rows = new Map();
  return {
    rows,
    cacheSet(key, value) { rows.set(key, structuredClone(value)); },
    cacheGet(key) { return rows.get(key) ?? null; },
  };
}

const bulk = {
  id: 'b|tt2467372:7|played|1000|1',
  event: 'played',
  scope: 'season',
  at: 1000,
  part: 1,
  parts: 1,
  videos: [
    { videoId: 'tt2467372:7:1', season: 7, episode: 1 },
    { videoId: 'tt2467372:7:2', season: 7, episode: 2 },
  ],
};

test('single same-kind episode shortly after successful bulk is covered', () => {
  const db = fakeDb();
  assert.equal(rememberBulkCoverage(db, 'p1', bulk, 300), 2);
  const hit = coveredByRecentBulk(db, 'p1', {
    id: 'e1', event: 'played', scope: 'episode', at: 1106,
    metaId: 'tt2467372', videoId: 'tt2467372:7:2', season: 7, episode: 2,
  }, 300);
  assert.equal(hit.bulkEventId, bulk.id);
  assert.equal(hit.videoId, 'tt2467372:7:2');
  assert.equal(hit.deltaSeconds, 106);
});

test('opposite event is never covered by a played bulk', () => {
  const db = fakeDb();
  rememberBulkCoverage(db, 'p1', bulk, 300);
  assert.equal(coveredByRecentBulk(db, 'p1', {
    id: 'e2', event: 'unplayed', scope: 'episode', at: 1106,
    videoId: 'tt2467372:7:2', season: 7, episode: 2,
  }, 300), null);
});

test('same-kind event outside dedupe window is not covered', () => {
  const db = fakeDb();
  rememberBulkCoverage(db, 'p1', bulk, 300);
  assert.equal(coveredByRecentBulk(db, 'p1', {
    id: 'e3', event: 'played', scope: 'episode', at: 1301,
    videoId: 'tt2467372:7:2', season: 7, episode: 2,
  }, 300), null);
});

test('event timestamp before bulk is not covered', () => {
  const db = fakeDb();
  rememberBulkCoverage(db, 'p1', bulk, 300);
  assert.equal(coveredByRecentBulk(db, 'p1', {
    id: 'e4', event: 'played', scope: 'episode', at: 999,
    videoId: 'tt2467372:7:2', season: 7, episode: 2,
  }, 300), null);
});

test('movies are not suppressed by bulk episode coverage', () => {
  const db = fakeDb();
  rememberBulkCoverage(db, 'p1', bulk, 300);
  assert.equal(coveredByRecentBulk(db, 'p1', {
    id: 'm1', event: 'played', scope: 'movie', at: 1100,
    videoId: 'tt2467372:7:2',
  }, 300), null);
});
