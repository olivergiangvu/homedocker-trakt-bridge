import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { createImportWitness, exactBridgeSink, importedChangeCount, openImportWitnessDb } from '../src/watch-state-import-witness.mjs';

test('witness isolates one active Bridge sink and notices only imports after baseline', () => {
  const dir = mkdtempSync(join(tmpdir(), 'watch-witness-'));
  const path = join(dir, 'aio.sqlite');
  const db = new DatabaseSync(path);
  try {
    db.exec(`
      CREATE TABLE watch_sinks (id TEXT, addon_name TEXT, uuid TEXT, persona TEXT, retired_at INTEGER);
      CREATE TABLE watch_state (uuid TEXT, persona TEXT, sink_id TEXT, origin TEXT, updated_at INTEGER, item_key TEXT);
      INSERT INTO watch_sinks VALUES ('trakt','homedocker-trakt-bridge','u1','','NULL');
      UPDATE watch_sinks SET retired_at=NULL;
      INSERT INTO watch_sinks VALUES ('aiom','AIOMetadata SH','u1','',NULL);
      INSERT INTO watch_state VALUES ('u1','','trakt','import',1000,'old');
    `);
    let now = 2000;
    const watcher = createImportWitness({ dbPath: path, now: () => now });
    assert.deepEqual(watcher.sample(), { status: 'BASELINE', changes: 0, latestMs: null });
    db.exec(`
      INSERT INTO watch_state VALUES ('u1','','aiom','import',2100,'aiom');
      INSERT INTO watch_state VALUES ('u2','','trakt','import',2200,'other');
      INSERT INTO watch_state VALUES ('u1','','trakt','local',2300,'local');
      INSERT INTO watch_state VALUES ('u1','','trakt','import',2400,'new');
    `);
    now = 2500;
    assert.deepEqual(watcher.sample(), {status:'IMPORT_OBSERVED', changes:1, latestMs:2400});
    // Late SQL commit carrying an earlier fetch-start timestamp must NOT be lost.
    db.exec("INSERT INTO watch_state VALUES ('u1','','trakt','import',2450,'late')");
    now = 3000;
    assert.deepEqual(watcher.sample(), {status:'IMPORT_OBSERVED', changes:1, latestMs:2450});
    now = 3500;
    assert.deepEqual(watcher.sample(), {status:'UNCHANGED', changes:0, latestMs:null});
    const witnessDb = openImportWitnessDb(path);
    try {
      const sink = exactBridgeSink(witnessDb);
      assert.equal(importedChangeCount(witnessDb, sink, 2000, 2500).changes, 1);
      assert.throws(() => witnessDb.exec('DELETE FROM watch_state'), /readonly|read-only|SQLITE_READONLY/i);
    } finally { witnessDb.close(); }
  } finally { db.close(); rmSync(dir,{recursive:true,force:true}); }
});

test('fails closed when no unique active Bridge sink', () => {
  const dir=mkdtempSync(join(tmpdir(),'watch-witness-'));
  const path=join(dir,'aio.sqlite');
  const db=new DatabaseSync(path);
  try {
    db.exec(`
      CREATE TABLE watch_sinks (id TEXT,addon_name TEXT,uuid TEXT,persona TEXT,retired_at INTEGER);
      CREATE TABLE watch_state (uuid TEXT,persona TEXT,sink_id TEXT,origin TEXT,updated_at INTEGER,item_key TEXT);
      INSERT INTO watch_sinks VALUES ('a','homedocker-trakt-bridge','u1','',NULL);
      INSERT INTO watch_sinks VALUES ('b','homedocker-trakt-bridge','u2','',NULL);
    `);
    const ro=openImportWitnessDb(path);
    try { assert.throws(()=>exactBridgeSink(ro),/expected_exactly_one_bridge_sink/); }
    finally {ro.close();}
  } finally {db.close();rmSync(dir,{recursive:true,force:true});}
});
