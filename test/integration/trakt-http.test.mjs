import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { TraktClient } from '../../src/trakt.mjs';
import { BridgeDb } from '../../src/db.mjs';
import { createServer } from '../../src/server.mjs';
import { deriveProfileKey } from '../../src/crypto.mjs';
import { BridgeError } from '../../src/errors.mjs';
import { startJsonServer, redirectTraktFetch, listen, closeServer } from '../helpers/http-fixture.mjs';

function tokenDb() {
  let tokens = {
    accessToken: 'access-old',
    refreshToken: 'refresh-old',
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
  };
  const cache = new Map();
  return {
    getTokens: () => ({ ...tokens }),
    setTokens: (_profileId, payload) => {
      const now = Math.floor(Date.now() / 1000);
      const createdAt = Number(payload.created_at || now);
      tokens = {
        accessToken: payload.access_token,
        refreshToken: payload.refresh_token,
        expiresAt: createdAt + Number(payload.expires_in || 3600),
      };
    },
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => cache.set(key, structuredClone(value)),
  };
}

function clientConfig() {
  return {
    traktClientId: 'client-id',
    traktClientSecret: 'client-secret',
    redirectUri: 'http://bridge.local/oauth/callback',
    userAgent: 'HomeDocker-Trakt-Bridge/integration',
    pullMaxPages: 10,
    pullIdentityMode: 'trakt',
  };
}

function bridgeConfig(dataDir) {
  return {
    ...clientConfig(),
    publicBaseUrl: 'http://bridge.local',
    bridgeSecret: 'integration-bridge-secret-0123456789',
    adminKey: 'integration-admin-key-0123456789',
    dataDir,
    dbPath: join(dataDir, 'bridge.db'),
    port: 0,
    logLevel: 'error',
    displayTimeZone: 'UTC',
    pullTtlSeconds: 900,
    pullStaleIfErrorSeconds: 3600,
    bulkSingleDedupeSeconds: 300,
  };
}

async function postJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function openBridge(config, db) {
  const trakt = new TraktClient(config, db);
  const server = createServer({ config, db, trakt });
  const baseUrl = await listen(server);
  return { trakt, server, baseUrl };
}

function episodePlayedEvent(id = 'e|tt44051354:1:7|played|1') {
  return {
    id,
    event: 'played',
    scope: 'episode',
    metaId: 'tt44051354',
    videoId: 'tt44051354:1:7',
    season: 1,
    episode: 7,
    at: 1790840000,
    played: true,
  };
}

function pushUrl(baseUrl, config, profileId) {
  const addonKey = deriveProfileKey(config.bridgeSecret, 'addon', profileId);
  return `${baseUrl}/u/${profileId}/${addonKey}/watch_state/push/series/tt44051354.json`;
}

test('Trakt HTTP: 401 refreshes once and retries with the new access token', async (t) => {
  let apiCalls = 0;
  let refreshCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/oauth/token' && req.method === 'POST') {
      refreshCalls += 1;
      assert.equal(req.json.grant_type, 'refresh_token');
      assert.equal(req.json.refresh_token, 'refresh-old');
      return {
        status: 200,
        body: {
          access_token: 'access-new',
          refresh_token: 'refresh-new',
          expires_in: 3600,
          created_at: Math.floor(Date.now() / 1000),
        },
      };
    }
    if (req.path === '/sync/last_activities') {
      apiCalls += 1;
      if (req.headers.authorization === 'Bearer access-old') return { status: 401, body: { error: 'expired' } };
      assert.equal(req.headers.authorization, 'Bearer access-new');
      return { status: 200, body: { all: 'ok' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const db = tokenDb();
  const client = new TraktClient(clientConfig(), db);
  const result = await client.request('profile-a', '/sync/last_activities');

  assert.deepEqual(result, { all: 'ok' });
  assert.equal(apiCalls, 2);
  assert.equal(refreshCalls, 1);
  assert.equal(db.getTokens('profile-a').accessToken, 'access-new');
});

test('Trakt HTTP: 429 preserves retry-after and upstream path', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/last_activities') {
      return { status: 429, headers: { 'retry-after': '17' }, body: { error: 'rate_limited' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb());
  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError
      && err.status === 429
      && err.code === 'trakt_429'
      && err.retryAfter === '17'
      && err.upstreamPath === '/sync/last_activities',
  );
});

test('Trakt HTTP: pagination follows the upstream page count and keeps order', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path !== '/sync/watched/movies') return null;
    const params = new URLSearchParams(req.search);
    const page = Number(params.get('page'));
    assert.equal(params.get('limit'), '2');
    if (page === 1) return { status: 200, headers: { 'x-pagination-page-count': '2' }, body: [{ id: 1 }, { id: 2 }] };
    if (page === 2) return { status: 200, headers: { 'x-pagination-page-count': '2' }, body: [{ id: 3 }] };
    return { status: 500, body: { error: 'unexpected_page' } };
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb());
  const rows = await client.requestAllPages('profile-a', '/sync/watched/movies', { limit: 2, maxPages: 5 });
  assert.deepEqual(rows, [{ id: 1 }, { id: 2 }, { id: 3 }]);
  assert.equal(mock.requests.filter((r) => r.path === '/sync/watched/movies').length, 2);
});

test('Bridge HTTP: successful push is idempotent across duplicate delivery and restart', async (t) => {
  let historyCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/search/imdb/tt44051354') {
      assert.equal(req.search, '?type=show');
      return { status: 200, body: [{ type: 'show', show: { ids: { trakt: 263102, imdb: 'tt44051354', tmdb: 276470, tvdb: 480791 } } }] };
    }
    if (req.path === '/shows/263102/seasons/1/episodes/7') {
      return { status: 200, body: { ids: { trakt: 900007, tvdb: 100007 } } };
    }
    if (req.path === '/sync/history' && req.method === 'POST') {
      historyCalls += 1;
      assert.equal(req.headers.authorization, 'Bearer access-live');
      assert.equal(req.json.episodes[0].ids.trakt, 900007);
      return { status: 201, body: { added: { episodes: 1 } } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-integration-'));
  const config = bridgeConfig(dataDir);
  const profileId = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  let db = new BridgeDb(config);
  db.createProfile(profileId, 'Integration');
  db.setTokens(profileId, {
    access_token: 'access-live',
    refresh_token: 'refresh-live',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000),
  });
  let runtime = await openBridge(config, db);

  t.after(async () => {
    await closeServer(runtime.server);
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  const body = episodePlayedEvent();
  let response = await postJson(pushUrl(runtime.baseUrl, config, profileId), body);
  assert.equal(response.status, 204);
  response = await postJson(pushUrl(runtime.baseUrl, config, profileId), body);
  assert.equal(response.status, 204);
  assert.equal(historyCalls, 1, 'duplicate event must not write history twice');
  assert.equal(db.isProcessed(profileId, body.id), true);

  await closeServer(runtime.server);
  db.close();

  db = new BridgeDb(config);
  runtime = await openBridge(config, db);
  response = await postJson(pushUrl(runtime.baseUrl, config, profileId), body);
  assert.equal(response.status, 204);
  assert.equal(historyCalls, 1, 'processed event must remain idempotent after restart');
});

test('Bridge HTTP: a retryable 429 is not marked processed and the same event can recover', async (t) => {
  let historyCalls = 0;
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/search/imdb/tt44051354') {
      return { status: 200, body: [{ type: 'show', show: { ids: { trakt: 263102, imdb: 'tt44051354' } } }] };
    }
    if (req.path === '/shows/263102/seasons/1/episodes/7') {
      return { status: 200, body: { ids: { trakt: 900007 } } };
    }
    if (req.path === '/sync/history' && req.method === 'POST') {
      historyCalls += 1;
      if (historyCalls === 1) return { status: 429, headers: { 'retry-after': '1' }, body: { error: 'rate_limited' } };
      return { status: 201, body: { added: { episodes: 1 } } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-retry-'));
  const config = bridgeConfig(dataDir);
  const profileId = 'bbbbbbbbbbbbbbbbbbbbbbbb';
  const db = new BridgeDb(config);
  db.createProfile(profileId, 'Retry integration');
  db.setTokens(profileId, {
    access_token: 'access-live',
    refresh_token: 'refresh-live',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000),
  });
  const runtime = await openBridge(config, db);
  t.after(async () => {
    await closeServer(runtime.server);
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  const body = episodePlayedEvent('e|tt44051354:1:7|played|retry');
  let response = await postJson(pushUrl(runtime.baseUrl, config, profileId), body);
  assert.equal(response.status, 429);
  assert.equal(db.isProcessed(profileId, body.id), false, 'failed upstream mutation must remain retryable');

  response = await postJson(pushUrl(runtime.baseUrl, config, profileId), body);
  assert.equal(response.status, 204);
  assert.equal(historyCalls, 2);
  assert.equal(db.isProcessed(profileId, body.id), true);

  const events = db.recentEvents(profileId, 10).filter((row) => row.event_id === body.id);
  assert.deepEqual(events.map((row) => row.status).sort(), ['error', 'ok']);
});
