import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { BridgeDb } from '../src/db.mjs';

function makeDb(dir) {
  return new BridgeDb({
    dataDir: dir,
    dbPath: path.join(dir, 'bridge.db'),
    bridgeSecret: '00'.repeat(32),
  });
}

test('pull cache survives a database reopen and can be explicitly cleared', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trakt-bridge-cache-'));
  const key = 'pull-state:profile1';
  const entry = {
    version: 'abc123',
    items: [{ type: 'movie', metaId: 'tt0111161', videoId: 'tt0111161', progressPercent: 25 }],
    fetchedAt: 1_790_000_000_000,
  };

  const db1 = makeDb(dir);
  db1.cacheSet(key, entry, 3600);
  db1.db.close();

  const db2 = makeDb(dir);
  assert.deepEqual(db2.cacheGet(key), entry);
  db2.cacheDelete(key);
  assert.equal(db2.cacheGet(key), null);
  db2.db.close();

  fs.rmSync(dir, { recursive: true, force: true });
});
