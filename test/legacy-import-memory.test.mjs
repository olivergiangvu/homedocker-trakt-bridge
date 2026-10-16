import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyRealLegacyImportInMemory } from '../scripts/verify-v140-legacy-import-memory.mjs';

const profileId = 'a'.repeat(24);
const record = (canonical, alias, timestamp = 1792000000) => ({
  traktImdb: canonical, preferredMetaId: alias, updatedAt: timestamp,
});

test('R20 actual production importer proves 15 real-shaped shows and one alias in RAM', () => {
  const rows = Object.fromEntries(Array.from({ length: 14 }, (_, i) => [
    String(i + 1), record(
      'tt' + String(i + 10000000), 'tt' + String(i + 10000000),
    ),
  ]));
  rows['263102'] = record('tt44051354', 'tt44094505');
  const legacy = { revision: 1, shows: rows };
  const before = structuredClone(legacy);
  const r = verifyRealLegacyImportInMemory({
    profileId, legacy, expected: { shows: 15, aliases: 1 },
  });
  assert.equal(r.status, 'OFFLINE_MEMORY_IMPORT_PASS');
  assert.equal(r.imported_show_mappings, 15);
  assert.equal(r.alias_groups, 1);
  assert.equal(r.verified_alias_count, 1);
  assert.equal(r.migration_persisted_to_production, false);
  assert.equal(r.sqlite_writes, 0);
  assert.equal(r.http_or_trakt_calls, 0);
  assert.equal(r.repeat_import_idempotent, true);
  assert.deepEqual(legacy, before);
  const wire = JSON.stringify(r);
  for (const sensitive of [profileId, 'tt44051354', 'tt44094505', '263102']) {
    assert.ok(!wire.includes(sensitive));
  }
});

test('R20 rejects mismatched offline expected counts without modifying legacy payload', () => {
  const legacy = { shows: {
    '263102': record('tt44051354', 'tt44094505'),
  }};
  assert.throws(() => verifyRealLegacyImportInMemory({
    profileId, legacy, expected: { shows: 15, aliases: 1 },
  }), /python_js_preview_disagreement/);
  assert.equal(legacy.shows['263102'].preferredMetaId, 'tt44094505');
});

test('R20 rejects conflict and never silently drops ambiguous mapping', () => {
  const legacy = { shows: {
    '263102': record('tt44051354', 'tt44094505'),
    '1': record('tt99999999', 'tt44094505'),
  }};
  assert.throws(() => verifyRealLegacyImportInMemory({
    profileId, legacy, expected: { shows: 2, aliases: 2 },
  }), /offline_js_preview_precondition_failed/);
});
