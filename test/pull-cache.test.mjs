import test from 'node:test';
import assert from 'node:assert/strict';
import { cachedPullPayload, makePullCacheEntry, stalePullPayload } from '../src/pull-cache.mjs';

test('fresh cache serves unchanged since without watched state', () => {
  const entry = makePullCacheEntry({
    version: 'v1',
    items: [{ metaId: 'tt1', videoId: 'tt1' }],
    watched: { movies: ['tt1'] },
  }, 1000);
  assert.deepEqual(cachedPullPayload(entry, 'v1', 1000 + 899_000, 900), {
    version: 'v1',
    items: [{ metaId: 'tt1', videoId: 'tt1' }],
  });
});

test('cache never answers initial or mismatched since', () => {
  const entry = makePullCacheEntry({ version: 'v1', items: [] }, 1000);
  assert.equal(cachedPullPayload(entry, null, 2000, 900), null);
  assert.equal(cachedPullPayload(entry, 'v0', 2000, 900), null);
});

test('fresh cache expires at ttl boundary', () => {
  const entry = makePullCacheEntry({ version: 'v1', items: [] }, 1000);
  assert.equal(cachedPullPayload(entry, 'v1', 1000 + 900_000, 900), null);
});

test('stale cache is bounded and only valid for matching since', () => {
  const entry = makePullCacheEntry({ version: 'v1', items: [] }, 1000);
  assert.deepEqual(stalePullPayload(entry, 'v1', 1000 + 3599_000, 3600), {
    version: 'v1',
    items: [],
  });
  assert.equal(stalePullPayload(entry, 'v1', 1000 + 3600_000, 3600), null);
  assert.equal(stalePullPayload(entry, 'v0', 2000, 3600), null);
});

test('invalid payload is not cached', () => {
  assert.equal(makePullCacheEntry({ version: 'v1' }), null);
  assert.equal(makePullCacheEntry({ items: [] }), null);
});
