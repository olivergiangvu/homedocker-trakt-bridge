import { BridgeError } from './errors.mjs';
import { TraktClient } from './trakt.mjs';

const WRITE_INTERVAL_MS = 1100;
const DEFAULT_READ_COOLDOWN_MS = 60_000;

function sleep(ms) {
  if (!(ms > 0)) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryAfterMs(value) {
  if (value == null || value === '') return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
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
    this.readCooldownUntil = new Map();
    this.writeNotBefore = new Map();
    this.writeQueues = new Map();
    this.rateLimits = new Map();
  }

  rateLimitSnapshot(profileId) {
    const readCooldownUntil = Number(this.readCooldownUntil.get(profileId) || 0);
    const writeNotBefore = Number(this.writeNotBefore.get(profileId) || 0);
    const now = Date.now();
    return {
      observed: this.rateLimits.get(profileId) || null,
      readCooldownSeconds: Math.max(0, Math.ceil((readCooldownUntil - now) / 1000)),
      writeCooldownSeconds: Math.max(0, Math.ceil((writeNotBefore - now) / 1000)),
    };
  }

  #observeRateLimit(profileId, headers) {
    const observed = rateLimitHeader(headers);
    if (observed) this.rateLimits.set(profileId, observed);
  }

  #armReadCooldown(profileId, retryAfter) {
    const wait = retryAfterMs(retryAfter) ?? DEFAULT_READ_COOLDOWN_MS;
    const until = Date.now() + Math.max(1000, wait);
    this.readCooldownUntil.set(
      profileId,
      Math.max(Number(this.readCooldownUntil.get(profileId) || 0), until),
    );
  }

  #throwIfReadCooling(profileId, path) {
    const until = Number(this.readCooldownUntil.get(profileId) || 0);
    if (!(until > Date.now())) {
      if (until) this.readCooldownUntil.delete(profileId);
      return;
    }
    throw new BridgeError('Trakt GET rate limit cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(until),
      code: 'trakt_read_cooldown',
      upstreamPath: path,
    });
  }

  async #pacedWrite(profileId, task) {
    const previous = this.writeQueues.get(profileId) || Promise.resolve();
    const run = previous.catch(() => undefined).then(async () => {
      const notBefore = Number(this.writeNotBefore.get(profileId) || 0);
      await sleep(Math.max(0, notBefore - Date.now()));

      const started = Date.now();
      this.writeNotBefore.set(profileId, started + WRITE_INTERVAL_MS);

      try {
        return await task();
      } catch (err) {
        if (err?.status === 429) {
          const retry = retryAfterMs(err.retryAfter);
          const until = Date.now() + Math.max(WRITE_INTERVAL_MS, retry ?? WRITE_INTERVAL_MS);
          this.writeNotBefore.set(
            profileId,
            Math.max(Number(this.writeNotBefore.get(profileId) || 0), until),
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
      this.#throwIfReadCooling(profileId, path);
      try {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers);
        return result;
      } catch (err) {
        if (err?.status === 429) this.#armReadCooldown(profileId, err.retryAfter);
        throw err;
      }
    }

    if (isWriteMethod(method)) {
      return this.#pacedWrite(profileId, async () => {
        const result = await super.requestDetailed(profileId, path, options);
        this.#observeRateLimit(profileId, result.headers);
        return result;
      });
    }

    return super.requestDetailed(profileId, path, options);
  }

  async pullState(profileId, since = null) {
    this.#throwIfReadCooling(profileId, '/sync/*');
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
