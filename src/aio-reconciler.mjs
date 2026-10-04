import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

const CURSOR_KEY = 'aio-reconcile:v2:cursor';
const PENDING_KEY = 'aio-reconcile:v2:pending';
const SETTLED_PREFIX = 'aio-reconcile:v2:settled:';
const COMPARE_PENDING_KEY = 'aio-reconcile:v3:compare-pending';
const COMPARE_SETTLED_PREFIX = 'aio-reconcile:v3:compare-settled:';
const COMPARE_STATS_KEY = 'aio-reconcile:v3:compare-stats';
const COMPARE_RETRY_BASE_MS = 60_000;
const COMPARE_RETRY_MAX_MS = 15 * 60_000;
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

function digestForPending(item) {
  return createHash('sha256')
    .update(`${item.uuid}\u001f${item.persona || ''}\u001f${item.itemKey}`)
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

function comparePendingMap(db) {
  const value = db.cacheGet(COMPARE_PENDING_KEY);
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value
    : {};
}

function storeComparePendingMap(db, value) {
  db.cacheSet(COMPARE_PENDING_KEY, value, CACHE_TTL_SECONDS);
}

function compareStats(db) {
  const value = db.cacheGet(COMPARE_STATS_KEY);
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value
    : {
        total: 0,
        sameOrNewer: 0,
        staleCandidate: 0,
        missingCandidate: 0,
        ambiguous: 0,
        lastDecision: null,
        lastAt: null,
      };
}

function rememberCompareDecision(db, decision, nowMs = Date.now()) {
  const stats = { ...compareStats(db) };
  stats.total = Number(stats.total || 0) + 1;
  if (decision === 'trakt_same_or_newer') {
    stats.sameOrNewer = Number(stats.sameOrNewer || 0) + 1;
  } else if (decision === 'trakt_stale_candidate') {
    stats.staleCandidate = Number(stats.staleCandidate || 0) + 1;
  } else if (decision === 'trakt_playback_missing_candidate') {
    stats.missingCandidate = Number(stats.missingCandidate || 0) + 1;
  } else {
    stats.ambiguous = Number(stats.ambiguous || 0) + 1;
  }
  stats.lastDecision = decision;
  stats.lastAt = Number(nowMs);
  db.cacheSet(COMPARE_STATS_KEY, stats, CACHE_TTL_SECONDS);
  return stats;
}

export function aioReconcileOperationalSnapshot(
  db,
  nowMs = Date.now(),
) {
  const detectPending = pendingMap(db);
  const comparePending = comparePendingMap(db);
  const stats = compareStats(db);
  const compareItems = Object.values(comparePending);

  return {
    detectPending: Object.keys(detectPending).length,
    compare: {
      awaiting: compareItems.length,
      retrying: compareItems.filter(
        (item) => Number(item.nextAttemptAt || 0) > Number(nowMs)
      ).length,
      total: Number(stats.total || 0),
      sameOrNewer: Number(stats.sameOrNewer || 0),
      staleCandidate: Number(stats.staleCandidate || 0),
      missingCandidate: Number(stats.missingCandidate || 0),
      ambiguous: Number(stats.ambiguous || 0),
      lastDecision: stats.lastDecision || null,
      lastAt: stats.lastAt == null ? null : Number(stats.lastAt),
    },
  };
}

function stageComparePending(db, profileId, row, firstSeenAt, nowMs) {
  const pending = comparePendingMap(db);
  const key = digestForRow(row);
  const previous = pending[key];

  if (!previous || Number(row.updated_at) > Number(previous.updatedAt)) {
    pending[key] = {
      ...rowToPending(row, firstSeenAt || nowMs),
      profileId,
      compareFirstSeenAt: Number(previous?.compareFirstSeenAt || nowMs),
      attempts: 0,
      nextAttemptAt: 0,
      lastError: null,
    };
    storeComparePendingMap(db, pending);
  }
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

function parseProviderIdentity(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;

  if (/^tt\d+$/i.test(raw)) {
    return { provider: 'imdb', value: raw.toLowerCase() };
  }

  const imdb = raw.match(/^imdb:(tt\d+)$/i);
  if (imdb) {
    return { provider: 'imdb', value: imdb[1].toLowerCase() };
  }

  const numeric = raw.match(/^(trakt|tmdb|tvdb):(\d+)$/i);
  if (numeric) {
    return {
      provider: numeric[1].toLowerCase(),
      value: Number(numeric[2]),
    };
  }

  return null;
}

function idsContain(ids, identity) {
  if (!ids || !identity) return false;
  const actual = ids[identity.provider];
  if (actual == null) return false;
  if (identity.provider === 'imdb') {
    return String(actual).toLowerCase() === String(identity.value).toLowerCase();
  }
  return Number(actual) === Number(identity.value);
}

function playbackPausedAtMs(row) {
  const raw = row?.paused_at || row?.updated_at || null;
  if (!raw) return null;
  const value = Date.parse(String(raw));
  return Number.isFinite(value) ? value : null;
}

function playbackIdentity(row, kind) {
  if (kind === 'movie') {
    return {
      kind,
      ids: row?.movie?.ids || null,
    };
  }
  return {
    kind,
    showIds: row?.show?.ids || null,
    season: row?.episode?.season == null
      ? null
      : Number(row.episode.season),
    episode: row?.episode?.number == null
      ? null
      : Number(row.episode.number),
    episodeIds: row?.episode?.ids || null,
  };
}

export function classifyTraktPlaybackCandidate(
  item,
  playbackRows,
  { positionToleranceMs = 2000 } = {},
) {
  const identity = parseProviderIdentity(item.baseId);
  if (!identity) {
    return {
      decision: 'trakt_compare_ambiguous',
      reason: 'candidate_identity_unusable',
      candidate: false,
      trakt: null,
    };
  }

  const kind = String(item.kind);
  const matches = (playbackRows || []).filter((row) => {
    if (kind === 'movie') {
      return idsContain(row?.movie?.ids, identity);
    }

    if (kind === 'episode') {
      return idsContain(row?.show?.ids, identity)
        && Number(row?.episode?.season) === Number(item.season)
        && Number(row?.episode?.number) === Number(item.episode);
    }

    return false;
  });

  if (matches.length === 0) {
    return {
      decision: 'trakt_playback_missing_candidate',
      reason: 'no_matching_trakt_playback',
      candidate: true,
      trakt: null,
    };
  }

  if (matches.length !== 1) {
    return {
      decision: 'trakt_compare_ambiguous',
      reason: 'multiple_matching_trakt_playback_rows',
      candidate: false,
      trakt: null,
    };
  }

  const row = matches[0];
  const traktProgress = Number(row?.progress);
  if (!Number.isFinite(traktProgress) || traktProgress < 0 || traktProgress > 100) {
    return {
      decision: 'trakt_compare_ambiguous',
      reason: 'trakt_progress_invalid',
      candidate: false,
      trakt: playbackIdentity(row, kind),
    };
  }

  const durationMs = Number(item.durationMs);
  const aioPositionMs = Number(item.positionMs);
  if (!(durationMs > 0) || !(aioPositionMs > 0)) {
    return {
      decision: 'trakt_compare_ambiguous',
      reason: 'candidate_position_invalid',
      candidate: false,
      trakt: playbackIdentity(row, kind),
    };
  }

  const traktPositionMs = Math.round((durationMs * traktProgress) / 100);
  const positionDeltaMs = traktPositionMs - aioPositionMs;
  const pausedAtMs = playbackPausedAtMs(row);
  const aioUpdatedAt = Number(item.updatedAt);

  const sameOrNewerByTime = Number.isFinite(pausedAtMs)
    && Number.isFinite(aioUpdatedAt)
    && pausedAtMs >= aioUpdatedAt;
  const sameOrNewerByPosition =
    traktPositionMs + Number(positionToleranceMs) >= aioPositionMs;

  if (sameOrNewerByTime || sameOrNewerByPosition) {
    return {
      decision: 'trakt_same_or_newer',
      reason: sameOrNewerByTime
        ? 'trakt_timestamp_same_or_newer'
        : 'trakt_position_same_or_newer',
      candidate: false,
      trakt: {
        ...playbackIdentity(row, kind),
        progress: traktProgress,
        positionMs: traktPositionMs,
        pausedAtMs,
        positionDeltaMs,
      },
    };
  }

  return {
    decision: 'trakt_stale_candidate',
    reason: 'trakt_older_and_behind',
    candidate: true,
    trakt: {
      ...playbackIdentity(row, kind),
      progress: traktProgress,
      positionMs: traktPositionMs,
      pausedAtMs,
      positionDeltaMs,
    },
  };
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
  compareEnabled = false,
  nowMs = Date.now(),
}) {
  const eventId = settledEventId(row);

  if (candidate && compareEnabled) {
    stageComparePending(
      db,
      profileId,
      row,
      firstSeenAt,
      nowMs,
    );
  }

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
        compareEnabled: config.aioReconcilerMode === 'compare',
        nowMs,
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
  if (!['detect', 'compare'].includes(config.aioReconcilerMode)) {
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

function compareEventId(item) {
  return `reconcile|aio-compare|${digestForPending(item)}|${item.updatedAt}`;
}

function compareSettledKey(item) {
  return `${COMPARE_SETTLED_PREFIX}${digestForPending(item)}`;
}

function retryAfterMs(value) {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 0) {
    if (numeric > 1e12) return Math.max(0, numeric - Date.now());
    if (numeric > 1e9) return Math.max(0, numeric * 1000 - Date.now());
    return numeric * 1000;
  }
  const date = Date.parse(String(value));
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : null;
}

function compareRetryDelayMs(item, error) {
  const explicit = retryAfterMs(error?.retryAfter);
  if (explicit != null) {
    return Math.min(
      COMPARE_RETRY_MAX_MS,
      Math.max(COMPARE_RETRY_BASE_MS, explicit),
    );
  }

  const attempts = Math.max(1, Number(item.attempts || 0) + 1);
  return Math.min(
    COMPARE_RETRY_MAX_MS,
    COMPARE_RETRY_BASE_MS * (2 ** Math.min(attempts - 1, 4)),
  );
}

function logCompareSettled(db, item, result) {
  const markerKey = compareSettledKey(item);
  const marker = db.cacheGet(markerKey);
  if (marker && Number(marker.updatedAt) === Number(item.updatedAt)) {
    return null;
  }

  const detail = {
    action: 'aio-reconcile:compare-settled',
    decision: result.decision,
    reason: result.reason,
    candidate: Boolean(result.candidate),
    itemKey: item.itemKey,
    kind: item.kind,
    videoId: item.videoId || null,
    baseId: item.baseId,
    season: item.season,
    episode: item.episode,
    positionMs: Number(item.positionMs),
    durationMs: Number(item.durationMs),
    progressPercent: progressPercent(item),
    updatedAt: Number(item.updatedAt),
    firstSeenAt: Number(item.firstSeenAt || item.updatedAt),
    compareFirstSeenAt: Number(
      item.compareFirstSeenAt || item.firstSeenAt || item.updatedAt
    ),
    trakt: result.trakt,
    writesTrakt: false,
  };

  db.cacheSet(markerKey, {
    updatedAt: Number(item.updatedAt),
    decision: result.decision,
  }, CACHE_TTL_SECONDS);
  rememberCompareDecision(db, result.decision);

  db.logEvent({
    profileId: item.profileId,
    eventId: compareEventId(item),
    event: 'reconcile',
    status: 'ignored',
    detail: JSON.stringify(detail),
  });

  return detail;
}

function markCompareUnavailable(item, error, nowMs) {
  const attempts = Number(item.attempts || 0) + 1;
  const delayMs = compareRetryDelayMs(item, error);
  return {
    ...item,
    attempts,
    nextAttemptAt: Number(nowMs) + delayMs,
    lastError: error?.code || error?.message || String(error),
  };
}

export async function compareAioCandidatesOnce({
  config,
  db,
  trakt,
  nowMs = Date.now(),
}) {
  if (config.aioReconcilerMode !== 'compare') {
    return {
      status: 'disabled',
      pending: 0,
      compared: 0,
      unavailable: 0,
    };
  }

  if (!trakt) {
    return {
      status: 'unavailable',
      reason: 'trakt_client_missing',
      pending: Object.keys(comparePendingMap(db)).length,
      compared: 0,
      unavailable: 0,
    };
  }

  const pending = comparePendingMap(db);
  const due = Object.entries(pending)
    .filter(([, item]) => Number(item.nextAttemptAt || 0) <= Number(nowMs))
    .slice(0, Number(config.aioReconcileMaxRows || 100));

  if (!due.length) {
    return {
      status: 'ok',
      pending: Object.keys(pending).length,
      compared: 0,
      sameOrNewer: 0,
      stale: 0,
      missing: 0,
      ambiguous: 0,
      unavailable: 0,
    };
  }

  let compared = 0;
  let sameOrNewer = 0;
  let stale = 0;
  let missing = 0;
  let ambiguous = 0;
  let unavailable = 0;
  const details = [];

  for (const kind of ['movie', 'episode']) {
    const group = due.filter(([, item]) => String(item.kind) === kind);
    if (!group.length) continue;

    let rows;
    try {
      rows = await trakt.requestAllPages(
        group[0][1].profileId,
        kind === 'movie'
          ? '/sync/playback/movies?extended=full'
          : '/sync/playback/episodes?extended=full',
        { limit: 100 },
      );
    } catch (error) {
      for (const [key, item] of group) {
        pending[key] = markCompareUnavailable(item, error, nowMs);
        unavailable += 1;
      }
      continue;
    }

    for (const [key, item] of group) {
      const result = classifyTraktPlaybackCandidate(item, rows, {
        positionToleranceMs:
          Number(config.aioReconcilePositionToleranceMs || 2000),
      });

      const detail = logCompareSettled(db, item, result);
      if (detail) {
        details.push(detail);
        compared += 1;
        if (result.decision === 'trakt_same_or_newer') sameOrNewer += 1;
        else if (result.decision === 'trakt_stale_candidate') stale += 1;
        else if (result.decision === 'trakt_playback_missing_candidate') {
          missing += 1;
        } else {
          ambiguous += 1;
        }
      }

      delete pending[key];
    }
  }

  for (const [key, item] of due) {
    if (['movie', 'episode'].includes(String(item.kind))) continue;
    const result = {
      decision: 'trakt_compare_ambiguous',
      reason: 'candidate_kind_unsupported',
      candidate: false,
      trakt: null,
    };
    const detail = logCompareSettled(db, item, result);
    if (detail) {
      details.push(detail);
      compared += 1;
      ambiguous += 1;
    }
    delete pending[key];
  }

  storeComparePendingMap(db, pending);

  return {
    status: 'ok',
    pending: Object.keys(pending).length,
    compared,
    sameOrNewer,
    stale,
    missing,
    ambiguous,
    unavailable,
    details,
  };
}

export function startAioReconciler({ config, db, trakt = null }) {
  if (!['detect', 'compare'].includes(config.aioReconcilerMode)) {
    return { stop() {} };
  }

  let running = false;
  let stopped = false;
  let lastError = null;

  const run = async () => {
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

      if (config.aioReconcilerMode === 'compare') {
        const compared = await compareAioCandidatesOnce({
          config,
          db,
          trakt,
        });

        if (
          compared.status === 'ok'
          && (
            compared.compared
            || compared.unavailable
          )
        ) {
          console.log(JSON.stringify({
            level: 'info',
            event: 'aio_reconcile_compare',
            pending: compared.pending,
            compared: compared.compared,
            sameOrNewer: compared.sameOrNewer,
            stale: compared.stale,
            missing: compared.missing,
            ambiguous: compared.ambiguous,
            unavailable: compared.unavailable,
            writesTrakt: false,
          }));
        }
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
    mode: config.aioReconcilerMode,
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

  void run();

  const timer = setInterval(
    () => void run(),
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
