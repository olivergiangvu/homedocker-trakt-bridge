import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { BridgeDb } from '../src/db.mjs';
import { SCHEMA_VERSION } from '../src/migrations.mjs';

function configFor(dir) {
  return {
    dataDir: dir,
    dbPath: path.join(dir, 'bridge.db'),
    bridgeSecret: 'test-secret',
  };
}

test('fresh database migrates to the current schema', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trakt-bridge-migrate-'));
  const db = new BridgeDb(configFor(dir));
  try {
    assert.equal(db.schemaVersion(), SCHEMA_VERSION);
    assert.equal(db.ping(), true);
    assert.equal(db.migration.from, 0);
    assert.equal(db.migration.to, SCHEMA_VERSION);
  } finally {
    db.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('v0.3.x schema upgrades in place without losing profiles', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trakt-bridge-legacy-'));
  const config = configFor(dir);
  const legacy = new DatabaseSync(config.dbPath);
  legacy.exec(`
    CREATE TABLE profiles (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      access_token_enc TEXT,
      refresh_token_enc TEXT,
      token_expires_at INTEGER,
      connected_at INTEGER,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
  `);
  legacy.prepare(`INSERT INTO profiles (id,name,created_at,updated_at) VALUES (?,?,?,?)`)
    .run('abc123', 'Oliver Trakt', 1, 1);
  legacy.close();

  const db = new BridgeDb(config);
  try {
    assert.equal(db.schemaVersion(), SCHEMA_VERSION);
    assert.equal(db.getProfile('abc123')?.name, 'Oliver Trakt');
    const tables = ['oauth_states', 'processed_events', 'media_cache', 'event_log'];
    for (const table of tables) {
      assert.equal(Boolean(db.db.prepare(`SELECT 1 FROM sqlite_master WHERE type='table' AND name=?`).get(table)), true);
    }
  } finally {
    db.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('database newer than this binary fails closed', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trakt-bridge-future-'));
  const config = configFor(dir);
  const future = new DatabaseSync(config.dbPath);
  future.exec(`PRAGMA user_version=${SCHEMA_VERSION + 1}`);
  future.close();

  assert.throws(() => new BridgeDb(config), /newer than supported schema/);
  fs.rmSync(dir, { recursive: true, force: true });
});
