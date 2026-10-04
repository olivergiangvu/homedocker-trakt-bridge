import test from 'node:test';
import assert from 'node:assert/strict';

import { ManagedTraktClient } from '../src/managed-trakt.mjs';
import { BridgeError } from '../src/errors.mjs';
import { startJsonServer, redirectTraktFetch } from './helpers/http-fixture.mjs';

function tokenDb() {
  const tokens = {
    accessToken: 'access-live',
    refreshToken: 'refresh-live',
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
  };
  const cache = new Map();
  return {
    getTokens: () => ({ ...tokens }),
    setTokens: () => {},
    clearTokens: () => {},
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => cache.set(key, structuredClone(value)),
  };
}

function config() {
  return {
    traktClientId: 'client-id',
    traktClientSecret: 'client-secret',
    redirectUri: 'http://bridge.local/oauth/callback',
    userAgent: 'HomeDocker-Trakt-Bridge/rate-limit-test',
    pullMaxPages: 10,
    pullIdentityMode: 'trakt',
  };
}

function rateHeader(name, retryAfter = '2') {
  return {
    'retry-after': retryAfter,
    'x-ratelimit': JSON.stringify({
      name,
      period: 1,
      limit: 1,
      remaining: 0,
      until: new Date(Date.now() + Number(retryAfter) * 1000).toISOString(),
    }),
  };
}

test('Managed Trakt: authenticated writes are serialized at Trakt safe cadence', async (t) => {
  const starts = [];
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history' && req.method === 'POST') {
      starts.push(Date.now());
      return { status: 201, body: { added: { episodes: 1 } } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());
  await Promise.all([
    client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [{ ids: { trakt: 1 } }] } }),
    client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [{ ids: { trakt: 2 } }] } }),
  ]);

  assert.equal(starts.length, 2);
  assert.ok(starts[1] - starts[0] >= 1000, `writes were only ${starts[1] - starts[0]}ms apart`);
});

test('Managed Trakt: headerless GET 429 cools reads but keeps write recovery lane open', async (t) => {
  let getCalls = 0;
  let writeCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/last_activities') {
      getCalls += 1;
      return { status: 429, headers: { 'retry-after': '2' }, body: { error: 'rate_limited' } };
    }
    if (req.path === '/sync/history' && req.method === 'POST') {
      writeCalls += 1;
      return { status: 201, body: { added: { episodes: 1 } } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_429' && err.retryAfter === '2',
  );
  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_rate_cooldown',
  );

  const write = await client.request('profile-a', '/sync/history', {
    method: 'POST',
    body: { episodes: [] },
  });
  assert.deepEqual(write, { added: { episodes: 1 } });

  assert.equal(getCalls, 1);
  assert.equal(writeCalls, 1);
});

test('Managed Trakt: confirmed POST bucket 429 blocks writes but keeps GET pull lane open', async (t) => {
  let writeCalls = 0;
  let getCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history' && req.method === 'POST') {
      writeCalls += 1;
      return {
        status: 429,
        headers: rateHeader('AUTHED_API_POST_LIMIT', '2'),
        body: { error: 'rate_limited' },
      };
    }
    if (req.path === '/sync/last_activities') {
      getCalls += 1;
      return { status: 200, body: { all: 'ok' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [] } }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_429'
      && err.rateLimit?.name === 'AUTHED_API_POST_LIMIT',
  );

  const read = await client.request('profile-a', '/sync/last_activities');
  assert.deepEqual(read, { all: 'ok' });

  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [] } }),
    (err) => err instanceof BridgeError && err.code === 'trakt_rate_cooldown',
  );

  assert.equal(writeCalls, 1);
  assert.equal(getCalls, 1, 'write-only throttle must not unnecessarily block pull GETs');
});

test('Managed Trakt: headerless history 429 cools history but keeps recovery GET open', async (t) => {
  let writeCalls = 0;
  let getCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history' && req.method === 'POST') {
      writeCalls += 1;
      return { status: 429, headers: { 'retry-after': '2' }, body: { error: 'security_limit' } };
    }
    if (req.path === '/sync/last_activities') {
      getCalls += 1;
      return { status: 200, body: { all: 'ok' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());
  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [] } }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const read = await client.request('profile-a', '/sync/last_activities');
  assert.deepEqual(read, { all: 'ok' });

  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [] } }),
    (err) => err instanceof BridgeError && err.code === 'trakt_rate_cooldown',
  );

  assert.equal(writeCalls, 1);
  assert.equal(getCalls, 1);
});

test('Managed Trakt: independent headerless 429s can cool both lanes without pre-emptive sharing', async (t) => {
  let writeCalls = 0;
  let getCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history' && req.method === 'POST') {
      writeCalls += 1;
      return { status: 429, headers: { 'retry-after': '2' }, body: { error: 'rate_limited' } };
    }
    if (req.path === '/sync/last_activities') {
      getCalls += 1;
      return { status: 429, headers: { 'retry-after': '2' }, body: { error: 'rate_limited' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: {} }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );
  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  await assert.rejects(
    () => client.request('profile-a', '/sync/history', { method: 'POST', body: {} }),
    (err) => err instanceof BridgeError && err.code === 'trakt_rate_cooldown',
  );
  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_rate_cooldown',
  );

  assert.equal(writeCalls, 1);
  assert.equal(getCalls, 1);
});

test('Managed Trakt: rate cooldown survives client reconstruction through cache persistence', async (t) => {
  let calls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/last_activities') {
      calls += 1;
      return { status: 429, headers: { 'retry-after': '2' }, body: { error: 'rate_limited' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const db = tokenDb();
  const first = new ManagedTraktClient(config(), db);
  await assert.rejects(
    () => first.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const reconstructed = new ManagedTraktClient(config(), db);
  await assert.rejects(
    () => reconstructed.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_rate_cooldown'
      && Number(err.retryAfter) >= 1,
  );

  assert.equal(calls, 1);
});


test('Managed Trakt: headerless history 429 does not poison scrobble writes', async (t) => {
  let historyCalls = 0;
  let scrobbleCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history/remove' && req.method === 'POST') {
      historyCalls += 1;
      return {
        status: 429,
        headers: { 'retry-after': '10' },
        body: { error: 'security_limit' },
      };
    }
    if (req.path === '/scrobble/stop' && req.method === 'POST') {
      scrobbleCalls += 1;
      return { status: 201, body: { action: 'pause' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/history/remove', {
      method: 'POST',
      body: { shows: [] },
    }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const scrobble = await client.request('profile-a', '/scrobble/stop', {
    method: 'POST',
    body: { movie: { ids: { imdb: 'tt1234567' } }, progress: 20 },
  });
  assert.deepEqual(scrobble, { action: 'pause' });

  await assert.rejects(
    () => client.request('profile-a', '/sync/history/remove', {
      method: 'POST',
      body: { shows: [] },
    }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_rate_cooldown'
      && err.rateLimit?.name === 'LOCAL_HISTORY_COOLDOWN',
  );

  assert.equal(historyCalls, 1);
  assert.equal(scrobbleCalls, 1);
});

test('Managed Trakt: headerless scrobble 429 does not poison history writes', async (t) => {
  let scrobbleCalls = 0;
  let historyCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/scrobble/stop' && req.method === 'POST') {
      scrobbleCalls += 1;
      return {
        status: 429,
        headers: { 'retry-after': '10' },
        body: { error: 'security_limit' },
      };
    }
    if (req.path === '/sync/history' && req.method === 'POST') {
      historyCalls += 1;
      return { status: 201, body: { added: { episodes: 1 } } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/scrobble/stop', {
      method: 'POST',
      body: { movie: { ids: { imdb: 'tt1234567' } }, progress: 20 },
    }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const history = await client.request('profile-a', '/sync/history', {
    method: 'POST',
    body: { movies: [] },
  });
  assert.deepEqual(history, { added: { episodes: 1 } });

  await assert.rejects(
    () => client.request('profile-a', '/scrobble/stop', {
      method: 'POST',
      body: { movie: { ids: { imdb: 'tt1234567' } }, progress: 20 },
    }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_rate_cooldown'
      && err.rateLimit?.name === 'LOCAL_SCROBBLE_COOLDOWN',
  );

  assert.equal(scrobbleCalls, 1);
  assert.equal(historyCalls, 1);
});

test('Managed Trakt: explicit authenticated POST bucket still blocks every write family', async (t) => {
  let historyCalls = 0;
  let scrobbleCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history/remove' && req.method === 'POST') {
      historyCalls += 1;
      return {
        status: 429,
        headers: rateHeader('AUTHED_API_POST_LIMIT', '10'),
        body: { error: 'rate_limited' },
      };
    }
    if (req.path === '/scrobble/stop' && req.method === 'POST') {
      scrobbleCalls += 1;
      return { status: 201, body: { action: 'pause' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/history/remove', {
      method: 'POST',
      body: { shows: [] },
    }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_429'
      && err.rateLimit?.name === 'AUTHED_API_POST_LIMIT',
  );

  await assert.rejects(
    () => client.request('profile-a', '/scrobble/stop', {
      method: 'POST',
      body: { movie: { ids: { imdb: 'tt1234567' } }, progress: 20 },
    }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_rate_cooldown'
      && err.rateLimit?.name === 'LOCAL_SCROBBLE_COOLDOWN',
  );

  assert.equal(historyCalls, 1);
  assert.equal(scrobbleCalls, 0);
});

test('Managed Trakt: family-specific cooldown survives client reconstruction', async (t) => {
  let historyCalls = 0;
  let scrobbleCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history/remove' && req.method === 'POST') {
      historyCalls += 1;
      return {
        status: 429,
        headers: { 'retry-after': '10' },
        body: { error: 'security_limit' },
      };
    }
    if (req.path === '/scrobble/pause' && req.method === 'POST') {
      scrobbleCalls += 1;
      return { status: 201, body: { action: 'pause' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const db = tokenDb();
  const first = new ManagedTraktClient(config(), db);

  await assert.rejects(
    () => first.request('profile-a', '/sync/history/remove', {
      method: 'POST',
      body: { shows: [] },
    }),
    (err) => err instanceof BridgeError && err.code === 'trakt_429',
  );

  const reconstructed = new ManagedTraktClient(config(), db);

  await assert.rejects(
    () => reconstructed.request('profile-a', '/sync/history/remove', {
      method: 'POST',
      body: { shows: [] },
    }),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_rate_cooldown'
      && err.rateLimit?.name === 'LOCAL_HISTORY_COOLDOWN',
  );

  const scrobble = await reconstructed.request('profile-a', '/scrobble/pause', {
    method: 'POST',
    body: { movie: { ids: { imdb: 'tt1234567' } }, progress: 20 },
  });
  assert.deepEqual(scrobble, { action: 'pause' });

  assert.equal(historyCalls, 1);
  assert.equal(scrobbleCalls, 1);
});
