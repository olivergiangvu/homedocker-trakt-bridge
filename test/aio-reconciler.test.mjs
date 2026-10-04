import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  classifyTraktPlaybackCandidate,
  aioReconcileOperationalSnapshot,
  compareAioCandidatesOnce,
  reconcileAioOnce,
  resolveHomeDockerSink,
  seedAioReconcileCursor,
} from '../src/aio-reconciler.mjs';

class FakeBridgeDb {
  constructor() {
    this.cache = new Map();
    this.events = [];
  }

  listProfiles() {
    return [{
      id: 'aaaaaaaaaaaaaaaaaaaaaaaa',
      access_token_enc: 'connected',
    }];
  }

  cacheGet(key) {
    return this.cache.get(key) ?? null;
  }

  cacheSet(key, value) {
    this.cache.set(key, structuredClone(value));
  }

  logEvent(event) {
    this.events.push(structuredClone(event));
  }
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

function addHomeDockerSink(db, {
  id = 'sink-home',
  instance = 'e3fe3b0',
  name = 'homedocker-trakt-bridge',
} = {}) {
  db.prepare(`
    INSERT INTO watch_sinks
      (id, uuid, persona, addon_instance_id, addon_name, status, updated_at)
    VALUES (?, 'u1', '', ?, ?, 'connected', 1)
  `).run(id, instance, name);
}

function insertResume(db, {
  positionMs,
  updatedAt,
  itemKey = 'e|tt10009170:2:7',
} = {}) {
  db.prepare(`
    INSERT INTO watch_state
      (uuid, persona, item_key, kind, media_type, base_id,
       season, episode, video_id, position_ms, duration_ms,
       played, origin, updated_at, last_played_at)
    VALUES (
      'u1', '', ?, 'episode', 'series', 'tt10009170',
      2, 7, 'tt10009170:2:7', ?, 1500000,
      0, 'local', ?, ?
    )
    ON CONFLICT(uuid, persona, item_key) DO UPDATE SET
      position_ms=excluded.position_ms,
      updated_at=excluded.updated_at,
      last_played_at=excluded.last_played_at
  `).run(itemKey, positionMs, updatedAt, updatedAt);
}

function config(path, overrides = {}) {
  return {
    aioReconcilerMode: 'detect',
    aioDbPath: path,
    aioReconcileIntervalSeconds: 15,
    aioReconcileGraceSeconds: 30,
    aioReconcileQuietSeconds: 300,
    aioReconcileCoverageLookbackSeconds: 180,
    aioReconcilePositionToleranceMs: 2000,
    aioReconcileMaxRows: 100,
    aioReconcileSinkName: 'homedocker-trakt-bridge',
    aioReconcileSinkInstanceId: 'e3fe3b0',
    ...overrides,
  };
}

test('first rc6 detect run establishes a fresh v2 baseline', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-baseline-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    insertResume(aio, { positionMs: 443904, updatedAt: 100000 });

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 500000,
    });

    assert.equal(result.status, 'baseline');
    assert.equal(result.observed, 0);
    assert.equal(result.settled, 0);
    assert.equal(bridge.events.length, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('intermediate local update stays pending until 300s quiet', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-pending-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, { positionMs: 362934, updatedAt: 100000 });

    let result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 140000,
    });

    assert.equal(result.observed, 1);
    assert.equal(result.pending, 1);
    assert.equal(result.settled, 0);
    assert.equal(bridge.events.length, 0);

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 399999,
    });

    assert.equal(result.pending, 1);
    assert.equal(result.settled, 0);
    assert.equal(bridge.events.length, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('newer same-item state replaces pending and resets quiet timer', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-coalesce-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);

    insertResume(aio, {
      positionMs: 362934,
      updatedAt: 100000,
    });

    let result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 140000,
    });

    assert.equal(result.pending, 1);
    assert.equal(result.settled, 0);

    insertResume(aio, {
      positionMs: 536771,
      updatedAt: 280500,
    });

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 320500,
    });

    assert.equal(result.observed, 1);
    assert.equal(result.replaced, 1);
    assert.equal(result.pending, 1);
    assert.equal(result.settled, 0);

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 500000,
    });

    assert.equal(result.settled, 0);
    assert.equal(result.pending, 1);
    assert.equal(bridge.events.length, 0);

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 580500,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 1);
    assert.equal(result.pending, 0);
    assert.equal(bridge.events.length, 1);

    const detail = JSON.parse(bridge.events[0].detail);
    assert.equal(
      detail.decision,
      'settled_missing_homedocker_playback_delivery',
    );
    assert.equal(detail.positionMs, 536771);
    assert.equal(detail.quietSeconds, 300);
    assert.equal(detail.writesTrakt, false);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('delivery coverage is specific to the HomeDocker sink', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-sink-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);

    aio.prepare(`
      INSERT INTO watch_sinks
        (id, uuid, persona, addon_instance_id, addon_name, status, updated_at)
      VALUES (
        'sink-meta', 'u1', '', '514e3b0',
        'AIOMetadata SH', 'connected', 1
      )
    `).run();

    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, {
      positionMs: 443904,
      updatedAt: 100000,
    });

    aio.prepare(`
      INSERT INTO watch_deliveries
        (sink_id, item_key, event, status, body, created_at, delivered_at)
      VALUES (
        'sink-meta', 'e|tt10009170:2:7',
        'stop', 'delivered', '{"positionMs":443904}',
        100005, 101000
      )
    `).run();

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 400000,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 1);
    assert.equal(result.covered, 0);

    const detail = JSON.parse(bridge.events[0].detail);
    assert.equal(
      detail.decision,
      'settled_missing_homedocker_playback_delivery',
    );
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('HomeDocker stop delivery marks settled state as covered', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-covered-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, {
      positionMs: 500000,
      updatedAt: 100000,
    });

    aio.prepare(`
      INSERT INTO watch_deliveries
        (sink_id, item_key, event, status, body, created_at, delivered_at)
      VALUES (
        'sink-home', 'e|tt10009170:2:7',
        'stop', 'delivered', '{"positionMs":500000}',
        100005, 101000
      )
    `).run();

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 400000,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 0);
    assert.equal(result.covered, 1);

    const detail = JSON.parse(bridge.events[0].detail);
    assert.equal(
      detail.decision,
      'covered_by_homedocker_playback_delivery',
    );
    assert.equal(detail.delivery.event, 'stop');
    assert.equal(detail.delivery.positionMs, 500000);
    assert.equal(detail.delivery.positionDeltaMs, 0);
    assert.equal(detail.sink.addonInstanceId, 'e3fe3b0');
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('rc7 covers a later UserData row with an equivalent delivered stop 65s earlier', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc7-late-userdata-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);

    insertResume(aio, {
      positionMs: 651269,
      updatedAt: 165235,
    });

    aio.prepare(`
      INSERT INTO watch_deliveries
        (sink_id, item_key, event, status, body, created_at, delivered_at)
      VALUES (
        'sink-home', 'e|tt10009170:2:7',
        'stop', 'delivered', '{"positionMs":651281}',
        100074, 146714
      )
    `).run();

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 465235,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 0);
    assert.equal(result.covered, 1);

    const detail = JSON.parse(bridge.events[0].detail);
    assert.equal(
      detail.decision,
      'covered_by_homedocker_playback_delivery',
    );
    assert.equal(detail.delivery.event, 'stop');
    assert.equal(detail.delivery.positionMs, 651281);
    assert.equal(detail.delivery.positionDeltaMs, 12);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('rc7 does not cover a delivered stop whose playback position differs materially', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc7-position-mismatch-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);

    insertResume(aio, {
      positionMs: 651269,
      updatedAt: 165235,
    });

    aio.prepare(`
      INSERT INTO watch_deliveries
        (sink_id, item_key, event, status, body, created_at, delivered_at)
      VALUES (
        'sink-home', 'e|tt10009170:2:7',
        'stop', 'delivered', '{"positionMs":624539}',
        100074, 146714
      )
    `).run();

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 465235,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 1);
    assert.equal(result.covered, 0);

    const detail = JSON.parse(bridge.events[0].detail);
    assert.equal(
      detail.decision,
      'settled_missing_homedocker_playback_delivery',
    );
    assert.equal(detail.delivery, null);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('rc7 does not treat pending or errored playback deliveries as coverage', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc7-undelivered-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);

    insertResume(aio, {
      positionMs: 651269,
      updatedAt: 165235,
    });

    aio.prepare(`
      INSERT INTO watch_deliveries
        (sink_id, item_key, event, status, body, created_at, last_error)
      VALUES (
        'sink-home', 'e|tt10009170:2:7',
        'stop', 'error', '{"positionMs":651281}',
        100074, 'upstream failure'
      )
    `).run();

    const result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 465235,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 1);
    assert.equal(result.covered, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('pending state survives restart and settles only once', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-restart-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, {
      positionMs: 536771,
      updatedAt: 100000,
    });

    let result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 140000,
    });

    assert.equal(result.pending, 1);
    assert.equal(result.settled, 0);

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 400000,
    });

    assert.equal(result.settled, 1);
    assert.equal(result.candidates, 1);
    assert.equal(bridge.events.length, 1);

    result = reconcileAioOnce({
      config: config(path),
      db: bridge,
      nowMs: 700000,
    });

    assert.equal(result.settled, 0);
    assert.equal(result.pending, 0);
    assert.equal(bridge.events.length, 1);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('exact sink instance id fails safe when it does not match', () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-rc6-sink-id-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);

  try {
    addHomeDockerSink(aio);

    const resolved = resolveHomeDockerSink(
      aio,
      { uuid: 'u1', persona: '' },
      {
        sinkName: 'homedocker-trakt-bridge',
        sinkInstanceId: 'wrong-id',
      },
    );

    assert.equal(resolved.status, 'missing');
    assert.equal(resolved.sink, null);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});


test('compare classifier lets newer native Trakt playback win', () => {
  const item = {
    kind: 'episode',
    baseId: 'tt10009170',
    season: 2,
    episode: 7,
    positionMs: 900000,
    durationMs: 1500000,
    updatedAt: 100000,
  };

  const result = classifyTraktPlaybackCandidate(item, [{
    progress: 20,
    paused_at: new Date(200000).toISOString(),
    episode: {
      season: 2,
      number: 7,
      ids: { trakt: 7007 },
    },
    show: {
      ids: { imdb: 'tt10009170', trakt: 1000 },
    },
  }]);

  assert.equal(result.decision, 'trakt_same_or_newer');
  assert.equal(result.reason, 'trakt_timestamp_same_or_newer');
  assert.equal(result.candidate, false);
});

test('compare classifier marks older behind Trakt playback stale', () => {
  const item = {
    kind: 'episode',
    baseId: 'tt10009170',
    season: 2,
    episode: 7,
    positionMs: 900000,
    durationMs: 1500000,
    updatedAt: 200000,
  };

  const result = classifyTraktPlaybackCandidate(item, [{
    progress: 40,
    paused_at: new Date(100000).toISOString(),
    episode: {
      season: 2,
      number: 7,
      ids: { trakt: 7007 },
    },
    show: {
      ids: { imdb: 'tt10009170', trakt: 1000 },
    },
  }], {
    positionToleranceMs: 2000,
  });

  assert.equal(result.decision, 'trakt_stale_candidate');
  assert.equal(result.reason, 'trakt_older_and_behind');
  assert.equal(result.candidate, true);
  assert.equal(result.trakt.positionMs, 600000);
});

test('compare classifier marks missing playback without writing', () => {
  const result = classifyTraktPlaybackCandidate({
    kind: 'movie',
    baseId: 'tt0111161',
    positionMs: 300000,
    durationMs: 600000,
    updatedAt: 200000,
  }, [{
    progress: 50,
    paused_at: new Date(100000).toISOString(),
    movie: {
      ids: { imdb: 'tt0068646', trakt: 2 },
    },
  }]);

  assert.equal(result.decision, 'trakt_playback_missing_candidate');
  assert.equal(result.candidate, true);
  assert.equal(result.trakt, null);
});

test('compare classifier fails closed for unusable identity', () => {
  const result = classifyTraktPlaybackCandidate({
    kind: 'episode',
    baseId: 'unknown-id',
    season: 1,
    episode: 1,
    positionMs: 300000,
    durationMs: 600000,
    updatedAt: 200000,
  }, []);

  assert.equal(result.decision, 'trakt_compare_ambiguous');
  assert.equal(result.reason, 'candidate_identity_unusable');
  assert.equal(result.candidate, false);
});

test('compare mode stages detect candidate then classifies Trakt state read-only', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-v13-compare-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();
  const calls = [];

  const trakt = {
    async requestAllPages(profileId, requestPath, options) {
      calls.push({ profileId, requestPath, options });
      return [{
        progress: 40,
        paused_at: new Date(100000).toISOString(),
        episode: {
          season: 2,
          number: 7,
          ids: { trakt: 7007 },
        },
        show: {
          ids: { imdb: 'tt10009170', trakt: 1000 },
        },
      }];
    },
  };

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, {
      positionMs: 900000,
      updatedAt: 200000,
    });

    const detected = reconcileAioOnce({
      config: config(path, {
        aioReconcilerMode: 'compare',
      }),
      db: bridge,
      nowMs: 500000,
    });

    assert.equal(detected.settled, 1);
    assert.equal(detected.candidates, 1);
    assert.equal(bridge.events.length, 1);

    const compared = await compareAioCandidatesOnce({
      config: config(path, {
        aioReconcilerMode: 'compare',
      }),
      db: bridge,
      trakt,
      nowMs: 500000,
    });

    assert.equal(compared.compared, 1);
    assert.equal(compared.stale, 1);
    assert.equal(compared.pending, 0);
    assert.equal(calls.length, 1);
    assert.equal(
      calls[0].requestPath,
      '/sync/playback/episodes?extended=full',
    );

    assert.equal(bridge.events.length, 2);
    const detail = JSON.parse(bridge.events[1].detail);
    assert.equal(detail.action, 'aio-reconcile:compare-settled');
    assert.equal(detail.decision, 'trakt_stale_candidate');
    assert.equal(detail.writesTrakt, false);

    const snapshot = aioReconcileOperationalSnapshot(bridge);
    assert.equal(snapshot.compare.awaiting, 0);
    assert.equal(snapshot.compare.total, 1);
    assert.equal(snapshot.compare.staleCandidate, 1);
    assert.equal(snapshot.compare.sameOrNewer, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('compare mode keeps candidate retryable when Trakt GET is rate limited', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'aio-v13-compare-retry-'));
  const path = join(dir, 'db.sqlite');
  const aio = makeAioDb(path);
  const bridge = new FakeBridgeDb();

  const trakt = {
    async requestAllPages() {
      const error = new Error('rate limited');
      error.status = 429;
      error.code = 'trakt_rate_cooldown';
      error.retryAfter = '120';
      throw error;
    },
  };

  try {
    addHomeDockerSink(aio);
    seedAioReconcileCursor(bridge, 0);
    insertResume(aio, {
      positionMs: 900000,
      updatedAt: 200000,
    });

    reconcileAioOnce({
      config: config(path, {
        aioReconcilerMode: 'compare',
      }),
      db: bridge,
      nowMs: 500000,
    });

    const first = await compareAioCandidatesOnce({
      config: config(path, {
        aioReconcilerMode: 'compare',
      }),
      db: bridge,
      trakt,
      nowMs: 500000,
    });

    assert.equal(first.compared, 0);
    assert.equal(first.unavailable, 1);
    assert.equal(first.pending, 1);
    assert.equal(bridge.events.length, 1);

    const second = await compareAioCandidatesOnce({
      config: config(path, {
        aioReconcilerMode: 'compare',
      }),
      db: bridge,
      trakt,
      nowMs: 550000,
    });

    assert.equal(second.compared, 0);
    assert.equal(second.unavailable, 0);
    assert.equal(second.pending, 1);
    assert.equal(bridge.events.length, 1);

    const snapshot = aioReconcileOperationalSnapshot(bridge, 550000);
    assert.equal(snapshot.compare.awaiting, 1);
    assert.equal(snapshot.compare.retrying, 1);
    assert.equal(snapshot.compare.total, 0);
  } finally {
    aio.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
