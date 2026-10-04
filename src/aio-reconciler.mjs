import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

const CURSOR_KEY = 'aio-reconcile:v2:cursor';
const PENDING_KEY = 'aio-reconcile:v2:pending';
const SETTLED_PREFIX = 'aio-reconcile:v2:settled:';
const CACHE_TTL_SECONDS = 10 * 365 * 24 * 3600;

const REQUIRED_WATCH_STATE_COLUMNS = [
  'uuid', 'persona', 'item_key', 'kind', 'media_type', 'base_id',
  'season', 'episode', 'video_id', 'position_ms', 'duration_ms',
  'played', 'origin', 'sink_id', 'external_at', 'updated_at', 'last_played_at',
];

const REQUIRED_SINK_COLUMNS = [
  'id', 'uuid', 'persona', 'addon_instance_id', 'addon_name',
  'status', 'updated_at',
];

const REQUIRED_DELIVERY_COLUMNS = [
  'sink_id', 'item_key', 'event', 'status', 'body',
  'created_at', 'delivered_at', 'last_error',
];

const PLAYBACK_EVENTS = ['start', 'pause', 'stop'];
const DELIVERY_FORWARD_MS = 15_000;

function defaultCursor(updatedAt = 0) {
  return { updatedAt, uuid: '', persona: '', itemKey: '' };
}

function normalizeCursor(value, fallback = 0) {
  if (!value || !Number.isFinite(Number(value.updatedAt))) {
    return defaultCursor(fallback);
  }
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

function sourceKey(row) {
  return `${row.uuid}\u001f${row.persona || ''}\u001f${row.item_key}`;
}

function digestForRow(row) {
  return createHash('sha256')
    .update(sourceKey(row))
    .digest('hex')
    .slice(0, 24);
}

function settledKey(row) {
  return `${SETTLED_PREFIX}${digestForRow(row)}`;
}

function tableColumns(db, table) {
  return new Set(
    db.prepare(`PRAGMA table_info(${table})`)
      .all()
      .map((row) => String(row.name))
  );
}

function requireColumns(db, table, required) {
  const columns = tableColumns(db, table);
  const missing = required.filter((column) => !columns.has(column));
  if (missing.length) {
    throw new Error(
      `AIO database schema incompatible: ${table} missing ${missing.join(', ')}`
    );
  }
}

export function openAioReadOnlyDatabase(path) {
  const db = new DatabaseSync(path, { readOnly: true, timeout: 1000 });
  db.exec('PRAGMA query_only=ON');
  requireColumns(db, 'watch_state', REQUIRED_WATCH_STATE_COLUMNS);
  requireColumns(db, 'watch_sinks', REQUIRED_SINK_COLUMNS);
  requireColumns(db, 'watch_deliveries', REQUIRED_DELIVERY_COLUMNS);
  return db;
}

function isResumeRow(row) {
  return row
    && String(row.origin) === 'local'
    && Number(row.played) === 0
    && Number(row.position_ms) > 0
    && Number(row.duration_ms) > 0
    && ['movie', 'episode'].includes(String(row.kind));
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

export function readCurrentAioRow(db, pending) {
  return db.prepare(`
    SELECT
      uuid, persona, item_key, kind, media_type, base_id,
      season, episode, video_id, position_ms, duration_ms,
      played, origin, sink_id, external_at, updated_at, last_played_at
    FROM watch_state
    WHERE uuid = ? AND persona = ? AND item_key = ?
    LIMIT 1
  `).get(
    String(pending.uuid),
    String(pending.persona || ''),
    String(pending.itemKey),
  ) || null;
}

export function resolveHomeDockerSink(db, row, {
  sinkName = 'homedocker-trakt-bridge',
  sinkInstanceId = null,
} = {}) {
  const params = [
    String(row.uuid),
    String(row.persona || ''),
    String(sinkName),
  ];

  let sql = `
    SELECT id, addon_instance_id, addon_name, status, updated_at
    FROM watch_sinks
    WHERE uuid = ? AND persona = ? AND addon_name = ?
  `;

  if (sinkInstanceId) {
    sql += ' AND addon_instance_id = ?';
    params.push(String(sinkInstanceId));
  }

  sql += ' ORDER BY updated_at DESC, id ASC';

  const rows = db.prepare(sql).all(...params);
  if (rows.length !== 1) {
    return {
      status: rows.length ? 'ambiguous' : 'missing',
      sink: null,
      matches: rows.map((r) => ({
        id: String(r.id),
        addonInstanceId: String(r.addon_instance_id),
        addonName: String(r.addon_name || ''),
        status: String(r.status || ''),
      })),
    };
  }

  const sink = rows[0];
  return {
    status: 'ok',
    sink: {
      id: String(sink.id),
      addonInstanceId: String(sink.addon_instance_id),
      addonName: String(sink.addon_name || ''),
      status: String(sink.status || ''),
    },
    matches: [],
  };
}

export function findEquivalentAioPlaybackDelivery(
  db,
  row,
  sinkId,
  {
    lookbackMs,
    forwardMs = DELIVERY_FORWARD_MS,
    positionToleranceMs,
  },
) {
  const at = Number(row.updated_at);
  const positionMs = Number(row.position_ms);

  const rows = db.prepare(`
    SELECT
      event, status, created_at, delivered_at, last_error, body
    FROM watch_deliveries
    WHERE
      sink_id = ?
      AND item_key = ?
      AND status = 'delivered'
      AND event IN ('start', 'pause', 'stop')
      AND created_at BETWEEN ? AND ?
    ORDER BY ABS(created_at - ?) ASC
  `).all(
    String(sinkId),
    String(row.item_key),
    at - Number(lookbackMs),
    at + Number(forwardMs),
    at,
  );

  for (const delivery of rows) {
    let body = null;
    try {
      body = JSON.parse(String(delivery.body || 'null'));
    } catch {
      body = null;
    }

    const deliveredPositionMs = Number(body?.positionMs);
    if (!Number.isFinite(deliveredPositionMs) || deliveredPositionMs <= 0) {
      continue;
    }

    const positionDeltaMs = Math.abs(deliveredPositionMs - positionMs);
    if (positionDeltaMs > Number(positionToleranceMs)) {
      continue;
    }

    return {
      ...delivery,
      body_position_ms: deliveredPositionMs,
      position_delta_ms: positionDeltaMs,
    };
  }

  return null;
}

function rowToPending(row, firstSeenAt = Date.now()) {
  return {
    uuid: String(row.uuid),
    persona: String(row.persona || ''),
    itemKey: String(row.item_key),
    kind: String(row.kind),
    mediaType: String(row.media_type),
    baseId: String(row.base_id),
    season: row.season == null ? null : Number(row.season),
    episode: row.episode == null ? null : Number(row.episode),
    videoId: row.video_id == null ? null : String(row.video_id),
    positionMs: Number(row.position_ms),
    durationMs: Number(row.duration_ms),
    updatedAt: Number(row.updated_at),
    lastPlayedAt: row.last_played_at == null
      ? null
      : Number(row.last_played_at),
    firstSeenAt: Number(firstSeenAt),
  };
}

function pendingMap(db) {
  const value = db.cacheGet(PENDING_KEY);
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value
    : {};
}

function storePendingMap(db, value) {
  db.cacheSet(PENDING_KEY, value, CACHE_TTL_SECONDS);
}

function stagePendingRows(db, pending, rows, nowMs) {
  let staged = 0;
  let replaced = 0;

  for (const row of rows) {
    const key = digestForRow(row);
    const previous = pending[key];

    if (!previous) {
      pending[key] = rowToPending(row, nowMs);
      staged += 1;
      continue;
    }

    if (Number(row.updated_at) > Number(previous.updatedAt)) {
      pending[key] = rowToPending(row, previous.firstSeenAt || nowMs);
      replaced += 1;
    }
  }

  return { staged, replaced };
}

function progressPercent(row) {
  const positionMs = Number(row.position_ms || row.positionMs || 0);
  const durationMs = Number(row.duration_ms || row.durationMs || 0);
  return durationMs > 0
    ? Number(((positionMs / durationMs) * 100).toFixed(3))
    : null;
}

function deliveryDetail(delivery) {
  if (!delivery || !PLAYBACK_EVENTS.includes(String(delivery.event))) {
    return null;
  }
  return {
    event: String(delivery.event),
    status: String(delivery.status || ''),
    createdAt: Number(delivery.created_at || 0),
    deliveredAt: delivery.delivered_at == null
      ? null
      : Number(delivery.delivered_at),
    lastError: delivery.last_error == null
      ? null
      : String(delivery.last_error),
    positionMs: delivery.body_position_ms == null
      ? null
      : Number(delivery.body_position_ms),
    positionDeltaMs: delivery.position_delta_ms == null
      ? null
      : Number(delivery.position_delta_ms),
  };
}

function settledEventId(row) {
  return `reconcile|aio-settled|${digestForRow(row)}|${row.updated_at}`;
}

function logSettled(db, profileId, row, {
  decision,
  candidate,
  sink,
  delivery = null,
  quietSeconds,
  firstSeenAt,
}) {
  const eventId = settledEventId(row);
  const marker = db.cacheGet(settledKey(row));

  if (marker && Number(marker.updatedAt) === Number(row.updated_at)) {
    return null;
  }

  const detail = {
    action: 'aio-reconcile:detect-settled',
    decision,
    settled: true,
    candidate: Boolean(candidate),
    itemKey: row.item_key,
    kind: row.kind,
    videoId: row.video_id || null,
    baseId: row.base_id,
    season: row.season == null ? null : Number(row.season),
    episode: row.episode == null ? null : Number(row.episode),
    positionMs: Number(row.position_ms),
    durationMs: Number(row.duration_ms),
    progressPercent: progressPercent(row),
    updatedAt: Number(row.updated_at),
    firstSeenAt: Number(firstSeenAt || row.updated_at),
    quietSeconds: Number(quietSeconds),
    origin: row.origin,
    sink: sink
      ? {
          addonInstanceId: sink.addonInstanceId,
          addonName: sink.addonName,
          status: sink.status,
        }
      : null,
    delivery: deliveryDetail(delivery),
    writesTrakt: false,
  };

  db.cacheSet(settledKey(row), {
    updatedAt: Number(row.updated_at),
    positionMs: Number(row.position_ms),
    decision,
  }, CACHE_TTL_SECONDS);

  db.logEvent({
    profileId,
    eventId,
    event: 'reconcile',
    status: 'ignored',
    detail: JSON.stringify(detail),
  });

  return detail;
}

export function seedAioReconcileCursor(db, updatedAt = Date.now()) {
  const cursor = defaultCursor(Number(updatedAt));
  db.cacheSet(CURSOR_KEY, cursor, CACHE_TTL_SECONDS);
  storePendingMap(db, {});
  return cursor;
}

function connectedProfileId(db) {
  const connected = db.listProfiles()
    .filter((profile) => profile.access_token_enc);
  return connected.length === 1 ? connected[0].id : null;
}

function settlePendingRows({
  aio,
  bridgeDb,
  profileId,
  pending,
  config,
  nowMs,
}) {
  const details = [];
  let settled = 0;
  let candidates = 0;
  let covered = 0;
  let refreshed = 0;
  let dropped = 0;
  let blocked = 0;

  for (const [key, item] of Object.entries(pending)) {
    const current = readCurrentAioRow(aio, item);

    if (!current || !isResumeRow(current)) {
      delete pending[key];
      dropped += 1;
      continue;
    }

    if (Number(current.updated_at) > Number(item.updatedAt)) {
      pending[key] = rowToPending(
        current,
        item.firstSeenAt || nowMs,
      );
      refreshed += 1;
      continue;
    }

    const quietMs = Number(config.aioReconcileQuietSeconds) * 1000;
    if (Number(nowMs) - Number(current.updated_at) < quietMs) {
      continue;
    }

    const resolved = resolveHomeDockerSink(aio, current, {
      sinkName: config.aioReconcileSinkName,
      sinkInstanceId: config.aioReconcileSinkInstanceId,
    });

    if (resolved.status !== 'ok') {
      blocked += 1;
      continue;
    }

    const delivery = findEquivalentAioPlaybackDelivery(
      aio,
      current,
      resolved.sink.id,
      {
        lookbackMs:
          Number(config.aioReconcileCoverageLookbackSeconds) * 1000,
        positionToleranceMs:
          Number(config.aioReconcilePositionToleranceMs),
      },
    );

    const isCovered = Boolean(delivery);
    const decision = isCovered
      ? 'covered_by_homedocker_playback_delivery'
      : 'settled_missing_homedocker_playback_delivery';

    const detail = logSettled(
      bridgeDb,
      profileId,
      current,
      {
        decision,
        candidate: !isCovered,
        sink: resolved.sink,
        delivery,
        quietSeconds: config.aioReconcileQuietSeconds,
        firstSeenAt: item.firstSeenAt,
      },
    );

    if (detail) {
      details.push(detail);
      settled += 1;
      if (isCovered) covered += 1;
      else candidates += 1;
    }

    delete pending[key];
  }

  return {
    settled,
    candidates,
    covered,
    refreshed,
    dropped,
    blocked,
    details,
  };
}

export function reconcileAioOnce({
  config,
  db,
  nowMs = Date.now(),
}) {
  if (config.aioReconcilerMode !== 'detect') {
    return {
      status: 'disabled',
      observed: 0,
      pending: 0,
      settled: 0,
      candidates: 0,
    };
  }

  if (!config.aioDbPath || !existsSync(config.aioDbPath)) {
    return {
      status: 'unavailable',
      reason: 'aio_db_missing',
      observed: 0,
      pending: 0,
      settled: 0,
      candidates: 0,
    };
  }

  const profileId = connectedProfileId(db);
  if (!profileId) {
    return {
      status: 'skipped',
      reason: 'requires_exactly_one_connected_profile',
      observed: 0,
      pending: 0,
      settled: 0,
      candidates: 0,
    };
  }

  let cursor = db.cacheGet(CURSOR_KEY);
  if (!cursor) {
    cursor = seedAioReconcileCursor(db, nowMs);
    return {
      status: 'baseline',
      cursor,
      observed: 0,
      pending: 0,
      settled: 0,
      candidates: 0,
    };
  }

  cursor = normalizeCursor(cursor);

  const cutoffMs = Number(nowMs)
    - (Number(config.aioReconcileGraceSeconds) * 1000);

  const aio = openAioReadOnlyDatabase(config.aioDbPath);

  try {
    const rows = readAioResumeCandidates(aio, {
      cursor,
      cutoffMs,
      maxRows: config.aioReconcileMaxRows,
    });

    const pending = pendingMap(db);
    const staged = stagePendingRows(db, pending, rows, nowMs);

    let lastCursor = cursor;
    for (const row of rows) {
      lastCursor = cursorForRow(row);
    }

    if (rows.length) {
      db.cacheSet(CURSOR_KEY, lastCursor, CACHE_TTL_SECONDS);
    }

    const settled = settlePendingRows({
      aio,
      bridgeDb: db,
      profileId,
      pending,
      config,
      nowMs,
    });

    storePendingMap(db, pending);

    return {
      status: 'ok',
      observed: rows.length,
      staged: staged.staged,
      replaced: staged.replaced,
      pending: Object.keys(pending).length,
      cursor: lastCursor,
      ...settled,
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

      if (
        result.status === 'ok'
        && (
          result.observed
          || result.settled
          || result.refreshed
          || result.dropped
          || result.blocked
        )
      ) {
        console.log(JSON.stringify({
          level: 'info',
          event: 'aio_reconcile_detect',
          observed: result.observed,
          pending: result.pending,
          settled: result.settled,
          candidates: result.candidates,
          covered: result.covered,
          refreshed: result.refreshed,
          dropped: result.dropped,
          blocked: result.blocked,
          writesTrakt: false,
        }));
      }

      lastError = null;
    } catch (error) {
      const message = error instanceof Error
        ? error.message
        : String(error);

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
    quietSeconds: config.aioReconcileQuietSeconds,
    coverageLookbackSeconds:
      config.aioReconcileCoverageLookbackSeconds,
    positionToleranceMs:
      config.aioReconcilePositionToleranceMs,
    sinkName: config.aioReconcileSinkName,
    sinkInstanceId: config.aioReconcileSinkInstanceId || null,
    writesTrakt: false,
  }));

  run();

  const timer = setInterval(
    run,
    config.aioReconcileIntervalSeconds * 1000,
  );
  timer.unref();

  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
  };
}
