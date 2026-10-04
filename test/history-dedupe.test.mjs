import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';
import {
  recentEquivalentHistoryState,
  rememberHistoryState,
} from '../src/history-dedupe.mjs';

function fakeDb() {
  const rows = new Map();
  return {
    rows,
    cacheSet(key, value) { rows.set(key, structuredClone(value)); },
    cacheGet(key) { return rows.get(key) ?? null; },
  };
}

const episodeA = {
  kind: 'episode',
  episode: { ids: { trakt: 73482 } },
  show: { ids: { trakt: 1388 } },
};
const episodeB = {
  kind: 'episode',
  episode: { ids: { trakt: 73483 } },
  show: { ids: { trakt: 1388 } },
};

test('same canonical media and same state are deduped inside the window', () => {
  const db = fakeDb();
  rememberHistoryState(db, 'p1', episodeA, { id: 'first', at: 1000 }, {
    state: 'played',
    source: 'history',
    ttlSeconds: 300,
  });
  const hit = recentEquivalentHistoryState(
    db, 'p1', episodeA, { id: 'second', at: 1140 }, 'played', 300,
  );
  assert.equal(hit.eventId, 'first');
  assert.equal(hit.source, 'history');
  assert.equal(hit.distanceSeconds, 140);
});

test('opposite state, another item, and an event outside the window are not deduped', () => {
  const db = fakeDb();
  rememberHistoryState(db, 'p1', episodeA, { id: 'first', at: 1000 }, {
    state: 'played',
    source: 'history',
    ttlSeconds: 300,
  });
  assert.equal(recentEquivalentHistoryState(db, 'p1', episodeA, { at: 1100 }, 'unplayed', 300), null);
  assert.equal(recentEquivalentHistoryState(db, 'p1', episodeB, { at: 1100 }, 'played', 300), null);
  assert.equal(recentEquivalentHistoryState(db, 'p1', episodeA, { at: 1301 }, 'played', 300), null);
});

function fakeClient(scrobbleAction = 'scrobble') {
  const db = fakeDb();
  const client = new TraktClient({
    historyDedupeSeconds: 300,
  }, db);
  const calls = [];
  client.resolveMedia = async () => ({
    kind: 'movie',
    movie: { title: 'Test', year: 2026, ids: { trakt: 101 } },
  });
  client.request = async (_profileId, path, options) => {
    calls.push({ path, options: structuredClone(options) });
    if (path === '/scrobble/stop') return { action: scrobbleAction };
    return {};
  };
  return { client, db, calls };
}

test('successful Trakt stop scrobble suppresses the following explicit played history add', async () => {
  const { client, calls } = fakeClient('scrobble');

  const stop = await client.applyEvent('p1', {
    id: 'stop-1',
    event: 'stop',
    at: 1000,
    played: true,
  }, {
    kind: 'scrobble',
    action: 'stop',
    progress: 95,
  });
  assert.equal(stop.action, 'scrobble:stop');

  const played = await client.applyEvent('p1', {
    id: 'played-1',
    event: 'played',
    at: 1005,
  }, { kind: 'history-add' });

  assert.equal(played.ignored, 'recent_history_equivalent');
  assert.equal(played.duplicateSource, 'scrobble-stop');
  assert.deepEqual(calls.map((x) => x.path), ['/scrobble/stop']);
});

test('a stop treated by Trakt as pause does not suppress a later explicit played mark', async () => {
  const { client, calls } = fakeClient('pause');

  await client.applyEvent('p1', {
    id: 'stop-1',
    event: 'stop',
    at: 1000,
    played: true,
  }, {
    kind: 'scrobble',
    action: 'stop',
    progress: 75,
  });

  const played = await client.applyEvent('p1', {
    id: 'played-1',
    event: 'played',
    at: 1005,
  }, { kind: 'history-add' });

  assert.equal(played.action, 'history:add');
  assert.deepEqual(calls.map((x) => x.path), ['/scrobble/stop', '/sync/history']);
});

test('repeated played marks are suppressed but an unplayed transition re-arms played', async () => {
  const { client, calls } = fakeClient();

  const first = await client.applyEvent('p1', {
    id: 'played-1',
    event: 'played',
    at: 1000,
  }, { kind: 'history-add' });
  assert.equal(first.action, 'history:add');

  const duplicate = await client.applyEvent('p1', {
    id: 'played-2',
    event: 'played',
    at: 1140,
  }, { kind: 'history-add' });
  assert.equal(duplicate.ignored, 'recent_history_equivalent');

  const unplayed = await client.applyEvent('p1', {
    id: 'unplayed-1',
    event: 'unplayed',
    at: 1150,
  }, { kind: 'history-remove' });
  assert.equal(unplayed.action, 'history:remove');

  const replayed = await client.applyEvent('p1', {
    id: 'played-3',
    event: 'played',
    at: 1160,
  }, { kind: 'history-add' });
  assert.equal(replayed.action, 'history:add');

  assert.deepEqual(calls.map((x) => x.path), [
    '/sync/history',
    '/sync/history/remove',
    '/sync/history',
  ]);
});
