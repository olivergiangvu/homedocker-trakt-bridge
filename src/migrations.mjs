export const SCHEMA_VERSION = 1;

const MIGRATIONS = [
  {
    version: 1,
    name: 'baseline-v0.4.0',
    up(db) {
      db.exec(`
        CREATE TABLE IF NOT EXISTS profiles (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          access_token_enc TEXT,
          refresh_token_enc TEXT,
          token_expires_at INTEGER,
          connected_at INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS oauth_states (
          state_hash TEXT PRIMARY KEY,
          profile_id TEXT NOT NULL,
          expires_at INTEGER NOT NULL,
          created_at INTEGER NOT NULL,
          FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS processed_events (
          profile_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          processed_at INTEGER NOT NULL,
          summary TEXT,
          PRIMARY KEY(profile_id, event_id),
          FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS media_cache (
          cache_key TEXT PRIMARY KEY,
          payload_json TEXT NOT NULL,
          expires_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          profile_id TEXT,
          event_id TEXT,
          event TEXT,
          status TEXT NOT NULL,
          detail TEXT,
          created_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_event_log_created_at
          ON event_log(created_at DESC);
      `);
    },
  },
];

export function currentSchemaVersion(db) {
  const row = db.prepare('PRAGMA user_version').get();
  return Number(row?.user_version || 0);
}

export function migrateDatabase(db) {
  const from = currentSchemaVersion(db);
  if (from > SCHEMA_VERSION) {
    throw new Error(`Database schema ${from} is newer than supported schema ${SCHEMA_VERSION}`);
  }

  let current = from;
  const applied = [];

  for (const migration of MIGRATIONS) {
    if (migration.version <= current) continue;

    db.exec('BEGIN IMMEDIATE');
    try {
      migration.up(db);
      db.exec(`PRAGMA user_version=${Number(migration.version)}`);
      db.exec('COMMIT');
      current = migration.version;
      applied.push({ version: migration.version, name: migration.name });
    } catch (err) {
      try { db.exec('ROLLBACK'); } catch {}
      throw err;
    }
  }

  return {
    from,
    to: currentSchemaVersion(db),
    applied,
  };
}
