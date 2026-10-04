import { BridgeError } from './errors.mjs';
import { TraktClient, parseRateLimitHeader } from './trakt.mjs';

const WRITE_INTERVAL_MS = 1100;
const DEFAULT_RATE_COOLDOWN_MS = 60_000;
const RATE_LIMIT_CACHE_PREFIX = 'rate-limit:v2:';
const LEGACY_RATE_LIMIT_CACHE_PREFIX = 'rate-limit:v1:';

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

function laneForRateLimit(rateLimit) {
  const name = String(rateLimit?.name || '').toUpperCase();
  if (name === 'AUTHED_API_POST_LIMIT') return 'write';
  if (name === 'AUTHED_API_GET_LIMIT') return 'read';
  return 'shared';
}

function emptyCooldowns() {
  return { readUntil: 0, writeUntil: 0, sharedUntil: 0 };
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

  #legacyCooldownKey(profileId) {
    return `${LEGACY_RATE_LIMIT_CACHE_PREFIX}${profileId}`;
  }

  #loadCooldowns(profileId) {
    const memory = this.rateCooldowns.get(profileId);
    if (memory) return memory;

    const persisted = this.db.cacheGet?.(this.#cooldownKey(profileId));
    const legacy = persisted ? null : this.db.cacheGet?.(this.#legacyCooldownKey(profileId));
    const state = {
      readUntil: Number(persisted?.readUntil || 0),
      writeUntil: Number(persisted?.writeUntil || 0),
      sharedUntil: Math.max(
        Number(persisted?.sharedUntil || 0),
        Number(legacy?.until || 0),
      ),
    };
    this.rateCooldowns.set(profileId, state);
    return state;
  }

  #saveCooldowns(profileId, state) {
    this.rateCooldowns.set(profileId, state);
    const until = Math.max(state.readUntil, state.writeUntil, state.sharedUntil);
    const ttlSeconds = Math.max(1, Math.ceil((until - Date.now()) / 1000) + 5);
    this.db.cacheSet?.(this.#cooldownKey(profileId), state, ttlSeconds);
  }

  #cooldownUntil(profileId, lane) {
    const state = this.#loadCooldowns(profileId);
    const now = Date.now();

    for (const key of ['readUntil', 'writeUntil', 'sharedUntil']) {
      if (state[key] <= now) state[key] = 0;
    }

    const laneUntil = lane === 'read' ? state.readUntil : state.writeUntil;
    return Math.max(state.sharedUntil, laneUntil);
  }

  #armRateCooldown(profileId, retryAfter, lane = 'shared') {
    const wait = retryAfterMs(retryAfter) ?? DEFAULT_RATE_COOLDOWN_MS;
    const proposed = Date.now() + Math.max(1000, wait);
    const state = { ...this.#loadCooldowns(profileId) };
    const key = lane === 'read'
      ? 'readUntil'
      : lane === 'write'
        ? 'writeUntil'
        : 'sharedUntil';
    state[key] = Math.max(Number(state[key] || 0), proposed);
    this.#saveCooldowns(profileId, state);
    return state[key];
  }

  #throwIfCooling(profileId, path, lane) {
    const until = this.#cooldownUntil(profileId, lane);
    if (!until) return;
    throw new BridgeError('Trakt rate limit cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(until),
      code: 'trakt_rate_cooldown',
      upstreamPath: path,
      rateLimit: { name: lane === 'read' ? 'LOCAL_READ_COOLDOWN' : 'LOCAL_WRITE_COOLDOWN' },
    });
  }

  rateLimitSnapshot(profileId) {
    const state = this.#loadCooldowns(profileId);
    const writeNotBefore = Number(this.writeNotBefore.get(profileId) || 0);
    const now = Date.now();
    const seconds = (until) => Math.max(0, Math.ceil((Number(until || 0) - now) / 1000));
    const sharedSeconds = seconds(state.sharedUntil);
    const readSeconds = Math.max(sharedSeconds, seconds(state.readUntil));
    const writeSeconds = Math.max(
      sharedSeconds,
      seconds(state.writeUntil),
      seconds(writeNotBefore),
    );
    return {
      observed: this.rateLimits.get(profileId) || null,
      sharedCooldownSeconds: sharedSeconds,
      readCooldownSeconds: readSeconds,
      writeCooldownSeconds: writeSeconds,
    };
  }

  #observeRateLimit(profileId, headers) {
    const observed = parseRateLimitHeader(headers);
    if (!observed) return;
    this.rateLimits.set(profileId, observed);
    if (observed.remaining === 0 && observed.until) {
      this.#armRateCooldown(profileId, observed.until, laneForRateLimit(observed));
    }
  }

  async #pacedWrite(profileId, path, task) {
    const previous = this.writeQueues.get(profileId) || Promise.resolve();
    const run = previous.catch(() => undefined).then(async () => {
      // A long upstream Retry-After must be returned to AIOStreams immediately;
      // holding its HTTP request open would exceed its delivery timeout.
      this.#throwIfCooling(profileId, path, 'write');

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
            laneForRateLimit(err.rateLimit),
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
        this.#observeRateLimit(profileId, result.headers);
        return result;
      } catch (err) {
        if (err?.status === 429) {
          this.#armRateCooldown(
            profileId,
            err.retryAfter,
            laneForRateLimit(err.rateLimit),
          );
        }
        throw err;
      }
    }

    if (isWriteMethod(method)) {
      return this.#pacedWrite(profileId, path, async () => {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers);
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
