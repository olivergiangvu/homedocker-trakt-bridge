import test from 'node:test';
import assert from 'node:assert/strict';
import {
  idsFromMetaId,
  normalizeProviderIds,
  preferredMetaId,
  providerIdsForEvent,
  representableMetaIds,
} from '../src/media-ids.mjs';

test('parses supported AIOStreams meta id spellings', () => {
  assert.deepEqual(idsFromMetaId('tt0903747'), { imdb: 'tt0903747' });
  assert.deepEqual(idsFromMetaId('TMDB:1396'), { tmdb: 1396 });
  assert.deepEqual(idsFromMetaId('tvdb:81189'), { tvdb: 81189 });
  assert.deepEqual(idsFromMetaId('kitsu:123'), {});
});

test('normalizes only valid shared provider ids', () => {
  assert.deepEqual(normalizeProviderIds({ imdb: 'TT0903747', tmdb: '1396', tvdb: 81189, mal: 1 }), {
    imdb: 'tt0903747', tmdb: 1396, tvdb: 81189,
  });
  assert.deepEqual(normalizeProviderIds({ imdb: 'bad', tmdb: 'x', tvdb: -1 }), {});
});

test('explicit ids win while metaId fills missing provider ids', () => {
  assert.deepEqual(providerIdsForEvent({
    metaId: 'tmdb:1396',
    ids: { imdb: 'tt0903747', tvdb: '81189' },
  }), { tmdb: 1396, imdb: 'tt0903747', tvdb: 81189 });
});

test('returns every representable alias in stable preference order', () => {
  assert.deepEqual(representableMetaIds({ imdb: 'tt0903747', tmdb: 1396, tvdb: 81189 }), [
    'tt0903747', 'tmdb:1396', 'tvdb:81189',
  ]);
  assert.equal(preferredMetaId({ tmdb: 1396, tvdb: 81189 }), 'tmdb:1396');
});
