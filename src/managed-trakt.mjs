import { BridgeError } from './errors.mjs';
import { TraktClient, parseRateLimitHeader } from './trakt.mjs';

const WRITE_INTERVAL_MS = 1100;
const DEFAULT_RATE_COOLDOWN_MS = 60_000;
const RATE_LIMIT_CACHE_PREFIX = 'rate-limit:v3:';
const LEGACY_RATE_LIMIT_V2_CACHE_PREFIX = 'rate-limit:v2:';
const LEGACY_RATE_LIMIT_V1_CACHE_PREFIX = 'rate-limit:v1:';

function sleep(ms) {
  if (!(ms > 0)) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryAfterMs(value) {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 0) {
    // Retry-After is normally delta-seconds, but tolerate an absolute unix value.
    if (numeric > 1e12) return Math.max(0, numeric - Date.now());
    if (numeric > 1e9) return Math.max(0, numeric * 1000 - Date.now());
    return numeric * 1000;
  }
  const date = Date.parse(String(value));
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : null;
}

function retryAfterSeconds(until) {
  return String(Math.max(1, Math.ceil((until - Date.now()) / 1000)));
}

function isWriteMethod(method) {
  return ['POST', 'PUT', 'DELETE'].includes(String(method || 'GET').toUpperCase());
}

function writeLaneForPath(path = '') {
  const value = String(path || '');
  if (value.startsWith('/scrobble/')) return 'scrobble';
  if (value === '/sync/history' || value === '/sync/history/remove') return 'history';
  if (value === '/sync/watchlist' || value === '/sync/watchlist/remove') return 'watchlist';
  return 'write';
}

function laneForRateLimit(rateLimit, method = 'GET', path = '') {
  const name = String(rateLimit?.name || '').toUpperCase();
  if (name === 'AUTHED_API_POST_LIMIT') return 'write';
  if (name === 'AUTHED_API_GET_LIMIT') return 'read';

  // Headerless/unnamed 429s are ambiguous. Keep them local to the request
  // family that actually failed. A history/remove security limit must not
  // poison the latency-sensitive scrobble lane, and vice versa. If Trakt
  // explicitly identifies its shared authenticated POST bucket, the rule
  // above still cools every write lane.
  if (!name) {
    if (!isWriteMethod(method)) return 'read';
    return writeLaneForPath(path);
  }

  return 'shared';
}

function emptyCooldowns() {
  return {
    readUntil: 0,
    allWriteUntil: 0,
    scrobbleUntil: 0,
    historyUntil: 0,
    watchlistUntil: 0,
    sharedUntil: 0,
  };
}

function cooldownKeyForLane(lane) {
  switch (lane) {
    case 'read': return 'readUntil';
    case 'write': return 'allWriteUntil';
    case 'scrobble': return 'scrobbleUntil';
    case 'history': return 'historyUntil';
    case 'watchlist': return 'watchlistUntil';
    default: return 'sharedUntil';
  }
}

function localCooldownName(lane) {
  switch (lane) {
    case 'read': return 'LOCAL_READ_COOLDOWN';
    case 'scrobble': return 'LOCAL_SCROBBLE_COOLDOWN';
    case 'history': return 'LOCAL_HISTORY_COOLDOWN';
    case 'watchlist': return 'LOCAL_WATCHLIST_COOLDOWN';
    case 'write': return 'LOCAL_WRITE_COOLDOWN';
    default: return 'LOCAL_SHARED_COOLDOWN';
  }
}

export class ManagedTraktClient extends TraktClient {
  constructor(config, db) {
    super(config, db);
    this.db = db;
    this.rateCooldowns = new Map();
    this.writeNotBefore = new Map();
    this.writeQueues = new Map();
    this.rateLimits = new Map();
  }

  #cooldownKey(profileId) {
    return `${RATE_LIMIT_CACHE_PREFIX}${profileId}`;
  }

  #legacyV2CooldownKey(profileId) {
    return `${LEGACY_RATE_LIMIT_V2_CACHE_PREFIX}${profileId}`;
  }

  #legacyV1CooldownKey(profileId) {
    return `${LEGACY_RATE_LIMIT_V1_CACHE_PREFIX}${profileId}`;
  }

  #loadCooldowns(profileId) {
    const memory = this.rateCooldowns.get(profileId);
    if (memory) return memory;

    const persisted = this.db.cacheGet?.(this.#cooldownKey(profileId));
    const legacyV2 = persisted
      ? null
      : this.db.cacheGet?.(this.#legacyV2CooldownKey(profileId));
    const legacyV1 = persisted || legacyV2
      ? null
      : this.db.cacheGet?.(this.#legacyV1CooldownKey(profileId));

    const state = {
      ...emptyCooldowns(),
      readUntil: Number(persisted?.readUntil ?? legacyV2?.readUntil ?? 0),
      // A v2 write cooldown did not record which write family caused it. Keep
      // that one legacy window conservative, then all new 429s use v3 lanes.
      allWriteUntil: Number(
        persisted?.allWriteUntil ?? legacyV2?.writeUntil ?? 0
      ),
      scrobbleUntil: Number(persisted?.scrobbleUntil || 0),
      historyUntil: Number(persisted?.historyUntil || 0),
      watchlistUntil: Number(persisted?.watchlistUntil || 0),
      sharedUntil: Math.max(
        Number(persisted?.sharedUntil || 0),
        Number(legacyV2?.sharedUntil || 0),
        Number(legacyV1?.until || 0),
      ),
    };
    this.rateCooldowns.set(profileId, state);
    return state;
  }

  #saveCooldowns(profileId, state) {
    this.rateCooldowns.set(profileId, state);
    const until = Math.max(
      state.readUntil,
      state.allWriteUntil,
      state.scrobbleUntil,
      state.historyUntil,
      state.watchlistUntil,
      state.sharedUntil,
    );
    const ttlSeconds = Math.max(1, Math.ceil((until - Date.now()) / 1000) + 5);
    this.db.cacheSet?.(this.#cooldownKey(profileId), state, ttlSeconds);
  }

  #activeCooldown(profileId, lane) {
    const state = this.#loadCooldowns(profileId);
    const now = Date.now();

    for (const key of [
      'readUntil',
      'allWriteUntil',
      'scrobbleUntil',
      'historyUntil',
      'watchlistUntil',
      'sharedUntil',
    ]) {
      if (state[key] <= now) state[key] = 0;
    }

    const candidates = [{ lane: 'shared', until: state.sharedUntil }];

    if (lane === 'read') {
      candidates.push({ lane: 'read', until: state.readUntil });
    } else {
      candidates.push({ lane: 'write', until: state.allWriteUntil });
      if (lane !== 'write') {
        candidates.push({
          lane,
          until: Number(state[cooldownKeyForLane(lane)] || 0),
        });
      }
    }

    return candidates.reduce(
      (active, candidate) => candidate.until > active.until ? candidate : active,
      { lane: 'shared', until: 0 },
    );
  }

  #armRateCooldown(profileId, retryAfter, lane = 'shared') {
    const wait = retryAfterMs(retryAfter) ?? DEFAULT_RATE_COOLDOWN_MS;
    const proposed = Date.now() + Math.max(1000, wait);
    const state = { ...this.#loadCooldowns(profileId) };
    const key = cooldownKeyForLane(lane);
    state[key] = Math.max(Number(state[key] || 0), proposed);
    this.#saveCooldowns(profileId, state);
    return state[key];
  }

  #throwIfCooling(profileId, path, lane) {
    const active = this.#activeCooldown(profileId, lane);
    if (!active.until) return;
    throw new BridgeError('Trakt rate limit cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(active.until),
      code: 'trakt_rate_cooldown',
      upstreamPath: path,
      rateLimit: { name: localCooldownName(active.lane) },
    });
  }

  rateLimitSnapshot(profileId) {
    const state = this.#loadCooldowns(profileId);
    const writeNotBefore = Number(this.writeNotBefore.get(profileId) || 0);
    const now = Date.now();
    const seconds = (until) => Math.max(
      0,
      Math.ceil((Number(until || 0) - now) / 1000)
    );
    const sharedSeconds = seconds(state.sharedUntil);
    const allWriteSeconds = Math.max(
      sharedSeconds,
      seconds(state.allWriteUntil),
    );
    const readSeconds = Math.max(sharedSeconds, seconds(state.readUntil));
    const scrobbleSeconds = Math.max(
      allWriteSeconds,
      seconds(state.scrobbleUntil),
    );
    const historySeconds = Math.max(
      allWriteSeconds,
      seconds(state.historyUntil),
    );
    const watchlistSeconds = Math.max(
      allWriteSeconds,
      seconds(state.watchlistUntil),
    );
    const writeSeconds = Math.max(
      scrobbleSeconds,
      historySeconds,
      watchlistSeconds,
      seconds(writeNotBefore),
    );
    return {
      observed: this.rateLimits.get(profileId) || null,
      sharedCooldownSeconds: sharedSeconds,
      readCooldownSeconds: readSeconds,
      writeCooldownSeconds: writeSeconds,
      allWriteCooldownSeconds: allWriteSeconds,
      scrobbleCooldownSeconds: scrobbleSeconds,
      historyCooldownSeconds: historySeconds,
      watchlistCooldownSeconds: watchlistSeconds,
    };
  }

  #observeRateLimit(profileId, headers, method = 'GET', path = '') {
    const observed = parseRateLimitHeader(headers);
    if (!observed) return;
    this.rateLimits.set(profileId, observed);
    if (observed.remaining === 0 && observed.until) {
      this.#armRateCooldown(
        profileId,
        observed.until,
        laneForRateLimit(observed, method, path),
      );
    }
  }

  async #pacedWrite(profileId, path, task) {
    const lane = writeLaneForPath(path);
    const previous = this.writeQueues.get(profileId) || Promise.resolve();
    const run = previous.catch(() => undefined).then(async () => {
      // A long upstream Retry-After must be returned to AIOStreams immediately;
      // holding its HTTP request open would exceed its delivery timeout.
      this.#throwIfCooling(profileId, path, lane);

      const notBefore = Number(this.writeNotBefore.get(profileId) || 0);
      await sleep(Math.max(0, notBefore - Date.now()));

      const started = Date.now();
      this.writeNotBefore.set(profileId, started + WRITE_INTERVAL_MS);

      try {
        return await task();
      } catch (err) {
        if (err?.status === 429) {
          this.#armRateCooldown(
            profileId,
            err.retryAfter,
            laneForRateLimit(err.rateLimit, 'POST', path),
          );
        }
        throw err;
      }
    });

    this.writeQueues.set(profileId, run.catch(() => undefined));
    return run;
  }

  async requestDetailed(profileId, path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();

    if (method === 'GET') {
      this.#throwIfCooling(profileId, path, 'read');
      try {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers, method, path);
        return result;
      } catch (err) {
        if (err?.status === 429) {
          this.#armRateCooldown(
            profileId,
            err.retryAfter,
            laneForRateLimit(err.rateLimit, method, path),
          );
        }
        throw err;
      }
    }

    if (isWriteMethod(method)) {
      return this.#pacedWrite(profileId, path, async () => {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers, method, path);
        return result;
      });
    }

    return super.requestDetailed(profileId, path, options);
  }

  async pullState(profileId, since = null) {
    this.#throwIfCooling(profileId, '/sync/*', 'read');
    return super.pullState(profileId, since);
  }

  async refresh(profileId, force = false) {
    try {
      return await super.refresh(profileId, force);
    } catch (err) {
      if (err?.code === 'reconnect_required') {
        this.db.clearTokens(profileId, 'reconnect_required');
      }
      throw err;
    }
  }
}
