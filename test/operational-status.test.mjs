import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ACTIVE_ERROR_WINDOW_SECONDS,
  buildProfileOperationalStatus,
  buildReadiness,
  filterOperationalEvents,
  summarizeEventDetail,
} from '../src/operational-status.mjs';

function fakeDb() {
  return {
    now: () => 400,
    ping: () => true,
    schemaVersion: () => 1,
    countConnectedProfiles: () => 1,
    getProfile: () => ({ id: 'p1', name: 'Oliver Trakt', access_token_enc: 'enc', connected_at: 100 }),
    recentEvents: () => [
      { event_id: 'pull|v1', event: 'pull', status: 'cached', detail: JSON.stringify({ version: 'v1', items: 97, watchedMovies: null, watchedEpisodes: null, watchlistItems: null, source: 'cache', ageSeconds: 90 }), created_at: 350 },
      { event_id: 'pull|initial', event: 'pull', status: 'ok', detail: JSON.stringify({ version: 'v1', items: 97, watchedMovies: 281, watchedEpisodes: 8604, watchedChanged: true, watchlistItems: 81, watchlistChanged: true, source: 'trakt' }), created_at: 300 },
      { event_id: 'e|x|stop|0', event: 'stop', status: 'ignored', detail: JSON.stringify({ ignored: 'progress_below_trakt_minimum' }), created_at: 250 },
      { event_id: 'e|y|pause|50', event: 'pause', status: 'ok', detail: JSON.stringify({ action: 'scrobble:pause' }), created_at: 200 },
      { event_id: 'e|z|stop|50', event: 'stop', status: 'error', detail: 'trakt_500:test endpoint=/scrobble/pause', created_at: 150 },
    ],
    cacheGet: (key) => key.startsWith('identity-alias:v1:') ? {
      revision: 2,
      shows: {
        123: {
          preferredMetaId: 'tt44094505',
          traktImdb: 'tt44051354',
          updatedAt: 1234,
        },
      },
    } : null,
  };
}

test('readiness reports database, schema and connected profiles', () => {
  const status = buildReadiness({ db: fakeDb() });
  assert.equal(status.ready, true);
  assert.equal(status.status, 'ready');
  assert.equal(status.schemaVersion, 1);
  assert.equal(status.connectedProfiles, 1);
});

test('operational status separates latest poll from last authoritative sync', () => {
  const status = buildProfileOperationalStatus({
    db: fakeDb(),
    config: { pullIdentityMode: 'trakt' },
    profileId: 'p1',
  });

  assert.equal(status.authority.history, 'trakt');
  assert.equal(status.authority.aiometadataReadModeRecommended, 'this_server_only');
  assert.equal(status.sync.lastPull.items, 97);
  assert.equal(status.sync.lastPull.source, 'cache');
  assert.equal(status.sync.lastAuthoritativePull.watchedMovies, 281);
  assert.equal(status.sync.lastAuthoritativePull.watchedEpisodes, 8604);
  assert.equal(status.sync.lastAuthoritativePull.watchlistItems, 81);
  assert.equal(status.identity.aliasCount, 1);
  assert.equal(status.identity.aliases[0].effectivePullMetaId, 'tt44051354');
  assert.equal(status.errors.active, 1);
  assert.equal(status.errors.unresolved, 1);
  assert.equal(status.errors.ignored, 1);
});

test('old unretried errors remain historical but stop affecting active health', () => {
  const db = fakeDb();
  db.now = () => 150 + ACTIVE_ERROR_WINDOW_SECONDS + 1;
  const status = buildProfileOperationalStatus({
    db,
    config: { pullIdentityMode: 'trakt' },
    profileId: 'p1',
  });
  assert.equal(status.errors.active, 0);
  assert.equal(status.errors.unresolved, 0);
  assert.equal(status.errors.historical, 1);
});

test('event detail summaries are compact operator-facing strings', () => {
  assert.equal(
    summarizeEventDetail({ event: 'played', detail: JSON.stringify({ action: 'history:add' }) }),
    'History added',
  );
  assert.equal(
    summarizeEventDetail({ event: 'stop', detail: JSON.stringify({ ignored: 'progress_below_trakt_minimum' }) }),
    'Below 1% · ignored locally',
  );
  assert.equal(
    summarizeEventDetail({ event: 'pull', detail: JSON.stringify({ source: 'cache', items: 97, ageSeconds: 50 }) }),
    'Cache · 97 items · age 50s',
  );
});

test('operational event filters keep the expected categories', () => {
  const rows = [
    { event: 'pull', status: 'ok', displayStatus: 'ok', hadError: false },
    { event: 'pause', status: 'error', displayStatus: 'retrying', hadError: true },
    { event: 'stop', status: 'ignored', displayStatus: 'ignored', hadError: false },
  ];
  assert.equal(filterOperationalEvents(rows, 'pull').length, 1);
  assert.equal(filterOperationalEvents(rows, 'errors').length, 1);
  assert.equal(filterOperationalEvents(rows, 'ignored').length, 1);
  assert.equal(filterOperationalEvents(rows, 'playback').length, 2);
});
