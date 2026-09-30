import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';
import { BridgeError } from '../src/errors.mjs';
import { learnShowAlias, loadIdentityAliases } from '../src/identity-alias.mjs';

function fakeClient(handler) {
  const cache = new Map();
  const db = {
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => cache.set(key, structuredClone(value)),
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

test('learns the AIOStreams IMDb spelling after an unfinished stop mapped to Trakt pause', async () => {
  const client = fakeClient(async (path) => {
    if (path.startsWith('/search/imdb/')) {
      return [{
        type: 'show',
        show: { ids: { trakt: 123, imdb: 'tt44051354', tmdb: 456, tvdb: 789 } },
      }];
    }
    if (path === '/shows/123/seasons/1/episodes/6') {
      return { ids: { trakt: 9001, tvdb: 8001 } };
    }
    throw new Error(`unexpected public path ${path}`);
  });
  client.request = async () => ({});

  const result = await client.applyEvent('profile-a', {
    event: 'stop',
    scope: 'episode',
    metaId: 'tt44094505',
    videoId: 'tt44094505:1:6',
    season: 1,
    episode: 6,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 30.603,
  });

  assert.equal(result.action, 'scrobble:pause');
  assert.equal(result.identityAlias.preferredMetaId, 'tt44094505');
  assert.equal(result.identityAlias.traktImdb, 'tt44051354');
  assert.equal(loadIdentityAliases(client.db, 'profile-a').shows['123'].preferredMetaId, 'tt44094505');
});

test('does not learn an alias from a plain pause event', async () => {
  const client = fakeClient(async (path) => {
    if (path.startsWith('/search/imdb/')) {
      return [{ type: 'show', show: { ids: { trakt: 123, imdb: 'tt44051354' } } }];
    }
    if (path === '/shows/123/seasons/1/episodes/6') return { ids: { trakt: 9001 } };
    throw new Error(`unexpected public path ${path}`);
  });
  client.request = async () => ({});

  const result = await client.applyEvent('profile-a', {
    event: 'pause',
    scope: 'episode',
    metaId: 'tt44094505',
    videoId: 'tt44094505:1:6',
    season: 1,
    episode: 6,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 30.603,
  });

  assert.equal(result.action, 'scrobble:pause');
  assert.equal(result.identityAlias, undefined);
  assert.equal(Object.keys(loadIdentityAliases(client.db, 'profile-a').shows).length, 0);
});

test('pull rewrites a Trakt IMDb alias to the learned AIOStreams spelling', async () => {
  const client = fakeClient(async () => []);
  learnShowAlias(
    client.db,
    'profile-a',
    { trakt: 123, imdb: 'tt44051354' },
    'tt44094505',
  );

  client.request = async (_profileId, path) => {
    if (path === '/sync/last_activities') {
      return {
        movies: { watched_at: null, watchlisted_at: null },
        episodes: { watched_at: null },
        shows: { watchlisted_at: null },
      };
    }
    throw new Error(`unexpected request path ${path}`);
  };
  client.requestAllPages = async (_profileId, path) => {
    if (path === '/sync/playback/episodes?extended=full') {
      return [{
        progress: 30.603,
        paused_at: '2026-09-30T10:00:00Z',
        show: { ids: { trakt: 123, imdb: 'tt44051354', tmdb: 456, tvdb: 789 } },
        episode: { season: 1, number: 6, runtime: 63 },
      }];
    }
    return [];
  };

  const payload = await client.pullState('profile-a');
  assert.equal(payload.items.length, 1);
  assert.equal(payload.items[0].metaId, 'tt44094505');
  assert.equal(payload.items[0].videoId, 'tt44094505:1:6');
});
