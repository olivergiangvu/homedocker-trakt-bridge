import { BridgeError } from './errors.mjs';
import { TraktClient } from './trakt.mjs';

const WRITE_INTERVAL_MS = 1100;
const DEFAULT_RATE_COOLDOWN_MS = 60_000;
const RATE_LIMIT_CACHE_PREFIX = 'rate-limit:v1:';

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

function rateLimitHeader(headers) {
  const raw = headers?.get?.('x-ratelimit');
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return {
      name: parsed?.name || null,
      period: Number.isFinite(Number(parsed?.period)) ? Number(parsed.period) : null,
      limit: Number.isFinite(Number(parsed?.limit)) ? Number(parsed.limit) : null,
      remaining: Number.isFinite(Number(parsed?.remaining)) ? Number(parsed.remaining) : null,
      until: parsed?.until || null,
    };
  } catch {
    return null;
  }
}

export class ManagedTraktClient extends TraktClient {
  constructor(config, db) {
    super(config, db);
    this.db = db;
    this.rateCooldownUntil = new Map();
    this.writeNotBefore = new Map();
    this.writeQueues = new Map();
    this.rateLimits = new Map();
  }

  #cooldownKey(profileId) {
    return `${RATE_LIMIT_CACHE_PREFIX}${profileId}`;
  }

  #cooldownUntil(profileId) {
    const memory = Number(this.rateCooldownUntil.get(profileId) || 0);
    const persisted = this.db.cacheGet?.(this.#cooldownKey(profileId));
    const stored = Number(persisted?.until || 0);
    const until = Math.max(memory, stored);

    if (until > Date.now()) {
      if (until > memory) this.rateCooldownUntil.set(profileId, until);
      return until;
    }

    if (memory) this.rateCooldownUntil.delete(profileId);
    return 0;
  }

  #armRateCooldown(profileId, retryAfter) {
    const wait = retryAfterMs(retryAfter) ?? DEFAULT_RATE_COOLDOWN_MS;
    const proposed = Date.now() + Math.max(1000, wait);
    const until = Math.max(this.#cooldownUntil(profileId), proposed);
    this.rateCooldownUntil.set(profileId, until);

    const ttlSeconds = Math.max(1, Math.ceil((until - Date.now()) / 1000) + 5);
    this.db.cacheSet?.(this.#cooldownKey(profileId), { until }, ttlSeconds);
    return until;
  }

  #throwIfCooling(profileId, path) {
    const until = this.#cooldownUntil(profileId);
    if (!until) return;
    throw new BridgeError('Trakt rate limit cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(until),
      code: 'trakt_rate_cooldown',
      upstreamPath: path,
    });
  }

  rateLimitSnapshot(profileId) {
    const cooldownUntil = this.#cooldownUntil(profileId);
    const writeNotBefore = Number(this.writeNotBefore.get(profileId) || 0);
    const now = Date.now();
    const sharedSeconds = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));
    return {
      observed: this.rateLimits.get(profileId) || null,
      cooldownSeconds: sharedSeconds,
      readCooldownSeconds: sharedSeconds,
      writeCooldownSeconds: Math.max(
        sharedSeconds,
        Math.max(0, Math.ceil((writeNotBefore - now) / 1000)),
      ),
    };
  }

  #observeRateLimit(profileId, headers) {
    const observed = rateLimitHeader(headers);
    if (!observed) return;
    this.rateLimits.set(profileId, observed);
    if (observed.remaining === 0 && observed.until) {
      this.#armRateCooldown(profileId, observed.until);
    }
  }

  async #pacedWrite(profileId, path, task) {
    const previous = this.writeQueues.get(profileId) || Promise.resolve();
    const run = previous.catch(() => undefined).then(async () => {
      // A long upstream Retry-After must be returned to AIOStreams immediately;
      // holding its HTTP request open would exceed its delivery timeout.
      this.#throwIfCooling(profileId, path);

      const notBefore = Number(this.writeNotBefore.get(profileId) || 0);
      await sleep(Math.max(0, notBefore - Date.now()));

      const started = Date.now();
      this.writeNotBefore.set(profileId, started + WRITE_INTERVAL_MS);

      try {
        return await task();
      } catch (err) {
        if (err?.status === 429) this.#armRateCooldown(profileId, err.retryAfter);
        throw err;
      }
    });

    this.writeQueues.set(profileId, run.catch(() => undefined));
    return run;
  }

  async requestDetailed(profileId, path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();

    if (method === 'GET') {
      this.#throwIfCooling(profileId, path);
      try {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers);
        return result;
      } catch (err) {
        if (err?.status === 429) this.#armRateCooldown(profileId, err.retryAfter);
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
    this.#throwIfCooling(profileId, '/sync/*');
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
