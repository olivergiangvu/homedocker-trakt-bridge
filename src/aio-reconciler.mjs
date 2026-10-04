import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

const CURSOR_KEY = 'aio-reconcile:v1:cursor';
const ROW_STATE_PREFIX = 'aio-reconcile:v1:row:';
const CACHE_TTL_SECONDS = 10 * 365 * 24 * 3600;
const REQUIRED_WATCH_STATE_COLUMNS = [
  'uuid', 'persona', 'item_key', 'kind', 'media_type', 'base_id',
  'season', 'episode', 'video_id', 'position_ms', 'duration_ms',
  'played', 'origin', 'sink_id', 'external_at', 'updated_at', 'last_played_at',
];
const REQUIRED_DELIVERY_COLUMNS = [
  'item_key', 'event', 'status', 'created_at', 'delivered_at', 'last_error',
];
const DELIVERY_EVENTS = ['start', 'pause', 'stop'];
const DELIVERY_MATCH_MS = 15_000;

function defaultCursor(updatedAt = 0) {
  return { updatedAt, uuid: '', persona: '', itemKey: '' };
}

function normalizeCursor(value, fallback = 0) {
  if (!value || !Number.isFinite(Number(value.updatedAt))) return defaultCursor(fallback);
  return {
    updatedAt: Number(value.updatedAt),
    uuid: String(value.uuid || ''),
    persona: String(value.persona || ''),
    itemKey: String(value.itemKey || ''),
  };
}

function cursorForRow(row) {
  return {
    updatedAt: Number(row.updated_at),
    uuid: String(row.uuid),
    persona: String(row.persona || ''),
    itemKey: String(row.item_key),
  };
}

function rowStateKey(row) {
  const digest = createHash('sha256')
    .update(`${row.uuid}\u001f${row.persona || ''}\u001f${row.item_key}`)
    .digest('hex')
    .slice(0, 24);
  return `${ROW_STATE_PREFIX}${digest}`;
}

function tableColumns(db, table) {
  return new Set(db.prepare(`PRAGMA table_info(${table})`).all().map((row) => String(row.name)));
}

function requireColumns(db, table, required) {
  const columns = tableColumns(db, table);
  const missing = required.filter((column) => !columns.has(column));
  if (missing.length) {
    throw new Error(`AIO database schema incompatible: ${table} missing ${missing.join(', ')}`);
  }
}

export function openAioReadOnlyDatabase(path) {
  const db = new DatabaseSync(path, { readOnly: true, timeout: 1000 });
  db.exec('PRAGMA query_only=ON');
  requireColumns(db, 'watch_state', REQUIRED_WATCH_STATE_COLUMNS);
  requireColumns(db, 'watch_deliveries', REQUIRED_DELIVERY_COLUMNS);
  return db;
}

export function readAioResumeCandidates(db, { cursor, cutoffMs, maxRows }) {
  const after = normalizeCursor(cursor);
  return db.prepare(`
    SELECT
      uuid, persona, item_key, kind, media_type, base_id,
      season, episode, video_id, position_ms, duration_ms,
      played, origin, sink_id, external_at, updated_at, last_played_at
    FROM watch_state
    WHERE
      origin = 'local'
      AND played = 0
      AND position_ms > 0
      AND duration_ms > 0
      AND kind IN ('movie', 'episode')
      AND updated_at <= ?
      AND (updated_at, uuid, persona, item_key) > (?, ?, ?, ?)
    ORDER BY updated_at ASC, uuid ASC, persona ASC, item_key ASC
    LIMIT ?
  `).all(
    Number(cutoffMs),
    after.updatedAt,
    after.uuid,
    after.persona,
    after.itemKey,
    Number(maxRows),
  );
}

export function findNearbyAioPlaybackDelivery(db, row, windowMs = DELIVERY_MATCH_MS) {
  const at = Number(row.updated_at);
  return db.prepare(`
    SELECT event, status, created_at, delivered_at, last_error
    FROM watch_deliveries
    WHERE
      item_key = ?
      AND event IN ('start', 'pause', 'stop')
      AND created_at BETWEEN ? AND ?
    ORDER BY ABS(created_at - ?) ASC
    LIMIT 1
  `).get(String(row.item_key), at - windowMs, at + windowMs, at) || null;
}

export function classifyAioResumeCandidate(row, delivery = null) {
  const positionMs = Number(row.position_ms || 0);
  const durationMs = Number(row.duration_ms || 0);
  const progressPercent = durationMs > 0
    ? Number(((positionMs / durationMs) * 100).toFixed(3))
    : null;
  if (delivery && DELIVERY_EVENTS.includes(String(delivery.event))) {
    return {
      decision: 'covered_by_aio_playback_delivery',
      candidate: false,
      progressPercent,
      delivery: {
        event: String(delivery.event),
        status: String(delivery.status || ''),
        createdAt: Number(delivery.created_at || 0),
        deliveredAt: delivery.delivered_at == null ? null : Number(delivery.delivered_at),
        lastError: delivery.last_error == null ? null : String(delivery.last_error),
      },
    };
  }
  return {
    decision: 'candidate_missing_aio_playback_delivery',
    candidate: true,
    progressPercent,
    delivery: null,
  };
}

function connectedProfileId(db) {
  const connected = db.listProfiles().filter((profile) => profile.access_token_enc);
  return connected.length === 1 ? connected[0].id : null;
}

function logDetection(db, profileId, row, classification) {
  const eventId = `reconcile|aio|${createHash('sha256')
    .update(`${row.uuid}\u001f${row.persona || ''}\u001f${row.item_key}`)
    .digest('hex')
    .slice(0, 16)}|${row.updated_at}`;
  const detail = {
    action: 'aio-reconcile:detect',
    decision: classification.decision,
    itemKey: row.item_key,
    kind: row.kind,
    videoId: row.video_id || null,
    baseId: row.base_id,
    season: row.season == null ? null : Number(row.season),
    episode: row.episode == null ? null : Number(row.episode),
    positionMs: Number(row.position_ms),
    durationMs: Number(row.duration_ms),
    progressPercent: classification.progressPercent,
    updatedAt: Number(row.updated_at),
    origin: row.origin,
    delivery: classification.delivery,
    writesTrakt: false,
  };
  db.logEvent({
    profileId,
    eventId,
    event: 'reconcile',
    status: 'ignored',
    detail: JSON.stringify(detail),
  });
  db.cacheSet(rowStateKey(row), {
    updatedAt: Number(row.updated_at),
    positionMs: Number(row.position_ms),
    decision: classification.decision,
  }, CACHE_TTL_SECONDS);
  return detail;
}

export function seedAioReconcileCursor(db, updatedAt = Date.now()) {
  const cursor = defaultCursor(Number(updatedAt));
  db.cacheSet(CURSOR_KEY, cursor, CACHE_TTL_SECONDS);
  return cursor;
}

export function reconcileAioOnce({ config, db, nowMs = Date.now() }) {
  if (config.aioReconcilerMode !== 'detect') {
    return { status: 'disabled', processed: 0, candidates: 0 };
  }
  if (!config.aioDbPath || !existsSync(config.aioDbPath)) {
    return { status: 'unavailable', reason: 'aio_db_missing', processed: 0, candidates: 0 };
  }

  const profileId = connectedProfileId(db);
  if (!profileId) {
    return { status: 'skipped', reason: 'requires_exactly_one_connected_profile', processed: 0, candidates: 0 };
  }

  let cursor = db.cacheGet(CURSOR_KEY);
  if (!cursor) {
    cursor = seedAioReconcileCursor(db, nowMs);
    return { status: 'baseline', cursor, processed: 0, candidates: 0 };
  }
  cursor = normalizeCursor(cursor);

  const cutoffMs = Number(nowMs) - (Number(config.aioReconcileGraceSeconds) * 1000);
  const aio = openAioReadOnlyDatabase(config.aioDbPath);
  try {
    const rows = readAioResumeCandidates(aio, {
      cursor,
      cutoffMs,
      maxRows: config.aioReconcileMaxRows,
    });
    let candidates = 0;
    let lastCursor = cursor;
    const details = [];
    for (const row of rows) {
      const delivery = findNearbyAioPlaybackDelivery(aio, row);
      const classification = classifyAioResumeCandidate(row, delivery);
      if (classification.candidate) candidates += 1;
      const detail = logDetection(db, profileId, row, classification);
      details.push(detail);
      lastCursor = cursorForRow(row);
    }
    if (rows.length) db.cacheSet(CURSOR_KEY, lastCursor, CACHE_TTL_SECONDS);
    return {
      status: 'ok',
      processed: rows.length,
      candidates,
      cursor: lastCursor,
      details,
    };
  } finally {
    aio.close();
  }
}

export function startAioReconciler({ config, db }) {
  if (config.aioReconcilerMode !== 'detect') {
    return { stop() {} };
  }

  let running = false;
  let stopped = false;
  let lastError = null;

  const run = () => {
    if (running || stopped) return;
    running = true;
    try {
      const result = reconcileAioOnce({ config, db });
      if (result.status === 'ok' && (result.processed || result.candidates)) {
        console.log(JSON.stringify({
          level: 'info',
          event: 'aio_reconcile_detect',
          processed: result.processed,
          candidates: result.candidates,
          writesTrakt: false,
        }));
      }
      lastError = null;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message !== lastError) {
        console.warn(JSON.stringify({
          level: 'warn',
          event: 'aio_reconcile_error',
          message,
          writesTrakt: false,
        }));
        lastError = message;
      }
    } finally {
      running = false;
    }
  };

  console.log(JSON.stringify({
    level: 'info',
    event: 'aio_reconcile_start',
    mode: 'detect',
    dbPath: config.aioDbPath,
    intervalSeconds: config.aioReconcileIntervalSeconds,
    graceSeconds: config.aioReconcileGraceSeconds,
    writesTrakt: false,
  }));

  run();
  const timer = setInterval(run, config.aioReconcileIntervalSeconds * 1000);
  timer.unref();

  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
  };
}
