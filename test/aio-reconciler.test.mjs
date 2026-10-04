import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  classifyAioResumeCandidate,
  reconcileAioOnce,
  seedAioReconcileCursor,
} from '../src/aio-reconciler.mjs';

class FakeBridgeDb {
  constructor() {
    this.cache = new Map();
    this.events = [];
  }
  listProfiles() { return [{ id: 'aaaaaaaaaaaaaaaaaaaaaaaa', access_token_enc: 'connected' }]; }
  cacheGet(key) { return this.cache.get(key) ?? null; }
  cacheSet(key, value) { this.cache.set(key, structuredClone(value)); }
  logEvent(event) { this.events.push(structuredClone(event)); }
}

function makeAioDb(path) {
  const db = new DatabaseSync(path);
  db.exec(`
    CREATE TABLE watch_state (
      uuid TEXT NOT NULL,
      persona TEXT NOT NULL DEFAULT '',
      item_key TEXT NOT NULL,
      kind TEXT NOT NULL,
      media_type TEXT NOT NULL,
      base_id TEXT NOT NULL,
      season INTEGER,
      episode INTEGER,
      video_id TEXT,
      position_ms INTEGER NOT NULL DEFAULT 0,
      duration_ms INTEGER NOT NULL DEFAULT 0,
      played INTEGER NOT NULL DEFAULT 0,
      origin TEXT NOT NULL DEFAULT 'local',
      sink_id TEXT,
      external_at INTEGER,
      updated_at INTEGER NOT NULL DEFAULT 0,
      last_played_at INTEGER,
      PRIMARY KEY (uuid, persona, item_key)
    );
    CREATE TABLE watch_deliveries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      item_key TEXT NOT NULL,
      event TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      delivered_at INTEGER,
      last_error TEXT
    );
  `);
  return db;
}

function config(path) {
  return {
    aioReconcilerMode: 'detect',
    aioDbPath: path,
    aioReconcileGraceSeconds: 30,
    aioReconcileMaxRows: 100,
    aioReconcileIntervalSeconds: 15,
  };
}

test('detect-only reconciler flags a local resume update with no AIO playback delivery', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-reconcile-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();
  try {
    aio.prepare(`
      INSERT INTO watch_state
        (uuid, persona, item_key, kind, media_type, base_id, season, episode, video_id,
         position_ms, duration_ms, played, origin, updated_at)
      VALUES (?, '', ?, 'episode', 'series', ?, 2, 7, ?, 443904, 1500000, 0, 'local', 100000)
    `).run('u1', 'e|tt10009170:2:7', 'tt10009170', 'tt10009170:2:7');

    seedAioReconcileCursor(bridge, 0);
    const result = reconcileAioOnce({ config: config(path), db: bridge, nowMs: 200000 });

    assert.equal(result.status, 'ok');
    assert.equal(result.processed, 1);
    assert.equal(result.candidates, 1);
    assert.equal(result.details[0].decision, 'candidate_missing_aio_playback_delivery');
    assert.equal(result.details[0].positionMs, 443904);
    assert.equal(result.details[0].writesTrakt, false);
    assert.equal(bridge.events.length, 1);
    assert.equal(bridge.events[0].event, 'reconcile');
    assert.equal(bridge.events[0].status, 'ignored');
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('nearby start/pause/stop delivery suppresses the detect-only candidate', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-reconcile-covered-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();
  try {
    aio.prepare(`
      INSERT INTO watch_state
        (uuid, persona, item_key, kind, media_type, base_id, season, episode, video_id,
         position_ms, duration_ms, played, origin, updated_at)
      VALUES (?, '', ?, 'episode', 'series', ?, 2, 7, ?, 500000, 1500000, 0, 'local', 300000)
    `).run('u1', 'e|tt10009170:2:7', 'tt10009170', 'tt10009170:2:7');
    aio.prepare(`
      INSERT INTO watch_deliveries (item_key, event, status, created_at, delivered_at)
      VALUES (?, 'stop', 'delivered', 300005, 301000)
    `).run('e|tt10009170:2:7');

    seedAioReconcileCursor(bridge, 0);
    const result = reconcileAioOnce({ config: config(path), db: bridge, nowMs: 400000 });

    assert.equal(result.processed, 1);
    assert.equal(result.candidates, 0);
    assert.equal(result.details[0].decision, 'covered_by_aio_playback_delivery');
    assert.equal(result.details[0].delivery.event, 'stop');
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('first detect-only run establishes a baseline instead of replaying old AIO history', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-reconcile-baseline-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();
  try {
    aio.prepare(`
      INSERT INTO watch_state
        (uuid, persona, item_key, kind, media_type, base_id, video_id,
         position_ms, duration_ms, played, origin, updated_at)
      VALUES ('u1', '', 'm|tt1234567', 'movie', 'movie', 'tt1234567', 'tt1234567',
              200000, 1000000, 0, 'local', 100000)
    `).run();

    const result = reconcileAioOnce({ config: config(path), db: bridge, nowMs: 500000 });
    assert.equal(result.status, 'baseline');
    assert.equal(result.processed, 0);
    assert.equal(bridge.events.length, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('candidate classification is explicitly non-writing', () => {
  const result = classifyAioResumeCandidate({ position_ms: 50, duration_ms: 100 }, null);
  assert.equal(result.candidate, true);
  assert.equal(result.decision, 'candidate_missing_aio_playback_delivery');
  assert.equal(result.progressPercent, 50);
});
