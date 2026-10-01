import test from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { TraktClient } from '../../src/trakt.mjs';
import { BridgeDb } from '../../src/db.mjs';
import { createServer } from '../../src/server.mjs';
import { deriveProfileKey } from '../../src/crypto.mjs';
import { BridgeError } from '../../src/errors.mjs';
import { startJsonServer, redirectTraktFetch, listen, closeServer } from '../helpers/http-fixture.mjs';

function tokenDb({ expiresAt = Math.floor(Date.now() / 1000) + 3600 } = {}) {
  let tokens = {
    accessToken: 'access-live',
    refreshToken: 'refresh-live',
    expiresAt,
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
    userAgent: 'HomeDocker-Trakt-Bridge/recovery-matrix',
    pullMaxPages: 10,
    pullIdentityMode: 'trakt',
  };
}

function bridgeConfig(dataDir, overrides = {}) {
  return {
    ...clientConfig(),
    publicBaseUrl: 'http://bridge.local',
    bridgeSecret: 'recovery-bridge-secret-0123456789',
    adminKey: 'recovery-admin-key-0123456789',
    dataDir,
    dbPath: join(dataDir, 'bridge.db'),
    port: 0,
    logLevel: 'error',
    displayTimeZone: 'UTC',
    pullTtlSeconds: 0,
    pullStaleIfErrorSeconds: 3600,
    bulkSingleDedupeSeconds: 300,
    ...overrides,
  };
}

async function openBridge(config, db, trakt = new TraktClient(config, db)) {
  const server = createServer({ config, db, trakt });
  const baseUrl = await listen(server);
  return { server, baseUrl, trakt };
}

function connectProfile(db, profileId) {
  db.createProfile(profileId, 'Recovery integration');
  db.setTokens(profileId, {
    access_token: 'access-live',
    refresh_token: 'refresh-live',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000),
  });
}

function pullUrl(baseUrl, config, profileId, since = null) {
  const addonKey = deriveProfileKey(config.bridgeSecret, 'addon', profileId);
  const url = new URL(`${baseUrl}/u/${profileId}/${addonKey}/watch_state/pull.json`);
  if (since) url.searchParams.set('since', since);
  return url.toString();
}

function authoritativeFixture({ fail = null, delayMs = 0 } = {}) {
  return async (req) => {
    if (req.path === '/sync/last_activities') {
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (fail) {
        return {
          status: fail,
          headers: fail === 429 ? { 'retry-after': '7' } : {},
          body: { error: fail === 429 ? 'rate_limited' : 'upstream_failure' },
        };
      }
      return {
        status: 200,
        body: {
          movies: { watched_at: '2026-10-01T00:00:00.000Z', watchlisted_at: '2026-10-01T00:00:00.000Z' },
          episodes: { watched_at: '2026-10-01T00:00:00.000Z' },
          shows: { watchlisted_at: '2026-10-01T00:00:00.000Z' },
        },
      };
    }
    if (req.path === '/sync/playback/movies') return { status: 200, body: [] };
    if (req.path === '/sync/playback/episodes') return { status: 200, body: [] };
    if (req.path === '/sync/watched/movies') return { status: 200, body: [] };
    if (req.path === '/sync/watched/shows') return { status: 200, body: [] };
    if (req.path === '/sync/watchlist/movies/added/desc') return { status: 200, body: [] };
    if (req.path === '/sync/watchlist/shows/added/desc') return { status: 200, body: [] };
    return null;
  };
}

test('OAuth recovery: invalid_grant is surfaced as reconnect_required', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/oauth/token' && req.method === 'POST') {
      assert.equal(req.json.grant_type, 'refresh_token');
      return { status: 400, body: { error: 'invalid_grant' } };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb({ expiresAt: 0 }));
  await assert.rejects(
    () => client.request('profile-a', '/sync/last_activities'),
    (err) => err instanceof BridgeError
      && err.status === 401
      && err.code === 'reconnect_required'
      && err.upstreamPath === '/oauth/token',
  );
});

test('Identity recovery: stale IMDb 404 falls through to TMDB', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/search/imdb/tt-stale') return { status: 404, body: { error: 'not_found' } };
    if (req.path === '/search/tmdb/276470') {
      return {
        status: 200,
        body: [{ type: 'show', show: { ids: { trakt: 263102, tmdb: 276470, tvdb: 480791 } } }],
      };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb());
  const row = await client.lookupExternal({ imdb: 'tt-stale', tmdb: 276470 }, 'show');
  assert.equal(row.show.ids.trakt, 263102);
  assert.deepEqual(
    mock.requests.filter((r) => r.path.startsWith('/search/')).map((r) => r.path),
    ['/search/imdb/tt-stale', '/search/tmdb/276470'],
  );
});

test('Deterministic upstream 420 and 422 remain non-5xx Bridge errors', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/watchlist') return { status: 420, body: { error: 'limit_exceeded' } };
    if (req.path === '/scrobble/pause') return { status: 422, body: { error: 'invalid_progress' } };
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb());
  await assert.rejects(
    () => client.request('profile-a', '/sync/watchlist', { method: 'POST', body: { shows: [] } }),
    (err) => err instanceof BridgeError && err.status === 422 && err.code === 'trakt_420',
  );
  await assert.rejects(
    () => client.request('profile-a', '/scrobble/pause', { method: 'POST', body: { progress: 0 } }),
    (err) => err instanceof BridgeError && err.status === 422 && err.code === 'trakt_422',
  );
});

test('Pagination safety cap fails closed before requesting excess pages', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/watched/movies') {
      return {
        status: 200,
        headers: { 'x-pagination-page-count': '99' },
        body: [{ id: 1 }],
      };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const client = new TraktClient(clientConfig(), tokenDb());
  await assert.rejects(
    () => client.requestAllPages('profile-a', '/sync/watched/movies', { limit: 2, maxPages: 3 }),
    (err) => err instanceof BridgeError && err.status === 502 && err.code === 'trakt_pagination_limit',
  );
  assert.equal(mock.requests.filter((r) => r.path === '/sync/watched/movies').length, 1);
});

test('Pull recovery: 429 and 5xx serve matching bounded stale state', async (t) => {
  let fail = null;
  const mock = await startJsonServer((req) => authoritativeFixture({ fail })(req));
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-stale-'));
  const config = bridgeConfig(dataDir);
  const profileId = 'cccccccccccccccccccccccc';
  const db = new BridgeDb(config);
  connectProfile(db, profileId);
  const runtime = await openBridge(config, db);
  t.after(async () => {
    await closeServer(runtime.server);
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  let response = await fetch(pullUrl(runtime.baseUrl, config, profileId));
  assert.equal(response.status, 200);
  const initial = await response.json();
  assert.ok(initial.version);
  assert.ok(initial.watched);
  assert.ok(Array.isArray(initial.watchlist));

  for (const upstreamStatus of [429, 500]) {
    fail = upstreamStatus;
    response = await fetch(pullUrl(runtime.baseUrl, config, profileId, initial.version));
    assert.equal(response.status, 200, `stale cache should cover upstream ${upstreamStatus}`);
    const stale = await response.json();
    assert.equal(stale.version, initial.version);
    assert.deepEqual(stale.items, initial.items);
    assert.equal(stale.watched, undefined);
    assert.equal(stale.watchlist, undefined);
  }

  const staleEvents = db.recentEvents(profileId, 10).filter((row) => row.status === 'stale');
  assert.equal(staleEvents.length, 2);
});

test('Pull recovery: stale window zero fails closed on upstream 5xx', async (t) => {
  let fail = null;
  const mock = await startJsonServer((req) => authoritativeFixture({ fail })(req));
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-stale-zero-'));
  const config = bridgeConfig(dataDir, { pullStaleIfErrorSeconds: 0 });
  const profileId = 'dddddddddddddddddddddddd';
  const db = new BridgeDb(config);
  connectProfile(db, profileId);
  const runtime = await openBridge(config, db);
  t.after(async () => {
    await closeServer(runtime.server);
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  let response = await fetch(pullUrl(runtime.baseUrl, config, profileId));
  const initial = await response.json();
  assert.equal(response.status, 200);

  fail = 500;
  response = await fetch(pullUrl(runtime.baseUrl, config, profileId, initial.version));
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, 'trakt_500');
});

test('Pull recovery: concurrent identical pulls are coalesced into one Trakt task', async (t) => {
  let activityCalls = 0;
  const fixture = authoritativeFixture({ delayMs: 60 });
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/sync/last_activities') activityCalls += 1;
    return fixture(req);
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  t.after(async () => { restoreFetch(); await mock.close(); });

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-coalesce-'));
  const config = bridgeConfig(dataDir);
  const profileId = 'eeeeeeeeeeeeeeeeeeeeeeee';
  const db = new BridgeDb(config);
  connectProfile(db, profileId);
  const runtime = await openBridge(config, db);
  t.after(async () => {
    await closeServer(runtime.server);
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  const url = pullUrl(runtime.baseUrl, config, profileId);
  const [a, b] = await Promise.all([fetch(url), fetch(url)]);
  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  const [pa, pb] = await Promise.all([a.json(), b.json()]);
  assert.equal(pa.version, pb.version);
  assert.equal(activityCalls, 1);
});

test('Database recovery: closed SQLite backup restores profile, tokens, idempotency and cache', () => {
  const dir = mkdtempSync(join(tmpdir(), 'trakt-bridge-restore-'));
  const config = bridgeConfig(dir);
  const profileId = 'ffffffffffffffffffffffff';
  const backupPath = join(dir, 'bridge.backup.db');

  let db = new BridgeDb(config);
  connectProfile(db, profileId);
  db.markProcessed(profileId, 'event-1', JSON.stringify({ action: 'history:add' }));
  db.cacheSet('restore-check', { ok: true }, 3600);
  db.close();

  copyFileSync(config.dbPath, backupPath);
  rmSync(config.dbPath, { force: true });
  copyFileSync(backupPath, config.dbPath);

  db = new BridgeDb(config);
  try {
    assert.equal(db.schemaVersion(), 1);
    assert.equal(db.getProfile(profileId)?.name, 'Recovery integration');
    assert.equal(db.getTokens(profileId)?.accessToken, 'access-live');
    assert.equal(db.isProcessed(profileId, 'event-1'), true);
    assert.deepEqual(db.cacheGet('restore-check'), { ok: true });
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
