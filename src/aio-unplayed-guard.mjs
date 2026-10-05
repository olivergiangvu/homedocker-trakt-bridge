import { existsSync } from 'node:fs';

import {
  findEquivalentAioPlaybackDelivery,
  openAioReadOnlyDatabase,
  resolveHomeDockerSink,
} from './aio-reconciler.mjs';

const EVENT_WINDOW_MS = 5_000;
const STATE_SKEW_MS = 2_000;

function eventTimestampMs(event) {
  const id = String(event?.id || '');
  const suffix = id.match(/\|(\d{12,})$/);
  if (suffix) {
    const value = Number(suffix[1]);
    if (Number.isFinite(value) && value > 0) return value;
  }

  const at = Number(event?.at);
  return Number.isFinite(at) && at > 0 ? Math.floor(at * 1000) : null;
}

function itemKeyForEvent(event) {
  if (event?.scope === 'episode') {
    const videoId = String(event?.videoId || '').trim();
    return videoId ? `e|${videoId}` : null;
  }

  if (event?.scope === 'movie') {
    const metaId = String(event?.metaId || '').trim();
    return metaId ? `m|${metaId}` : null;
  }

  return null;
}

function parseBody(value) {
  try {
    return JSON.parse(String(value || 'null'));
  } catch {
    return null;
  }
}

function matchingDeliveryRows(db, event, itemKey, sinkId, eventMs) {
  return db.prepare(`
    SELECT
      event, status, created_at, delivered_at, last_error, body
    FROM watch_deliveries
    WHERE
      sink_id = ?
      AND item_key = ?
      AND event = 'unplayed'
      AND created_at BETWEEN ? AND ?
    ORDER BY ABS(created_at - ?) ASC
    LIMIT 8
  `).all(
    String(sinkId),
    String(itemKey),
    Number(eventMs) - EVENT_WINDOW_MS,
    Number(eventMs) + EVENT_WINDOW_MS,
    Number(eventMs),
  ).filter((row) => parseBody(row.body)?.id === event.id);
}

function currentRow(db, scope, itemKey) {
  return db.prepare(`
    SELECT
      uuid, persona, item_key, kind, media_type, base_id,
      season, episode, video_id, position_ms, duration_ms,
      played, origin, sink_id, external_at, updated_at, last_played_at
    FROM watch_state
    WHERE uuid = ? AND persona = ? AND item_key = ?
    LIMIT 1
  `).get(
    String(scope.uuid),
    String(scope.persona || ''),
    String(itemKey),
  ) || null;
}

function matchingDuration(event, row) {
  const eventDuration = Number(event?.durationMs);
  const rowDuration = Number(row?.duration_ms);
  return Number.isFinite(eventDuration)
    && eventDuration > 0
    && Number.isFinite(rowDuration)
    && rowDuration > 0
    && Math.round(eventDuration) === Math.round(rowDuration);
}

function isContradictingResumeRow(event, row, delivery) {
  if (!row) return false;
  if (String(row.origin) !== 'local') return false;
  if (Number(row.played) !== 0) return false;
  if (!(Number(row.position_ms) > 0)) return false;
  if (!matchingDuration(event, row)) return false;

  const updatedAt = Number(row.updated_at);
  const deliveryAt = Number(delivery.created_at);
  if (!Number.isFinite(updatedAt) || !Number.isFinite(deliveryAt)) return false;

  // The VidHub/Jellyfin echo is produced inside one UserData request:
  // AIO first queues Played=false as unplayed, then writes the positive
  // PlaybackPositionTicks as a stop/resume row. Require that contradiction to
  // be contemporaneous rather than suppressing a later real Mark Unwatched.
  return updatedAt >= deliveryAt
    && updatedAt - deliveryAt <= STATE_SKEW_MS;
}

export function classifyAioFalseUnplayedEcho(
  aio,
  event,
  {
    sinkName = 'homedocker-trakt-bridge',
    sinkInstanceId = null,
    coverageLookbackMs = 180_000,
    positionToleranceMs = 2_000,
  } = {},
) {
  if (event?.event !== 'unplayed') return null;
  if (!['movie', 'episode'].includes(String(event?.scope || ''))) return null;
  if (event?.played !== false) return null;
  if (Number(event?.positionMs) !== 0) return null;

  const itemKey = itemKeyForEvent(event);
  const eventMs = eventTimestampMs(event);
  if (!itemKey || !eventMs) return null;

  // Resolve the exact AIO user/persona through the configured HomeDocker sink.
  const sinkRows = aio.prepare(`
    SELECT
      id, uuid, persona, addon_instance_id, addon_name, status, updated_at
    FROM watch_sinks
    WHERE addon_name = ?
      ${sinkInstanceId ? 'AND addon_instance_id = ?' : ''}
    ORDER BY updated_at DESC, id ASC
  `).all(
    String(sinkName),
    ...(
      sinkInstanceId
        ? [String(sinkInstanceId)]
        : []
    ),
  );

  if (sinkRows.length !== 1) return null;
  const scope = sinkRows[0];

  const exactDeliveries = matchingDeliveryRows(
    aio,
    event,
    itemKey,
    scope.id,
    eventMs,
  );
  if (exactDeliveries.length !== 1) return null;
  const unplayedDelivery = exactDeliveries[0];

  const row = currentRow(aio, scope, itemKey);
  if (!isContradictingResumeRow(event, row, unplayedDelivery)) return null;

  const resolved = resolveHomeDockerSink(aio, row, {
    sinkName,
    sinkInstanceId,
  });
  if (resolved.status !== 'ok') return null;

  // Suppress only when the same positive resume position already has a
  // delivered HomeDocker playback event. If the positive playback never made
  // it to the bridge, fail open and preserve the existing history-remove path.
  const playback = findEquivalentAioPlaybackDelivery(
    aio,
    row,
    resolved.sink.id,
    {
      lookbackMs: Number(coverageLookbackMs),
      positionToleranceMs: Number(positionToleranceMs),
    },
  );
  if (!playback) return null;

  return {
    action: 'history:guarded',
    ignored: 'aio_false_unplayed_echo',
    itemKey,
    eventCreatedAt: Number(unplayedDelivery.created_at),
    stateUpdatedAt: Number(row.updated_at),
    stateDeltaMs:
      Number(row.updated_at) - Number(unplayedDelivery.created_at),
    aioPositionMs: Number(row.position_ms),
    aioDurationMs: Number(row.duration_ms),
    playbackEvent: String(playback.event),
    playbackPositionMs: Number(playback.body_position_ms),
    playbackPositionDeltaMs: Number(playback.position_delta_ms),
    sinkInstanceId: resolved.sink.addonInstanceId,
    writesTrakt: false,
  };
}

export function detectAioFalseUnplayedEcho(config, event) {
  if (!config?.aioUnplayedEchoGuard) return null;
  if (!config?.aioDbPath || !existsSync(config.aioDbPath)) return null;

  let aio;
  try {
    aio = openAioReadOnlyDatabase(config.aioDbPath);
    return classifyAioFalseUnplayedEcho(
      aio,
      event,
      {
        sinkName: config.aioReconcileSinkName,
        sinkInstanceId: config.aioReconcileSinkInstanceId,
        coverageLookbackMs:
          Number(config.aioReconcileCoverageLookbackSeconds) * 1000,
        positionToleranceMs:
          Number(config.aioReconcilePositionToleranceMs),
      },
    );
  } catch {
    // Guard failure must never convert an intentional Mark Unwatched into an
    // ignore. Fail open to the pre-existing Trakt history-remove behavior.
    return null;
  } finally {
    aio?.close();
  }
}
