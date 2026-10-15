/**
 * R20: execute actual v1.4 identity importer against isolated in-memory fake DB.
 *
 * Reads JSON payload from stdin ONLY. Does not open SQLite, call AIO or Trakt,
 * write files, import network modules, log raw profile/IMDb/cache information,
 * or use production OAuth encryption keys. The Python launcher supplies only
 * the legacy cache show mapping and matching profile ID through a local pipe.
 *
 * CLI is not a production migration tool; a successful result is NOT approval
 * to activate a Gateway or modify production Bridge SQLite.
 */
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';
import {
  loadVerifiedIdentityGraph, verifiedCanonicalForImdb,
} from '../src/identity-continuity.mjs';

const MAX_STDIN = 2 * 1024 * 1024;

async function readBoundedStdin() {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk.toString('utf8');
    if (Buffer.byteLength(raw) > MAX_STDIN) {
      throw new Error('too_much_identity_evidence');
    }
  }
  return JSON.parse(raw);
}

export function verifyRealLegacyImportInMemory({ profileId, legacy, expected }) {
  const cache = new Map();
  const oldKey = 'identity-alias:v1:' + profileId;
  const graphKey = 'identity-graph:v1:' + profileId;
  const oldRaw = JSON.stringify(legacy);
  cache.set(oldKey, structuredClone(legacy));
  let insertCalls = 0;
  const fakeDb = {
    getProfile(id) {
      return id === profileId
        ? { id, access_token_enc: 'OFFLINE_FLAG_NO_REAL_SECRET' } : null;
    },
    cachePeek(key) { return structuredClone(cache.get(key) ?? null); },
    cacheGet(key) { return structuredClone(cache.get(key) ?? null); },
    cacheInsertIfAbsent(key, body) {
      if (key !== graphKey || cache.has(key)) return false;
      insertCalls++;
      cache.set(key, structuredClone(body));
      return true;
    },
    cacheSet() { throw new Error('persistent_write_not_permitted'); },
    cacheDelete() { throw new Error('persistent_delete_not_permitted'); },
  };
  const preview = importLegacyIdentityAliases(fakeDb, profileId);
  if (preview.status !== 'DRY_RUN_READY' || preview.changed
      || insertCalls !== 0 || cache.size !== 1) {
    throw new Error('offline_js_preview_precondition_failed');
  }
  if (preview.showCount !== expected?.shows
      || preview.aliasedShows !== expected?.aliases) {
    throw new Error('python_js_preview_disagreement');
  }

  const result = importLegacyIdentityAliases(fakeDb, profileId, { dryRun: false });
  if (result.status !== 'LEGACY_IMPORTED' || !result.changed
      || insertCalls !== 1 || cache.size !== 2) {
    throw new Error('offline_js_import_failed');
  }
  const graph = loadVerifiedIdentityGraph(fakeDb, profileId);
  const rows = Object.values(graph.shows);
  const aliases = rows.reduce((n, row) => n + row.aliases.length, 0);
  const verified = Object.entries(graph.shows).every(([showId, row]) =>
    [row.canonicalImdb, ...row.aliases].every(id =>
      verifiedCanonicalForImdb(graph, id)?.traktShowId === Number(showId)));
  const after = importLegacyIdentityAliases(fakeDb, profileId, { dryRun: false });
  if (after.status !== 'GRAPH_ALREADY_EXISTS'
      || insertCalls !== 1 || !verified
      || graph.conflicts.length > 0
      || graph.revision !== 1
      || rows.length !== expected.shows
      || aliases !== expected.aliases
      || JSON.stringify(cache.get(oldKey)) !== oldRaw) {
    throw new Error('offline_js_result_integrity_failed');
  }

  return {
    audit: 'R20_REAL_LEGACY_IMPORT_ISOLATED_MEMORY',
    status: 'OFFLINE_MEMORY_IMPORT_PASS',
    actual_v140_javascript_importer_executed: true,
    connected_profile_count: 1,
    imported_show_mappings: rows.length,
    alias_groups: rows.filter(row => row.aliases.length > 0).length,
    verified_alias_count: aliases,
    conflict_count: graph.conflicts.length,
    identity_graph_revision: graph.revision,
    repeat_import_idempotent: true,
    legacy_source_unchanged_in_memory: true,
    migration_persisted_to_production: false,
    sqlite_writes: 0,
    aio_watch_state_writes: 0,
    http_or_trakt_calls: 0,
    tokens_ids_or_history_printed: false,
    gateway_activated_or_client_routed: false,
  };
}

if (process.argv[1]?.endsWith('verify-v140-legacy-import-memory.mjs')) {
  try {
    const input = await readBoundedStdin();
    console.log(JSON.stringify(verifyRealLegacyImportInMemory(input)));
  } catch {
    console.error('STOP: R20 memory-only verifier could not establish integrity');
    process.exitCode = 1;
  }
}
