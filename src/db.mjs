import fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { encryptSecret, decryptSecret, sha256 } from './crypto.mjs';

export class BridgeDb {
  constructor(config) {
    fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
    this.config = config;
    this.db = new DatabaseSync(config.dbPath);
    this.db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=NORMAL;
      PRAGMA foreign_keys=ON;
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
      CREATE INDEX IF NOT EXISTS idx_event_log_created_at ON event_log(created_at DESC);
    `);
  }

  now() { return Math.floor(Date.now() / 1000); }

  createProfile(id, name) {
    const now = this.now();
    this.db.prepare(`INSERT INTO profiles (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)`)
      .run(id, name || 'Trakt', now, now);
    return this.getProfile(id);
  }

  getProfile(id) {
    return this.db.prepare(`SELECT * FROM profiles WHERE id = ?`).get(id) || null;
  }

  listProfiles() {
    return this.db.prepare(`SELECT * FROM profiles ORDER BY created_at ASC`).all();
  }

  setTokens(profileId, tokenResponse) {
    const now = this.now();
    const createdAt = Number(tokenResponse.created_at || now);
    const expiresIn = Number(tokenResponse.expires_in || 7 * 24 * 3600);
    const expiresAt = createdAt + expiresIn;
    this.db.prepare(`
      UPDATE profiles
      SET access_token_enc = ?, refresh_token_enc = ?, token_expires_at = ?, connected_at = COALESCE(connected_at, ?), updated_at = ?
      WHERE id = ?
    `).run(
      encryptSecret(tokenResponse.access_token, this.config.bridgeSecret),
      encryptSecret(tokenResponse.refresh_token, this.config.bridgeSecret),
      expiresAt,
      now,
      now,
      profileId,
    );
  }

  clearTokens(profileId) {
    const now = this.now();
    this.db.prepare(`UPDATE profiles SET access_token_enc=NULL, refresh_token_enc=NULL, token_expires_at=NULL, connected_at=NULL, updated_at=? WHERE id=?`)
      .run(now, profileId);
  }

  getTokens(profileId) {
    const profile = this.getProfile(profileId);
    if (!profile || !profile.access_token_enc) return null;
    return {
      accessToken: decryptSecret(profile.access_token_enc, this.config.bridgeSecret),
      refreshToken: decryptSecret(profile.refresh_token_enc, this.config.bridgeSecret),
      expiresAt: Number(profile.token_expires_at || 0),
    };
  }

  putOauthState(state, profileId, ttlSeconds = 600) {
    const now = this.now();
    this.pruneOauthStates();
    this.db.prepare(`INSERT OR REPLACE INTO oauth_states (state_hash, profile_id, expires_at, created_at) VALUES (?, ?, ?, ?)`)
      .run(sha256(state), profileId, now + ttlSeconds, now);
  }

  consumeOauthState(state) {
    const hash = sha256(state);
    const row = this.db.prepare(`SELECT * FROM oauth_states WHERE state_hash = ?`).get(hash);
    this.db.prepare(`DELETE FROM oauth_states WHERE state_hash = ?`).run(hash);
    if (!row || Number(row.expires_at) < this.now()) return null;
    return row.profile_id;
  }

  pruneOauthStates() {
    this.db.prepare(`DELETE FROM oauth_states WHERE expires_at < ?`).run(this.now());
  }

  isProcessed(profileId, eventId) {
    return Boolean(this.db.prepare(`SELECT 1 FROM processed_events WHERE profile_id=? AND event_id=?`).get(profileId, eventId));
  }

  markProcessed(profileId, eventId, summary = null) {
    this.db.prepare(`INSERT OR IGNORE INTO processed_events (profile_id, event_id, processed_at, summary) VALUES (?, ?, ?, ?)`)
      .run(profileId, eventId, this.now(), summary);
  }

  cacheGet(key) {
    const row = this.db.prepare(`SELECT payload_json, expires_at FROM media_cache WHERE cache_key=?`).get(key);
    if (!row) return null;
    if (Number(row.expires_at) < this.now()) {
      this.db.prepare(`DELETE FROM media_cache WHERE cache_key=?`).run(key);
      return null;
    }
    try { return JSON.parse(row.payload_json); }
    catch {
      this.db.prepare(`DELETE FROM media_cache WHERE cache_key=?`).run(key);
      return null;
    }
  }

  cacheSet(key, payload, ttlSeconds = 7 * 24 * 3600) {
    const now = this.now();
    this.db.prepare(`
      INSERT INTO media_cache (cache_key, payload_json, expires_at, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, expires_at=excluded.expires_at, updated_at=excluded.updated_at
    `).run(key, JSON.stringify(payload), now + ttlSeconds, now);
  }

  cacheDelete(key) {
    this.db.prepare(`DELETE FROM media_cache WHERE cache_key=?`).run(key);
  }

  logEvent({ profileId = null, eventId = null, event = null, status, detail = null }) {
    this.db.prepare(`INSERT INTO event_log (profile_id, event_id, event, status, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)`)
      .run(profileId, eventId, event, status, detail ? String(detail).slice(0, 1000) : null, this.now());
    this.db.exec(`DELETE FROM event_log WHERE id NOT IN (SELECT id FROM event_log ORDER BY id DESC LIMIT 1000)`);
  }

  recentEvents(profileId, limit = 20) {
    return this.db.prepare(`SELECT event_id, event, status, detail, created_at FROM event_log WHERE profile_id=? ORDER BY id DESC LIMIT ?`)
      .all(profileId, limit);
  }
}
