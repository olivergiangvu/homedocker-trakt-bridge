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
  return {
    getTokens: () => ({ ...tokens }),
    setTokens: () => {},
    clearTokens: () => {},
    cacheGet: () => null,
    cacheSet: () => {},
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

test('Managed Trakt: GET 429 arms a profile read cooldown before another upstream call', async (t) => {
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

  const client = new ManagedTraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError && err.code === 'trakt_429' && err.retryAfter === '2',
  );

  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError
      && err.code === 'trakt_read_cooldown'
      && Number(err.retryAfter) >= 1,
  );

  assert.equal(calls, 1, 'cooldown must prevent the second Trakt GET entirely');
});

test('Managed Trakt: write 429 extends the local write gate using Retry-After', async (t) => {
  const starts = [];
  let calls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/history' && req.method === 'POST') {
      calls += 1;
      starts.push(Date.now());
      if (calls === 1) return { status: 429, headers: { 'retry-after': '1' }, body: { error: 'rate_limited' } };
      return { status: 201, body: { added: { episodes: 1 } } };
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

  await client.request('profile-a', '/sync/history', { method: 'POST', body: { episodes: [] } });

  assert.equal(starts.length, 2);
  assert.ok(starts[1] - starts[0] >= 950, `Retry-After gate lasted only ${starts[1] - starts[0]}ms`);
});
