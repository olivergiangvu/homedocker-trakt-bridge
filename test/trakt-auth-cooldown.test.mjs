import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';

function fakeDb() {
  const cache = new Map();
  return {
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => { cache.set(key, value); },
    getTokens: () => ({
      accessToken: 'test-access-token',
      refreshToken: 'test-refresh-token',
      expiresAt: Math.floor(Date.now() / 1000) + 86400,
    }),
  };
}

const config = {
  traktClientId: 'test-client',
  traktClientSecret: 'test-secret',
  userAgent: 'RC6-Test/1',
  traktAuthCooldownSeconds: 30,
};

test('authenticated Trakt 429 arms per-profile cooldown and respects Retry-After', async () => {
  const originalFetch = globalThis.fetch;
  let sent = 0;
  globalThis.fetch = async () => {
    sent++;
    if (sent === 1) {
      return new Response(JSON.stringify({ error: 'rate limited' }), {
        status: 429,
        headers: { 'retry-after': '25', 'content-type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ all: 'good' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const client = new TraktClient(config, fakeDb());
    await assert.rejects(
      client.request('profile-A', '/sync/last_activities'),
      (err) => err.status === 429 && err.code === 'trakt_429',
    );
    assert.equal(sent, 1);
    await assert.rejects(
      client.request('profile-A', '/sync/last_activities'),
      (err) => err.status === 429
        && err.code === 'trakt_rate_cooldown'
        && Number(err.retryAfter) > 0,
    );
    assert.equal(sent, 1, 'no second upstream call during cooldown');
    assert.deepEqual(await client.request('profile-B', '/sync/last_activities'), { all: 'good' });
    assert.equal(sent, 2, 'other user profile is independent');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Trakt 422 never arms authenticated cooldown or changes scrobble semantics', async () => {
  const originalFetch = globalThis.fetch;
  let sent = 0;
  globalThis.fetch = async () => {
    sent++;
    return new Response(JSON.stringify({ error: 'Progress is 99.982%. Use stop to scrobble.' }), {
      status: sent === 1 ? 422 : 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const client = new TraktClient(config, fakeDb());
    await assert.rejects(
      client.request('profile-A', '/scrobble/pause', { method: 'POST', body: { progress: 99.982 } }),
      (err) => err.status === 422 && err.code === 'trakt_422',
    );
    await client.request('profile-A', '/scrobble/start', { method: 'POST', body: { progress: 50 } });
    assert.equal(sent, 2, 'unprocessable response is not a rate limit');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
