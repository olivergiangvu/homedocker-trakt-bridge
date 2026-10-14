import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { BridgeDb } from '../src/db.mjs';
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';

const P = 'a'.repeat(24);
const make = () => {
  const db = Object.create(BridgeDb.prototype);
  db.db = new DatabaseSync(':memory:');
  db.db.exec(
    'CREATE TABLE media_cache (cache_key TEXT PRIMARY KEY, '
    + 'payload_json TEXT NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);'
    + 'CREATE TABLE profiles (id TEXT PRIMARY KEY, access_token_enc TEXT);'
  );
  db.db.prepare('INSERT INTO profiles VALUES (?,?)').run(P, 'encrypted-placeholder');
  const original = {
    revision: 1,
    shows: { 263102: {
      preferredMetaId: 'tt44094505', traktImdb: 'tt44051354',
      updatedAt: 1791940800,
    } },
  };
  db.cacheSet('identity-alias:v1:' + P, original, 10 * 365 * 86400);
  return { db, original };
};

test('SQLite cachePeek does not delete expired/corrupt rows during preview', () => {
  const { db } = make();
  try {
    const now = db.now();
    db.db.prepare('INSERT INTO media_cache VALUES (?, ?, ?, ?)')
      .run('corrupt', '{', now + 3600, now);
    db.db.prepare('INSERT INTO media_cache VALUES (?, ?, ?, ?)')
      .run('expired', '{}', now - 1, now);
    assert.equal(db.cachePeek('corrupt'), null);
    assert.equal(db.cachePeek('expired'), null);
    assert.equal(db.db.prepare('SELECT COUNT(*) AS n FROM media_cache').get().n, 3);
  } finally { db.db.close(); }
});

test('SQLite import creates one atomic graph; preview is read-only', () => {
  const { db, original } = make();
  try {
    const count = () => db.db.prepare('SELECT COUNT(*) AS n FROM media_cache').get().n;
    const before = count();
    assert.equal(importLegacyIdentityAliases(db, P).status, 'DRY_RUN_READY');
    assert.equal(count(), before);
    assert.equal(importLegacyIdentityAliases(db, P, { dryRun: false }).status,
      'LEGACY_IMPORTED');
    assert.equal(count(), before + 1);
    assert.equal(importLegacyIdentityAliases(db, P, { dryRun: false }).status,
      'GRAPH_ALREADY_EXISTS');
    assert.equal(count(), before + 1);
    assert.deepEqual(db.cachePeek('identity-alias:v1:' + P), original);
    assert.deepEqual(db.cachePeek('identity-graph:v1:' + P).shows['263102'].aliases,
      ['tt44094505']);
  } finally { db.db.close(); }
});

test('SQLite atomic insert-if-absent will never overwrite a graph', () => {
  const { db } = make();
  try {
    assert.equal(db.cacheInsertIfAbsent('identity-graph:v1:' + P,
      { revision: 77, shows: {}, conflicts: [] }), true);
    assert.equal(db.cacheInsertIfAbsent('identity-graph:v1:' + P,
      { revision: 1, shows: {}, conflicts: [] }), false);
    assert.equal(db.cachePeek('identity-graph:v1:' + P).revision, 77);
  } finally { db.db.close(); }
});
