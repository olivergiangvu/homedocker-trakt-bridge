import test from 'node:test';
import assert from 'node:assert/strict';
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';
import { loadVerifiedIdentityGraph, verifiedCanonicalForImdb } from '../src/identity-continuity.mjs';

const P = 'a'.repeat(24);
const fixture = (show = '263102', canonical = 'tt44051354',
                 alias = 'tt44094505') => ({
  revision: 1,
  shows: {
    [show]: {
      traktImdb: canonical,
      preferredMetaId: alias,
      updatedAt: 1791940800,
    },
  },
});
function db() {
  const data = new Map();
  let insertCount = 0;
  const profile = { id: P, access_token_enc: 'encrypted-token-not-for-logging' };
  const store = {
    data,
    getProfile(id) { return id === P ? profile : null; },
    cachePeek(key) { return structuredClone(data.get(key) ?? null); },
    cacheGet(key) { return structuredClone(data.get(key) ?? null); },
    cacheInsertIfAbsent(key, payload) {
      if (data.has(key)) return false;
      insertCount++;
      data.set(key, structuredClone(payload));
      return true;
    },
    cacheSet(key, payload) {
      data.set(key, structuredClone(payload));
    },
    inserted: () => insertCount,
    loadLegacy: (value) => data.set('identity-alias:v1:' + P, structuredClone(value)),
  };
  return store;
}
const migrate = (store, dryRun = true) =>
  importLegacyIdentityAliases(store, P, { dryRun });

test('one RC6 legacy proven show previews with ZERO writes, then imports exactly once', () => {
  const store = db();
  store.loadLegacy(fixture());
  const old = structuredClone(store.data.get('identity-alias:v1:' + P));
  const pre = migrate(store);
  assert.deepEqual(pre, {
    status: 'DRY_RUN_READY', changed: false, showCount: 1,
    aliasedShows: 1,
    source: 'rc6_successful_trakt_scrobble_identity_alias_v1',
    targetRevision: 1,
  });
  assert.equal(store.inserted(), 0);
  assert.equal(store.data.size, 1);
  const applied = migrate(store, false);
  assert.equal(applied.status, 'LEGACY_IMPORTED');
  assert.equal(applied.changed, true);
  const graph = loadVerifiedIdentityGraph(store, P);
  assert.deepEqual(graph.shows['263102'], {
    canonicalImdb: 'tt44051354', tmdb: null, tvdb: null,
    aliases: ['tt44094505'],
  });
  assert.deepEqual(verifiedCanonicalForImdb(graph, 'tt44094505'), {
    traktShowId: 263102, canonicalImdb: 'tt44051354', verified: true,
  });
  assert.deepEqual(store.data.get('identity-alias:v1:' + P), old);
  assert.equal(migrate(store, false).status, 'GRAPH_ALREADY_EXISTS');
  assert.equal(store.inserted(), 1);
});

test('import is exactly scoped to the connected Bridge profile, not every cached profile', () => {
  const store = db();
  store.loadLegacy(fixture());
  store.data.set('identity-alias:v1:' + 'b'.repeat(24), fixture(
    '999', 'tt88888888', 'tt77777777'));
  assert.equal(importLegacyIdentityAliases(store, 'b'.repeat(24)).status,
    'PROFILE_NOT_CONNECTED');
  assert.equal(store.inserted(), 0);
  assert.equal(migrate(store, false).status, 'LEGACY_IMPORTED');
  assert.equal(store.data.has('identity-graph:v1:' + 'b'.repeat(24)), false);
});

test('invalid/imprecise/expired provenance or partial row fails closed atomically', () => {
  for (const bad of [
    { ...fixture(), shows: { '263102': { ...fixture().shows['263102'], updatedAt: 0 } } },
    fixture('263102', 'x-not-imdb', 'tt44094505'),
    fixture('263102', 'tt44051354', 'invalid'),
    fixture('0', 'tt44051354', 'tt44094505'),
    { shows: { '263102': fixture().shows['263102'],
      '263103': { preferredMetaId: 'tt10101010' } } },
  ]) {
    const store = db();
    store.loadLegacy(bad);
    assert.equal(migrate(store, false).changed, false);
    assert.equal(store.inserted(), 0);
    assert.equal(store.data.has('identity-graph:v1:' + P), false);
  }
});

test('cross-Trakt show claims same IMDb alias MUST abort entire migration', () => {
  const store = db();
  const legacy = fixture();
  legacy.shows['1'] = {
    traktImdb: 'tt88888888', preferredMetaId: 'tt44094505',
    updatedAt: 1791940800,
  };
  store.loadLegacy(legacy);
  const out = migrate(store, false);
  assert.equal(out.status, 'CROSS_SHOW_IDENTITY_CONFLICT');
  assert.equal(store.inserted(), 0);
});

test('canonical-only legacy rows never create fake aliases', () => {
  const store = db();
  store.loadLegacy(fixture('263102', 'tt44051354', 'tt44051354'));
  assert.equal(migrate(store, false).status, 'NO_DIFFERING_ALIASES');
  assert.equal(store.inserted(), 0);
});

test('an existing graph is never overwritten even if it was created by another worker', () => {
  const store = db();
  store.loadLegacy(fixture());
  store.data.set('identity-graph:v1:' + P, { revision: 42, shows: {}, conflicts: [] });
  assert.equal(migrate(store, false).status, 'GRAPH_ALREADY_EXISTS');
  assert.equal(store.data.get('identity-graph:v1:' + P).revision, 42);
  assert.equal(store.inserted(), 0);
});

test('atomic insert-if-absent guard handles a concurrent graph writer', () => {
  const store = db();
  store.loadLegacy(fixture());
  store.cacheInsertIfAbsent = () => false;
  assert.equal(migrate(store, false).status, 'GRAPH_CREATED_CONCURRENTLY');
});

test('no direct AIO SQLite, no network, no Trakt calls or user history writes', () => {
  const store = db();
  store.loadLegacy(fixture());
  assert.equal(migrate(store, false).status, 'LEGACY_IMPORTED');
  assert.deepEqual([...store.data.keys()].sort(), [
    'identity-alias:v1:' + P,
    'identity-graph:v1:' + P,
  ]);
});
