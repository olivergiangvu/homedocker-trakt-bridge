import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { BridgeDb } from '../../src/db.mjs';
import { TraktClient } from '../../src/trakt.mjs';
import { createServer } from '../../src/server.mjs';
import { deriveProfileKey } from '../../src/crypto.mjs';
import { learnShowAlias } from '../../src/identity-alias.mjs';
import { startJsonServer, redirectTraktFetch, listen, closeServer } from '../helpers/http-fixture.mjs';

function configFor(dataDir) {
  return {
    publicBaseUrl: 'http://bridge.local',
    redirectUri: 'http://bridge.local/oauth/callback',
    traktClientId: 'client-id',
    traktClientSecret: 'client-secret',
    bridgeSecret: 'integration-pull-secret-0123456789',
    adminKey: 'integration-admin-key-0123456789',
    dataDir,
    dbPath: join(dataDir, 'bridge.db'),
    port: 0,
    logLevel: 'error',
    displayTimeZone: 'UTC',
    pullTtlSeconds: 900,
    pullStaleIfErrorSeconds: 3600,
    pullMaxPages: 10,
    bulkSingleDedupeSeconds: 300,
    pullIdentityMode: 'trakt',
    userAgent: 'HomeDocker-Trakt-Bridge/integration',
  };
}

function page(body) {
  return { status: 200, headers: { 'x-pagination-page-count': '1' }, body };
}

function activities() {
  return {
    movies: {
      watched_at: '2026-10-01T08:00:00.000Z',
      watchlisted_at: '2026-10-01T08:10:00.000Z',
    },
    episodes: { watched_at: '2026-10-01T08:20:00.000Z' },
    shows: { watchlisted_at: '2026-10-01T08:30:00.000Z' },
  };
}

function episodePlayback() {
  return [{
    progress: 25.46,
    paused_at: '2026-10-01T09:00:00.000Z',
    show: {
      title: "Ok! Let's Get Divorced",
      year: 2023,
      ids: { trakt: 263102, imdb: 'tt44051354', tmdb: 276470, tvdb: 480791 },
    },
    episode: { season: 1, number: 7, runtime: 61 },
  }];
}

function watchedShow({ complete = true } = {}) {
  const row = {
    plays: 6,
    last_watched_at: '2026-10-01T08:20:00.000Z',
    show: {
      title: "Ok! Let's Get Divorced",
      year: 2023,
      aired_episodes: 10,
      ids: { trakt: 263102, imdb: 'tt44051354', tmdb: 276470, tvdb: 480791 },
    },
    next_episode: { season: 1, number: 7 },
  };
  if (complete) {
    row.seasons = [{
      number: 1,
      episodes: [1, 2, 3, 4, 5, 6].map((number) => ({
        number,
        plays: 1,
        last_watched_at: `2026-10-01T08:${String(number).padStart(2, '0')}:00.000Z`,
      })),
    }];
  }
  return row;
}

async function runtimeWithMock(t, handler, profileId) {
  const mock = await startJsonServer(handler);
  const restoreFetch = redirectTraktFetch(mock.baseUrl);
  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-pull-'));
  const config = configFor(dataDir);
  const db = new BridgeDb(config);
  db.createProfile(profileId, 'Pull integration');
  db.setTokens(profileId, {
    access_token: 'access-live',
    refresh_token: 'refresh-live',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000),
  });
  const trakt = new TraktClient(config, db);
  const server = createServer({ config, db, trakt });
  const baseUrl = await listen(server);
  const addonKey = deriveProfileKey(config.bridgeSecret, 'addon', profileId);
  const pullUrl = `${baseUrl}/u/${profileId}/${addonKey}/watch_state/pull.json`;

  t.after(async () => {
    await closeServer(server);
    db.close();
    restoreFetch();
    await mock.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  return { mock, dataDir, config, db, server, baseUrl, pullUrl };
}

test('Bridge pull: trakt mode preserves Trakt IMDb across playback, watched, next-up and watchlist', async (t) => {
  const profileId = 'cccccccccccccccccccccccc';
  const runtime = await runtimeWithMock(t, async (req) => {
    if (req.path === '/sync/last_activities') return { status: 200, body: activities() };
    if (req.path === '/sync/playback/movies') return page([]);
    if (req.path === '/sync/playback/episodes') return page(episodePlayback());
    if (req.path === '/sync/watched/movies') return page([]);
    if (req.path === '/sync/watched/shows') return page([watchedShow()]);
    if (req.path === '/sync/watchlist/movies/added/desc') return page([]);
    if (req.path === '/sync/watchlist/shows/added/desc') {
      return page([{
        listed_at: '2026-10-01T08:30:00.000Z',
        show: { ids: { trakt: 263102, imdb: 'tt44051354', tmdb: 276470, tvdb: 480791 } },
      }]);
    }
    return null;
  }, profileId);

  // Keep the historical regression evidence in the DB. In trakt mode this
  // evidence must never rewrite authoritative pull output.
  learnShowAlias(
    runtime.db,
    profileId,
    { trakt: 263102, imdb: 'tt44051354' },
    'tt44094505',
  );

  const response = await fetch(runtime.pullUrl);
  assert.equal(response.status, 200);
  const payload = await response.json();
  const serialized = JSON.stringify(payload);

  assert.equal(payload.items[0].metaId, 'tt44051354');
  assert.equal(payload.items[0].videoId, 'tt44051354:1:7');
  assert.deepEqual(payload.watched.episodes, [
    'tt44051354:1:1',
    'tt44051354:1:2',
    'tt44051354:1:3',
    'tt44051354:1:4',
    'tt44051354:1:5',
    'tt44051354:1:6',
  ]);
  assert.equal(payload.watched.nextUp[0].metaId, 'tt44051354');
  assert.equal(payload.watched.nextUp[0].videoId, 'tt44051354:1:7');
  assert.equal(payload.watchlist[0].metaId, 'tt44051354');
  assert.equal(serialized.includes('tt44094505'), false, 'learned AIOStreams alias must not leak into trakt-mode pull output');

  const unchanged = await fetch(`${runtime.pullUrl}?since=${encodeURIComponent(payload.version)}`);
  assert.equal(unchanged.status, 200);
  const unchangedPayload = await unchanged.json();
  assert.equal(Array.isArray(unchangedPayload.items), true);
  assert.equal('watched' in unchangedPayload, false);
  assert.equal('watchlist' in unchangedPayload, false);
});

test('Bridge pull: incomplete authoritative watched state fails closed and is not cached', async (t) => {
  const profileId = 'dddddddddddddddddddddddd';
  const runtime = await runtimeWithMock(t, async (req) => {
    if (req.path === '/sync/last_activities') return { status: 200, body: activities() };
    if (req.path === '/sync/playback/movies') return page([]);
    if (req.path === '/sync/playback/episodes') return page(episodePlayback());
    if (req.path === '/sync/watched/movies') return page([]);
    if (req.path === '/sync/watched/shows') return page([watchedShow({ complete: false })]);
    if (req.path === '/sync/watchlist/movies/added/desc') return page([]);
    if (req.path === '/sync/watchlist/shows/added/desc') return page([]);
    return null;
  }, profileId);

  const response = await fetch(runtime.pullUrl);
  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.error, 'trakt_state_incomplete');
  assert.equal(runtime.db.cacheGet(`pull-state:v5:${profileId}`), null, 'failed authoritative state must not enter pull cache');

  const events = runtime.db.recentEvents(profileId, 10);
  const failedPull = events.find((row) => row.event === 'pull' && row.status === 'error');
  assert.ok(failedPull);
  assert.match(failedPull.detail, /trakt_state_incomplete/);
});
