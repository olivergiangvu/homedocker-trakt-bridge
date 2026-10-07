import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  captureAioHistoryEvidenceOnce,
} from '../src/aio-history-evidence.mjs';
import {
  detectAioHistoryEcho,
} from '../src/aio-history-guard.mjs';
import {
  rememberCanonicalWatchedSnapshot,
} from '../src/canonical-history.mjs';

class MemoryDb {
  constructor() {
    this.values = new Map();
  }

  cacheGet(key) {
    return this.values.get(key) ?? null;
  }

  cacheSet(key, value) {
    this.values.set(key, JSON.parse(JSON.stringify(value)));
  }

  cacheDelete(key) {
    this.values.delete(key);
  }
}

function makeAio(path) {
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
      id TEXT PRIMARY KEY,
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

  db.prepare(`
    INSERT INTO watch_sinks
      (id, uuid, persona, addon_instance_id, addon_name, status, updated_at)
    VALUES
      ('sink-home', 'u1', '', 'e3fe3b0',
       'homedocker-trakt-bridge', 'connected', 1)
  `).run();

  return db;
}

function config(path) {
  return {
    aioHistoryEchoGuard: true,
    aioDbPath: path,
    aioReconcileSinkName: 'homedocker-trakt-bridge',
    aioReconcileSinkInstanceId: 'e3fe3b0',
    aioHistoryEvidenceLookbackSeconds: 120,
    aioHistoryEvidenceMaxRows: 500,
    aioHistoryCohortWindowMs: 5000,
    aioHistoryCohortMinItems: 3,
    canonicalHistoryMaxAgeSeconds: 900,
    aioUnplayedEchoGuard: false,
  };
}

function event({
  kind = 'unplayed',
  videoId = 'tt1000000:1:1',
  atMs = 2_000_000,
  positionMs = 0,
  played = kind === 'played',
} = {}) {
  return {
    id: `e|${videoId}|${kind}|${atMs}`,
    event: kind,
    scope: 'episode',
    at: Math.floor(atMs / 1000),
    metaId: 'tt1000000',
    videoId,
    positionMs,
    durationMs: 1_800_000,
    played,
    season: 1,
    episode: Number(videoId.split(':').at(-1)),
    ids: { imdb: 'tt1000000' },
  };
}

function addDelivery(
  db,
  e,
  createdAt,
  {
    itemKey = `e|${e.videoId}`,
    status = 'pending',
  } = {},
) {
  db.prepare(`
    INSERT INTO watch_deliveries
      (id, sink_id, item_key, event, status, body, created_at)
    VALUES (?, 'sink-home', ?, ?, ?, ?, ?)
  `).run(
    `row-${e.id}`,
    itemKey,
    e.event,
    status,
    JSON.stringify(e),
    createdAt,
  );
}

function putState(
  db,
  e,
  {
    positionMs = 0,
    played = 0,
    origin = 'local',
    updatedAt,
    lastPlayedAt = null,
  },
) {
  db.prepare(`
    INSERT INTO watch_state
      (uuid, persona, item_key, kind, media_type, base_id,
       season, episode, video_id, position_ms, duration_ms,
       played, origin, updated_at, last_played_at)
    VALUES
      ('u1', '', ?, 'episode', 'series', 'tt1000000',
       1, ?, ?, ?, 1800000, ?, ?, ?, ?)
  `).run(
    `e|${e.videoId}`,
    e.episode,
    e.videoId,
    positionMs,
    played,
    origin,
    updatedAt,
    lastPlayedAt,
  );
}

test('RC4 suppresses positive-resume unplayed from immutable event-time evidence', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rc4-evidence-'));
  const path = join(dir, 'aio.sqlite');
  const aio = makeAio(path);
  const bridge = new MemoryDb();

  try {
    const e = event({ atMs: 2_000_000 });
    addDelivery(aio, e, 2_000_010);
    putState(aio, e, {
      positionMs: 420_000,
      updatedAt: 2_000_020,
      lastPlayedAt: 2_000_019,
    });
    aio.close();

    const captured = captureAioHistoryEvidenceOnce({
      config: config(path),
      db: bridge,
      nowMs: 2_000_100,
    });
    assert.equal(captured.captured, 1);

    const result = detectAioHistoryEcho(
      config(path),
      bridge,
      'p1',
      e,
    );

    assert.ok(result);
    assert.equal(result.ignored, 'aio_false_unplayed_echo');
    assert.equal(result.guardVariant, 'event_time_positive_resume');
    assert.equal(result.aioPositionMs, 420_000);
    assert.equal(result.writesTrakt, false);
  } finally {
    try { aio.close(); } catch {}
    rmSync(dir, { recursive: true, force: true });
  }
});

test('RC4 suppresses three-item single-mark sync fanout', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rc4-cohort-'));
  const path = join(dir, 'aio.sqlite');
  const aio = makeAio(path);
  const bridge = new MemoryDb();

  try {
    const events = [1, 2, 3].map((episode, index) => event({
      kind: 'played',
      videoId: `tt1000000:1:${episode}`,
      atMs: 3_000_000 + (index * 700),
      played: true,
    }));

    events.forEach((e, index) => addDelivery(
      aio,
      e,
      3_000_010 + (index * 700),
    ));
    aio.close();

    const result = detectAioHistoryEcho(
      config(path),
      bridge,
      'p1',
      events[0],
    );

    assert.ok(result);
    assert.equal(result.ignored, 'aio_history_sync_fanout');
    assert.equal(result.guardVariant, 'single_mark_cohort');
    assert.equal(result.cohortItems, 3);
  } finally {
    try { aio.close(); } catch {}
    rmSync(dir, { recursive: true, force: true });
  }
});

test('RC4 suppresses same-state played so old Trakt history is not rejuvenated', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rc4-canonical-'));
  const path = join(dir, 'aio.sqlite');
  const aio = makeAio(path);
  const bridge = new MemoryDb();

  try {
    const e = event({
      kind: 'played',
      atMs: 4_000_000,
      played: true,
    });
    addDelivery(aio, e, 4_000_010);
    aio.close();

    rememberCanonicalWatchedSnapshot(
      bridge,
      'p1',
      {
        movies: [],
        episodes: ['tt1000000:1:1'],
      },
      {
        version: 'pull-v1',
        nowMs: Date.now(),
      },
    );

    const result = detectAioHistoryEcho(
      config(path),
      bridge,
      'p1',
      e,
    );

    assert.ok(result);
    assert.equal(result.ignored, 'canonical_history_same_state');
    assert.equal(result.guardVariant, 'canonical_same_state_played');
    assert.equal(result.canonicalWatched, true);
  } finally {
    try { aio.close(); } catch {}
    rmSync(dir, { recursive: true, force: true });
  }
});

test('RC4 preserves a genuine single Mark Watched transition', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rc4-manual-played-'));
  const path = join(dir, 'aio.sqlite');
  const aio = makeAio(path);
  const bridge = new MemoryDb();

  try {
    const e = event({
      kind: 'played',
      atMs: 5_000_000,
      played: true,
    });
    addDelivery(aio, e, 5_000_010);
    aio.close();

    rememberCanonicalWatchedSnapshot(
      bridge,
      'p1',
      {
        movies: [],
        episodes: [],
      },
      {
        version: 'pull-v1',
        nowMs: Date.now(),
      },
    );

    const result = detectAioHistoryEcho(
      config(path),
      bridge,
      'p1',
      e,
    );

    assert.equal(result, null);
  } finally {
    try { aio.close(); } catch {}
    rmSync(dir, { recursive: true, force: true });
  }
});
