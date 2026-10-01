import test from 'node:test';
import assert from 'node:assert/strict';
import { buildProfileOperationalStatus, buildReadiness, filterOperationalEvents } from '../src/operational-status.mjs';

function fakeDb() {
  return {
    ping: () => true,
    schemaVersion: () => 1,
    countConnectedProfiles: () => 1,
    getProfile: () => ({ id: 'p1', name: 'Oliver Trakt', access_token_enc: 'enc', connected_at: 100 }),
    recentEvents: () => [
      { event_id: 'pull|initial', event: 'pull', status: 'ok', detail: JSON.stringify({ items: 97, watchedMovies: 281, watchedEpisodes: 8604, watchlistItems: 81 }), created_at: 300 },
      { event_id: 'e|x|stop|0', event: 'stop', status: 'ignored', detail: JSON.stringify({ ignored: 'progress_below_trakt_minimum' }), created_at: 250 },
      { event_id: 'e|y|pause|50', event: 'pause', status: 'ok', detail: JSON.stringify({ action: 'scrobble:pause' }), created_at: 200 },
      { event_id: 'e|z|stop|50', event: 'stop', status: 'error', detail: 'trakt_500:test', created_at: 150 },
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

test('operational status exposes authority and effective Trakt identity', () => {
  const status = buildProfileOperationalStatus({
    db: fakeDb(),
    config: { pullIdentityMode: 'trakt' },
    profileId: 'p1',
  });

  assert.equal(status.authority.history, 'trakt');
  assert.equal(status.authority.aiometadataReadModeRecommended, 'this_server_only');
  assert.equal(status.sync.lastPull.items, 97);
  assert.equal(status.identity.aliasCount, 1);
  assert.equal(status.identity.aliases[0].effectivePullMetaId, 'tt44051354');
  assert.equal(status.errors.unresolved, 1);
  assert.equal(status.errors.ignored, 1);
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
