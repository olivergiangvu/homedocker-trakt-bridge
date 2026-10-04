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

function clientFixture() {
  const db = fakeDb();
  const client = new TraktClient({ historyDedupeSeconds: 300 }, db);
  const calls = [];
  client.resolveMedia = async () => ({
    kind: 'episode',
    episode: { ids: { trakt: 73482 } },
    show: { ids: { trakt: 1388 } },
  });
  return { client, calls };
}

test('newer failed playback event still prevents an older AIOStreams retry from rewinding state', async () => {
  const { client, calls } = clientFixture();
  let failNext = true;
  client.request = async (_profileId, path, options) => {
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
    return {};
  };

  await assert.rejects(
    () => client.applyEvent('p1', {
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
    id: 'stop-old',
    event: 'stop',
    at: 1000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 13,
  });

  assert.equal(stale.ignored, 'stale_playback_event');
  assert.equal(stale.newestEventId, 'pause-new');
  assert.equal(stale.deltaSeconds, 1000);
  assert.equal(calls.length, 1, 'stale retry must not call Trakt');
});

test('a newer backwards seek remains valid because ordering uses event time, not progress', async () => {
  const { client, calls } = clientFixture();
  client.request = async (_profileId, path, options) => {
    calls.push({ path, options: structuredClone(options) });
    return {};
  };

  await client.applyEvent('p1', {
    id: 'pause-80',
    event: 'pause',
    at: 1000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 80,
  });

  const seekBack = await client.applyEvent('p1', {
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


test('newer event that fails during media resolution still blocks an older retry before resolution', async () => {
  const db = fakeDb();
  const client = new TraktClient({ historyDedupeSeconds: 300 }, db);
  let resolveCalls = 0;

  client.resolveMedia = async () => {
    resolveCalls += 1;
    throw new BridgeError('Trakt API 429', {
      status: 429,
      code: 'trakt_429',
      retryAfter: '65',
      upstreamPath: '/shows/285217/seasons/1/episodes/2',
    });
  };

  const identity = {
    scope: 'episode',
    metaId: 'tt36885662',
    videoId: 'tt36885662:1:2',
    season: 1,
    episode: 2,
  };

  await assert.rejects(
    () => client.applyEvent('p1', {
      ...identity,
      id: 'pause-new',
      event: 'pause',
      at: 2000,
    }, {
      kind: 'scrobble',
      action: 'pause',
      progress: 11.84,
    }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const stale = await client.applyEvent('p1', {
    ...identity,
    id: 'stop-old',
    event: 'stop',
    at: 1000,
  }, {
    kind: 'scrobble',
    action: 'pause',
    progress: 7,
  });

  assert.equal(stale.ignored, 'stale_playback_event');
  assert.equal(stale.watermarkSource, 'source');
  assert.equal(stale.newestEventId, 'pause-new');
  assert.equal(resolveCalls, 1, 'older retry must be rejected before another resolver call');
});
