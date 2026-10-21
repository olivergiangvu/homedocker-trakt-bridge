/**
 * v1.5 beta: narrow, read-only witness for Trakt Bridge -> AIO import changes.
 *
 * This intentionally does NOT fetch Trakt, write AIO SQLite, send playback or
 * alter the v1.4 Gateway. A later, independently qualified notification route
 * can consume returned change counts; it must not infer client refresh from
 * the existence of a DB row.
 */
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';

function columns(db, table) {
  return new Set(db.prepare(`PRAGMA table_info(${table})`).all().map(r => r.name));
}

export function openImportWitnessDb(dbPath) {
  if (!dbPath || !existsSync(dbPath)) throw new Error('aio_db_missing');
  const db = new DatabaseSync(dbPath, { readOnly: true, timeout: 1000 });
  try {
    db.exec('PRAGMA query_only=ON');
    for (const [table, required] of [
      ['watch_sinks', ['id', 'addon_name', 'uuid', 'persona', 'retired_at']],
      ['watch_state', ['uuid', 'persona', 'sink_id', 'origin', 'updated_at', 'item_key']],
    ]) {
      const available = columns(db, table);
      if (required.some(name => !available.has(name))) throw new Error('aio_schema_incompatible');
    }
    return db;
  } catch (error) {
    db.close();
    throw error;
  }
}

/** Require exactly one active named sink and never cross a user/persona boundary. */
export function exactBridgeSink(db, { addonName = 'homedocker-trakt-bridge' } = {}) {
  const rows = db.prepare(`
    SELECT id, uuid, persona
    FROM watch_sinks
    WHERE addon_name=? AND retired_at IS NULL
  `).all(addonName);
  if (rows.length !== 1) throw new Error('expected_exactly_one_bridge_sink');
  return rows[0];
}

/** A bounded aggregate; never emit item keys, ids, titles or credentials. */
export function importedChangeCount(db, sink, afterMs, throughMs = Date.now()) {
  if (!sink || !Number.isSafeInteger(afterMs) || !Number.isSafeInteger(throughMs)
      || afterMs < 0 || throughMs < afterMs) throw new Error('invalid_witness_window');
  const row = db.prepare(`
    SELECT COUNT(*) AS changes, MAX(updated_at) AS latest_ms
    FROM watch_state
    WHERE uuid=? AND persona=? AND sink_id=? AND origin='import'
      AND updated_at>? AND updated_at<=?
  `).get(sink.uuid, sink.persona, sink.id, afterMs, throughMs);
  return { changes: Number(row.changes), latestMs: row.latest_ms == null ? null : Number(row.latest_ms) };
}

/**
 * Use a lookback checkpoint only after the first successful sample. This
 * witness is intentionally not a notification relay; zero updates is not
 * evidence of Trakt availability or of successful UI invalidation.
 */
export function createImportWitness({ dbPath, addonName = 'homedocker-trakt-bridge',
  now = Date.now, lookbackMs = 180_000 }) {
  if (!Number.isSafeInteger(lookbackMs) || lookbackMs < 30_000 || lookbackMs > 600_000)
    throw new Error('invalid_witness_lookback');
  const seen = new Map();
  let initialized = false;
  return {
    sample() {
      const db = openImportWitnessDb(dbPath);
      try {
        const sink = exactBridgeSink(db, { addonName });
        const current = now();
        if (!Number.isSafeInteger(current) || current < 0) throw new Error('invalid_clock');
        const cutoff = Math.max(0, current - lookbackMs);
        // AIO stores the fetch START time in updated_at; a slow response may
        // commit AFTER that timestamp. Overlap + per-key dedupe avoids missing it.
        const rows = db.prepare(`
          SELECT item_key, updated_at FROM watch_state
          WHERE uuid=? AND persona=? AND sink_id=? AND origin='import'
            AND updated_at>=? AND updated_at<=?
          ORDER BY updated_at DESC LIMIT 20001
        `).all(sink.uuid, sink.persona, sink.id, cutoff, current);
        if (rows.length > 20000) throw new Error('witness_window_too_large');
        for (const [key, stamp] of seen) if (stamp < cutoff) seen.delete(key);
        let changes = 0;
        let latestMs = null;
        for (const row of rows) {
          const at = Number(row.updated_at);
          const signature = `${row.item_key}\x1f${at}`;
          if (seen.has(signature)) continue;
          seen.set(signature, at);
          if (initialized) {
            changes++;
            latestMs = Math.max(latestMs ?? 0, at);
          }
        }
        if (!initialized) {
          initialized = true;
          return { status: 'BASELINE', changes: 0, latestMs: null };
        }
        return { status: changes ? 'IMPORT_OBSERVED' : 'UNCHANGED', changes, latestMs };
      } finally { db.close(); }
    },
  };
}
