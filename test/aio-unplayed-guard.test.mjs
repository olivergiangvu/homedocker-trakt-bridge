import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  classifyAioFalseUnplayedEcho,
} from '../src/aio-unplayed-guard.mjs';

function makeDb(path) {
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

    CREATE TABLE watch_sinks (
      id TEXT PRIMARY KEY,
      uuid TEXT NOT NULL,
      persona TEXT NOT NULL DEFAULT '',
      addon_instance_id TEXT NOT NULL,
      addon_name TEXT,
      status TEXT NOT NULL DEFAULT 'connected',
      updated_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE watch_deliveries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sink_id TEXT NOT NULL,
      item_key TEXT NOT NULL,
      event TEXT NOT NULL,
      status TEXT NOT NULL,
      body TEXT,
      created_at INTEGER NOT NULL,
      delivered_at INTEGER,
      last_error TEXT
    );
  `);
  return db;
}

function addSink(db) {
  db.prepare(`
    INSERT INTO watch_sinks
      (id, uuid, persona, addon_instance_id, addon_name, status, updated_at)
    VALUES (
      'sink-home', 'u1', '', 'e3fe3b0',
      'homedocker-trakt-bridge', 'connected', 1
    )
  `).run();
}

function putState(db, {
  positionMs = 398930,
  durationMs = 1740000,
  played = 0,
  origin = 'local',
  updatedAt = 1000150,
  lastPlayedAt = updatedAt,
} = {}) {
  db.prepare(`
    INSERT INTO watch_state
      (uuid, persona, item_key, kind, media_type, base_id,
       season, episode, video_id, position_ms, duration_ms,
       played, origin, updated_at, last_played_at)
    VALUES (
      'u1', '', 'e|tt14261112:2:6', 'episode', 'series', 'tt14261112',
      2, 6, 'tt14261112:2:6', ?, ?, ?, ?, ?, ?
    )
  `).run(
    positionMs,
    durationMs,
    played,
    origin,
    updatedAt,
    lastPlayedAt,
  );
}

function event(overrides = {}) {
  return {
    id: 'e|tt14261112:2:6|unplayed|1000000',
    event: 'unplayed',
    scope: 'episode',
    at: 1000,
    metaId: 'tt14261112',
    videoId: 'tt14261112:2:6',
    positionMs: 0,
    durationMs: 1740000,
    played: false,
    season: 2,
    episode: 6,
    ids: { imdb: 'tt14261112' },
    ...overrides,
  };
}

function addUnplayed(db, body = event(), createdAt = 1000000) {
  db.prepare(`
    INSERT INTO watch_deliveries
      (sink_id, item_key, event, status, body, created_at)
    VALUES (
      'sink-home', 'e|tt14261112:2:6',
      'unplayed', 'pending', ?, ?
    )
  `).run(JSON.stringify(body), createdAt);
}

function addPlayback(db, {
  eventName = 'stop',
  status = 'delivered',
  positionMs = 398928,
  createdAt = 900000,
  deliveredAt = 910000,
} = {}) {
  db.prepare(`
    INSERT INTO watch_deliveries
      (sink_id, item_key, event, status, body, created_at, delivered_at)
    VALUES (
      'sink-home', 'e|tt14261112:2:6',
      ?, ?, ?, ?, ?
    )
  `).run(
    eventName,
    status,
    JSON.stringify({ positionMs }),
    createdAt,
    deliveredAt,
  );
}

function classify(db, e = event()) {
  return classifyAioFalseUnplayedEcho(db, e, {
    sinkName: 'homedocker-trakt-bridge',
    sinkInstanceId: 'e3fe3b0',
    coverageLookbackMs: 180000,
    positionToleranceMs: 2000,
  });
}

test('suppresses exact AIO false-unplayed echo backed by matching delivered playback', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-guard-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db);
    addPlayback(db);
    addUnplayed(db);

    const result = classify(db);
    assert.ok(result);
    assert.equal(result.ignored, 'aio_false_unplayed_echo');
    assert.equal(result.itemKey, 'e|tt14261112:2:6');
    assert.equal(result.guardVariant, 'positive_resume');
    assert.equal(result.aioPositionMs, 398930);
    assert.equal(result.playbackEvent, 'stop');
    assert.equal(result.playbackPositionMs, 398928);
    assert.equal(result.playbackPositionDeltaMs, 2);
    assert.equal(result.writesTrakt, false);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('suppresses zero-position UserData echo when stop semantics stamp last_played_at', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-zero-echo-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db, {
      positionMs: 0,
      updatedAt: 1000019,
      lastPlayedAt: 1000018,
    });
    addUnplayed(db);

    const result = classify(db);
    assert.ok(result);
    assert.equal(result.ignored, 'aio_false_unplayed_echo');
    assert.equal(result.guardVariant, 'userdata_zero_position_stop');
    assert.equal(result.aioPositionMs, 0);
    assert.equal(result.stateDeltaMs, 19);
    assert.equal(result.lastPlayedDeltaMs, 18);
    assert.equal(result.writesTrakt, false);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('explicit Mark Unwatched zero state with unchanged last_played_at is never suppressed', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-explicit-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db, {
      positionMs: 0,
      updatedAt: 1000150,
      lastPlayedAt: 900000,
    });
    addPlayback(db);
    addUnplayed(db);

    assert.equal(classify(db), null);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('zero-position row fails open when last_played_at is stale', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-zero-stale-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db, {
      positionMs: 0,
      updatedAt: 1000100,
      lastPlayedAt: 997000,
    });
    addUnplayed(db);

    assert.equal(classify(db), null);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('guard fails open when matching playback was not delivered', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-undelivered-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db);
    addPlayback(db, { status: 'error', deliveredAt: null });
    addUnplayed(db);

    assert.equal(classify(db), null);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('later unrelated positive resume state does not suppress an older unplayed mark', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-stale-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db, { updatedAt: 1005001 });
    addPlayback(db);
    addUnplayed(db);

    assert.equal(classify(db), null);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('bulk unplayed is outside the single-item guard', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-unplayed-bulk-'));
  const db = makeDb(join(dir, 'db.sqlite'));

  try {
    addSink(db);
    putState(db);
    addPlayback(db);
    addUnplayed(db);

    assert.equal(classify(db, event({
      scope: 'season',
      videos: [{ videoId: 'tt14261112:2:6', season: 2, episode: 6 }],
      part: 1,
      parts: 1,
    })), null);
  } finally {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
