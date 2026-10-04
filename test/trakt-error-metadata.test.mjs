import test from 'node:test';
import assert from 'node:assert/strict';

import { TraktClient } from '../src/trakt.mjs';
import { BridgeError } from '../src/errors.mjs';
import { startJsonServer, redirectTraktFetch } from './helpers/http-fixture.mjs';

function tokenDb() {
  return {
    getTokens: () => ({
      accessToken: 'access-live',
      refreshToken: 'refresh-live',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
    }),
    setTokens: () => {},
  };
}

function config() {
  return {
    traktClientId: 'client-id',
    traktClientSecret: 'client-secret',
    redirectUri: 'http://bridge.local/oauth/callback',
    userAgent: 'HomeDocker-Trakt-Bridge/error-metadata-test',
  };
}

test('Trakt errors retain bounded safe detail and parsed rate-limit metadata', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/scrobble/pause' && req.method === 'POST') {
      return {
        status: 422,
        headers: {
          'retry-after': '7',
          'x-ratelimit': JSON.stringify({
            name: 'AUTHED_API_POST_LIMIT',
            period: 1,
            limit: 1,
            remaining: 0,
            until: '2026-10-04T10:30:00Z',
          }),
        },
        body: { error: 'no active scrobble' },
      };
    }
    return null;
  });

  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(config(), tokenDb());

  await assert.rejects(
    () => client.request('p1', '/scrobble/pause', {
      method: 'POST',
      body: { progress: 80, episode: { ids: { trakt: 73482 } } },
    }),
    (err) => {
      assert.ok(err instanceof BridgeError);
      assert.equal(err.code, 'trakt_422');
      assert.equal(err.upstreamStatus, 422);
      assert.equal(err.upstreamDetail, 'no active scrobble');
      assert.equal(err.retryAfter, '7');
      assert.equal(err.rateLimit?.name, 'AUTHED_API_POST_LIMIT');
      assert.equal(err.rateLimit?.remaining, 0);
      return true;
    },
  );
});
