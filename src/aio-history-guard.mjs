import { existsSync } from 'node:fs';

import {
  canonicalHistoryState,
} from './canonical-history.mjs';
import {
  loadAioHistoryEvidence,
} from './aio-history-evidence.mjs';
import {
  detectAioFalseUnplayedEcho,
} from './aio-unplayed-guard.mjs';
import {
  openAioReadOnlyDatabase,
} from './aio-reconciler.mjs';

const STATE_SKEW_MS = 2_000;

function parseBody(value) {
  try {
    return JSON.parse(String(value || 'null'));
  } catch {
    return null;
  }
}

function eventTimestampMs(event) {
  const id = String(event?.id || '');
  const suffix = id.match(/\|(\d{12,})$/);
  if (suffix) {
    const value = Number(suffix[1]);
    if (Number.isFinite(value) && value > 0) return value;
  }

  const at = Number(event?.at);
  return Number.isFinite(at) && at > 0
    ? Math.floor(at * 1000)
    : null;
}

function exactSink(aio, {
  sinkName,
  sinkInstanceId,
}) {
  const params = [String(sinkName)];
  let sql = `
    SELECT id, addon_instance_id, addon_name
    FROM watch_sinks
    WHERE addon_name = ?
  `;

  if (sinkInstanceId) {
    sql += ' AND addon_instance_id = ?';
    params.push(String(sinkInstanceId));
  }

  sql += ' ORDER BY updated_at DESC, id ASC';

  const rows = aio.prepare(sql).all(...params);
  return rows.length === 1 ? rows[0] : null;
}

function matchingDuration(event, state) {
  const eventDuration = Number(event?.durationMs);
  const stateDuration = Number(state?.durationMs);

  return Number.isFinite(eventDuration)
    && eventDuration > 0
    && Number.isFinite(stateDuration)
    && stateDuration > 0
    && Math.round(eventDuration) === Math.round(stateDuration);
}

function evidenceUnplayedEcho(event, evidence) {
  if (event?.event !== 'unplayed') return null;
  if (event?.played !== false) return null;
  if (Number(event?.positionMs) !== 0) return null;

  const state = evidence?.state;
  if (!state) return null;
  if (String(state.origin) !== 'local') return null;
  if (Number(state.played) !== 0) return null;
  if (!matchingDuration(event, state)) return null;

  const deliveryAt = Number(evidence.deliveryCreatedAt);
  const updatedAt = Number(state.updatedAt);
  if (
    !Number.isFinite(deliveryAt)
    || !Number.isFinite(updatedAt)
    || updatedAt < deliveryAt
    || updatedAt - deliveryAt > STATE_SKEW_MS
  ) {
    return null;
  }

  if (Number(state.positionMs) > 0) {
    return {
      action: 'history:guarded',
      ignored: 'aio_false_unplayed_echo',
      guardVariant: 'event_time_positive_resume',
      itemKey: evidence.itemKey || null,
      eventCreatedAt: deliveryAt,
      stateUpdatedAt: updatedAt,
      stateDeltaMs: updatedAt - deliveryAt,
      aioPositionMs: Number(state.positionMs),
      aioDurationMs: Number(state.durationMs),
      sinkInstanceId: evidence.sinkInstanceId || null,
      writesTrakt: false,
    };
  }

  if (Number(state.positionMs) !== 0) return null;

  const lastPlayedAt = Number(state.lastPlayedAt);
  if (!Number.isFinite(lastPlayedAt)) return null;

  if (
    lastPlayedAt >= deliveryAt
    && lastPlayedAt - deliveryAt <= STATE_SKEW_MS
    && Math.abs(updatedAt - lastPlayedAt) <= STATE_SKEW_MS
  ) {
    return {
      action: 'history:guarded',
      ignored: 'aio_false_unplayed_echo',
      guardVariant: 'event_time_userdata_zero_position_stop',
      itemKey: evidence.itemKey || null,
      eventCreatedAt: deliveryAt,
      stateUpdatedAt: updatedAt,
      stateDeltaMs: updatedAt - deliveryAt,
      stateLastPlayedAt: lastPlayedAt,
      lastPlayedDeltaMs: lastPlayedAt - deliveryAt,
      aioPositionMs: 0,
      aioDurationMs: Number(state.durationMs),
      sinkInstanceId: evidence.sinkInstanceId || null,
      writesTrakt: false,
    };
  }

  return null;
}

function cohortForEvent(config, event) {
  if (!config?.aioDbPath || !existsSync(config.aioDbPath)) return null;
  if (!['played', 'unplayed'].includes(String(event?.event || ''))) return null;
  if (!['movie', 'episode'].includes(String(event?.scope || ''))) return null;
  if (Array.isArray(event?.videos)) return null;

  const eventMs = eventTimestampMs(event);
  if (!eventMs) return null;

  let aio;
  try {
    aio = openAioReadOnlyDatabase(config.aioDbPath);

    const sink = exactSink(aio, {
      sinkName: config.aioReconcileSinkName,
      sinkInstanceId: config.aioReconcileSinkInstanceId,
    });
    if (!sink) return null;

    const windowMs = Number(
      config.aioHistoryCohortWindowMs || 5000,
    );

    const rows = aio.prepare(`
      SELECT item_key, event, body, created_at
      FROM watch_deliveries
      WHERE
        sink_id = ?
        AND event = ?
        AND item_key NOT LIKE 'b|%'
        AND created_at BETWEEN ? AND ?
      ORDER BY created_at ASC
      LIMIT 1000
    `).all(
      String(sink.id),
      String(event.event),
      Number(eventMs) - windowMs,
      Number(eventMs) + windowMs,
    );

    const matching = rows.filter((row) => {
      const body = parseBody(row.body);
      return (
        body
        && ['movie', 'episode'].includes(String(body.scope || ''))
        && !Array.isArray(body.videos)
      );
    });

    const itemKeys = [...new Set(
      matching.map((row) => String(row.item_key || '')).filter(Boolean),
    )];

    if (
      itemKeys.length
      < Number(config.aioHistoryCohortMinItems || 3)
    ) {
      return null;
    }

    return {
      ignored: 'aio_history_sync_fanout',
      guardVariant: 'single_mark_cohort',
      event: String(event.event),
      cohortItems: itemKeys.length,
      cohortWindowMs: windowMs,
      cohortFirstAt: Math.min(
        ...matching.map((row) => Number(row.created_at)),
      ),
      cohortLastAt: Math.max(
        ...matching.map((row) => Number(row.created_at)),
      ),
      sinkInstanceId: String(sink.addon_instance_id || ''),
      writesTrakt: false,
    };
  } catch {
    return null;
  } finally {
    aio?.close();
  }
}

export function detectAioHistoryEcho(
  config,
  db,
  profileId,
  event,
) {
  if (!config?.aioHistoryEchoGuard) return null;
  if (!['played', 'unplayed'].includes(String(event?.event || ''))) {
    return null;
  }
  if (!['movie', 'episode'].includes(String(event?.scope || ''))) {
    return null;
  }
  if (Array.isArray(event?.videos)) return null;

  const cohort = cohortForEvent(config, event);
  if (cohort) return cohort;

  const canonical = canonicalHistoryState(
    db,
    profileId,
    event,
    {
      maxAgeSeconds:
        Number(config.canonicalHistoryMaxAgeSeconds || 900),
    },
  );

  if (
    canonical.known
    && (
      (event.event === 'played' && canonical.watched)
      || (event.event === 'unplayed' && !canonical.watched)
    )
  ) {
    return {
      ignored: 'canonical_history_same_state',
      guardVariant:
        event.event === 'played'
          ? 'canonical_same_state_played'
          : 'canonical_same_state_unplayed',
      itemKey: canonical.itemKey,
      canonicalWatched: canonical.watched,
      snapshotAgeSeconds: Number(
        canonical.ageSeconds.toFixed(3),
      ),
      snapshotVersion: canonical.version,
      writesTrakt: false,
    };
  }

  if (event.event === 'unplayed') {
    const evidence = loadAioHistoryEvidence(db, event.id);
    const eventTime = evidenceUnplayedEcho(event, evidence);
    if (eventTime) return eventTime;

    /*
     * RC3 remains a conservative fallback when the fast event-time journal did
     * not capture the row before AIO mutated it. The fallback still requires
     * its older stronger proof and therefore cannot broaden suppression.
     */
    const rc3 = detectAioFalseUnplayedEcho(config, event);
    if (rc3) {
      return {
        ...rc3,
        guardVariant: `rc3_fallback:${rc3.guardVariant || 'unknown'}`,
      };
    }
  }

  return null;
}
