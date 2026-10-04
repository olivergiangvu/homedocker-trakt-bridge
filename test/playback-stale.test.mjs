import test from 'node:test';
import assert from 'node:assert/strict';

import { TraktClient } from '../src/trakt.mjs';
import { BridgeError } from '../src/errors.mjs';

function fakeDb() {
  const rows = new Map();
  return {
    cacheGet(key) { return rows.get(key) ?? null; },
    cacheSet(key, value) { rows.set(key, structuredClone(value)); },
  };
}

const episodeIdentity = {
  scope: 'episode',
  metaId: 'tt36885662',
  videoId: 'tt36885662:1:2',
  season: 1,
  episode: 2,
};

function clientFixture() {
  const db = fakeDb();
  const client = new TraktClient({ historyDedupeSeconds: 300 }, db);
  const calls = [];
  return { client, calls };
}

test('newer failed direct playback write still prevents an older AIOStreams retry from rewinding state', async () => {
  const { client, calls } = clientFixture();
  let failNext = true;
  client.requestDetailed = async (_profileId, path, options) => {
    calls.push({ path, options: structuredClone(options) });
    if (failNext) {
      failNext = false;
      throw new BridgeError('Trakt API 429', {
        status: 429,
        code: 'trakt_429',
        retryAfter: '120',
        upstreamPath: path,
      });
    }
    return {
      status: 201,
      headers: new Headers(),
      data: { action: 'pause' },
    };
  };

  await assert.rejects(
    () => client.applyEvent('p1', {
      ...episodeIdentity,
      id: 'pause-new',
      event: 'pause',
      at: 2000,
    }, {
      kind: 'scrobble',
      action: 'pause',
      progress: 80,
    }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const stale = await client.applyEvent('p1', {
    ...episodeIdentity,
    id: 'stop-old',
    event: 'stop',
    at: 1000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 13,
  });

  assert.equal(stale.ignored, 'stale_playback_event');
  assert.equal(stale.watermarkSource, 'source');
  assert.equal(stale.newestEventId, 'pause-new');
  assert.equal(stale.deltaSeconds, 1000);
  assert.equal(calls.length, 1, 'stale retry must not call Trakt');
});

test('a newer backwards seek remains valid because ordering uses event time, not progress', async () => {
  const { client, calls } = clientFixture();
  client.requestDetailed = async (_profileId, path, options) => {
    calls.push({ path, options: structuredClone(options) });
    return {
      status: 201,
      headers: new Headers(),
      data: { action: path.endsWith('/start') ? 'start' : 'pause' },
    };
  };

  await client.applyEvent('p1', {
    ...episodeIdentity,
    id: 'pause-80',
    event: 'pause',
    at: 1000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 80,
  });

  const seekBack = await client.applyEvent('p1', {
    ...episodeIdentity,
    id: 'start-30',
    event: 'start',
    at: 1100,
  }, {
    kind: 'scrobble',
    action: 'start',
    progress: 30,
  });

  assert.equal(seekBack.action, 'scrobble:start');
  assert.deepEqual(calls.map((x) => x.path), ['/scrobble/pause', '/scrobble/start']);
});

test('normal episode playback never invokes the public metadata resolver hot path', async () => {
  const { client, calls } = clientFixture();
  let publicCalls = 0;
  client.publicRequest = async () => {
    publicCalls += 1;
    throw new Error('public resolver must not run');
  };
  client.requestDetailed = async (_profileId, path, options) => {
    calls.push({ path, options: structuredClone(options) });
    return {
      status: 201,
      headers: new Headers(),
      data: {
        action: 'pause',
        episode: { ids: { trakt: 73482 } },
        show: { ids: { trakt: 1388, imdb: 'tt36885662' } },
      },
    };
  };

  const result = await client.applyEvent('p1', {
    ...episodeIdentity,
    id: 'pause-direct',
    event: 'pause',
    at: 2000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 11.84,
  });

  assert.equal(result.action, 'scrobble:pause');
  assert.equal(result.transport, 'direct-provider-ids');
  assert.equal(publicCalls, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/scrobble/pause');
  assert.deepEqual(calls[0].options.body, {
    show: { ids: { imdb: 'tt36885662' } },
    episode: { season: 1, number: 2 },
    progress: 11.84,
  });
});
