import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';

import {
  openAioReadOnlyDatabase,
} from './aio-reconciler.mjs';

const EVIDENCE_PREFIX = 'aio-history-evidence:v1:';
const EVIDENCE_TTL_SECONDS = 24 * 3600;
const STATE_SKEW_MS = 2_000;

function evidenceKey(eventId) {
  const digest = createHash('sha256')
    .update(String(eventId || ''))
    .digest('hex')
    .slice(0, 32);
  return `${EVIDENCE_PREFIX}${digest}`;
}

function parseBody(value) {
  try {
    return JSON.parse(String(value || 'null'));
  } catch {
    return null;
  }
}

function exactSink(aio, {
  sinkName,
  sinkInstanceId,
}) {
  const params = [String(sinkName)];
  let sql = `
    SELECT id, uuid, persona, addon_instance_id, addon_name
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

function stateForDelivery(aio, sink, delivery) {
  const row = aio.prepare(`
    SELECT
      item_key, position_ms, duration_ms, played, origin,
      updated_at, last_played_at
    FROM watch_state
    WHERE uuid = ? AND persona = ? AND item_key = ?
    LIMIT 1
  `).get(
    String(sink.uuid),
    String(sink.persona || ''),
    String(delivery.item_key),
  );

  if (!row) return null;

  const createdAt = Number(delivery.created_at);
  const updatedAt = Number(row.updated_at);
  if (
    !Number.isFinite(createdAt)
    || !Number.isFinite(updatedAt)
    || updatedAt < createdAt
    || updatedAt - createdAt > STATE_SKEW_MS
  ) {
    return null;
  }

  return {
    itemKey: String(row.item_key),
    positionMs: Number(row.position_ms),
    durationMs: Number(row.duration_ms),
    played: Number(row.played),
    origin: String(row.origin || ''),
    updatedAt,
    lastPlayedAt:
      row.last_played_at == null
        ? null
        : Number(row.last_played_at),
  };
}

export function loadAioHistoryEvidence(db, eventId) {
  if (!eventId) return null;
  return db.cacheGet(evidenceKey(eventId));
}

export function captureAioHistoryEvidenceOnce({
  config,
  db,
  nowMs = Date.now(),
}) {
  if (!config?.aioHistoryEchoGuard) {
    return {
      status: 'disabled',
      observed: 0,
      captured: 0,
    };
  }

  if (!config?.aioDbPath || !existsSync(config.aioDbPath)) {
    return {
      status: 'unavailable',
      reason: 'aio_db_missing',
      observed: 0,
      captured: 0,
    };
  }

  const aio = openAioReadOnlyDatabase(config.aioDbPath);

  try {
    const sink = exactSink(aio, {
      sinkName: config.aioReconcileSinkName,
      sinkInstanceId: config.aioReconcileSinkInstanceId,
    });

    if (!sink) {
      return {
        status: 'blocked',
        reason: 'sink_not_exact',
        observed: 0,
        captured: 0,
      };
    }

    const lookbackMs = Number(
      config.aioHistoryEvidenceLookbackSeconds || 120,
    ) * 1000;

    const rows = aio.prepare(`
      SELECT id, sink_id, item_key, event, status, body, created_at
      FROM watch_deliveries
      WHERE
        sink_id = ?
        AND event IN ('played', 'unplayed')
        AND item_key NOT LIKE 'b|%'
        AND created_at BETWEEN ? AND ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(
      String(sink.id),
      Number(nowMs) - lookbackMs,
      Number(nowMs) + 1000,
      Number(config.aioHistoryEvidenceMaxRows || 500),
    );

    let captured = 0;

    for (const delivery of rows) {
      const body = parseBody(delivery.body);
      const eventId = String(body?.id || '').trim();
      if (!eventId) continue;

      const current = loadAioHistoryEvidence(db, eventId);
      const state = stateForDelivery(aio, sink, delivery);
      if (
        current?.state
        && (
          !state
          || Number(current.state.updatedAt || 0)
            >= Number(state.updatedAt || 0)
        )
      ) {
        continue;
      }
      const evidence = {
        eventId,
        event: String(delivery.event),
        itemKey: String(delivery.item_key),
        deliveryCreatedAt: Number(delivery.created_at),
        deliveryStatus: String(delivery.status || ''),
        sinkInstanceId: String(sink.addon_instance_id || ''),
        capturedAt: Number(nowMs),
        state,
      };

      db.cacheSet(
        evidenceKey(eventId),
        evidence,
        EVIDENCE_TTL_SECONDS,
      );

      if (state) captured += 1;
    }

    return {
      status: 'ok',
      observed: rows.length,
      captured,
    };
  } finally {
    aio.close();
  }
}

export function startAioHistoryEvidenceJournal({
  config,
  db,
}) {
  if (!config?.aioHistoryEchoGuard) {
    return { stop() {} };
  }

  let stopped = false;
  let running = false;
  let lastError = null;

  const run = () => {
    if (stopped || running) return;
    running = true;

    try {
      const result = captureAioHistoryEvidenceOnce({
        config,
        db,
      });

      if (
        result.status === 'ok'
        && result.captured
      ) {
        console.log(JSON.stringify({
          level: 'info',
          event: 'aio_history_evidence',
          observed: result.observed,
          captured: result.captured,
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
          event: 'aio_history_evidence_error',
          message,
          writesTrakt: false,
        }));
        lastError = message;
      }
    } finally {
      running = false;
    }
  };

  void run();

  const timer = setInterval(
    run,
    Number(config.aioHistoryEvidenceIntervalMs || 1000),
  );
  timer.unref();

  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
  };
}
