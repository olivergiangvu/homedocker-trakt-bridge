import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';
import { BridgeError } from '../src/errors.mjs';

function fakeClient(handler) {
  const cache = new Map();
  const db = {
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => cache.set(key, value),
  };
  const client = new TraktClient({ traktClientId: 'x', userAgent: 'test', pullMaxPages: 10 }, db);
  client.publicRequest = handler;
  return client;
}

test('resolves a show from metaId when event.ids is absent', async () => {
  const paths = [];
  const client = fakeClient(async (path) => {
    paths.push(path);
    return [{ type: 'show', show: { ids: { trakt: 1, imdb: 'tt0903747', tmdb: 1396, tvdb: 81189 } } }];
  });
  const resolved = await client.resolveShow({ metaId: 'tt0903747' });
  assert.equal(paths[0], '/search/imdb/tt0903747?type=show');
  assert.equal(resolved.show.ids.trakt, 1);
});

test('falls through a stale IMDb alias to TMDB', async () => {
  const paths = [];
  const client = fakeClient(async (path) => {
    paths.push(path);
    if (path.startsWith('/search/imdb/')) {
      throw new BridgeError('Trakt API 404', { status: 422, code: 'trakt_404', upstreamPath: path });
    }
    return [{ type: 'show', show: { ids: { trakt: 2, tmdb: 1396, tvdb: 81189 } } }];
  });
  const resolved = await client.resolveShow({
    metaId: 'tt0903747',
    ids: { imdb: 'tt0903747', tmdb: 1396, tvdb: 81189 },
  });
  assert.deepEqual(paths.slice(0, 2), [
    '/search/imdb/tt0903747?type=show',
    '/search/tmdb/1396?type=show',
  ]);
  assert.equal(resolved.show.ids.trakt, 2);
});

test('does not swallow transient upstream failures while trying aliases', async () => {
  const client = fakeClient(async (path) => {
    throw new BridgeError('Trakt API 429', { status: 429, code: 'trakt_429', upstreamPath: path });
  });
  await assert.rejects(
    () => client.resolveShow({ metaId: 'tt0903747', ids: { tmdb: 1396 } }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );
});
