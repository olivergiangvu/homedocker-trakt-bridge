import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';

function mockDb() {
  const cache = new Map();
  return {
    cacheGet: (key) => cache.get(key),
    cacheSet: (key, value) => cache.set(key, value),
  };
}

test('stale canonical verification is single-flight and reuses the old version', async () => {
  const db = mockDb();
  const key = 'canonical-history:v1:profile-A';
  db.cacheSet(key, { version: 'state-v1', capturedAt: Date.now() - 1_800_000, items: {} });
  const client = new TraktClient({}, db);
  let calls = 0;
  client.pullState = async (id, since) => {
    calls++;
    assert.equal(id, 'profile-A');
    assert.equal(since, 'state-v1');
    await new Promise((resolve) => setImmediate(resolve));
    db.cacheSet(key, { version: 'state-v1', capturedAt: Date.now(), items: {} });
  };
  const [a,b] = await Promise.all([
    client.ensureCanonicalHistoryFresh('profile-A', 900),
    client.ensureCanonicalHistoryFresh('profile-A', 900),
  ]);
  assert.equal(calls, 1, 'one authoritative pull for simultaneous marks');
  assert.equal(a.refreshed, true);
  assert.equal(b.refreshed, true);
  const after = await client.ensureCanonicalHistoryFresh('profile-A', 900);
  assert.equal(after.reason, 'snapshot_fresh');
  assert.equal(calls, 1);
});

test('failed canonical recheck throttles repeat probes without accepting stale state', async () => {
  const db = mockDb();
  const key = 'canonical-history:v1:profile-A';
  db.cacheSet(key, { version: 'state-v1', capturedAt: Date.now() - 2_000_000, items: {} });
  const client = new TraktClient({}, db);
  let calls = 0;
  client.pullState = async () => { calls++; throw new Error('upstream rate limited'); };
  await assert.rejects(client.ensureCanonicalHistoryFresh('profile-A', 900, 120), /upstream rate limited/);
  const retry = await client.ensureCanonicalHistoryFresh('profile-A', 900, 120);
  assert.deepEqual(retry, { attempted: false, refreshed: false, reason: 'verification_throttled' });
  assert.equal(calls, 1);
});

test('missing canonical version never triggers an unbounded verification', async () => {
  const db = mockDb();
  const client = new TraktClient({}, db);
  client.pullState = async () => { throw new Error('should not call'); };
  assert.deepEqual(await client.ensureCanonicalHistoryFresh('profile-A'), {
    attempted: false, refreshed: false, reason: 'snapshot_unavailable',
  });
});
