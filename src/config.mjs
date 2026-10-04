import path from 'node:path';

export const APP_NAME = 'HomeDocker Trakt Bridge';
export const APP_VERSION = '1.2.0-rc.5';

function requireEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function intEnv(name, fallback, min, max) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function intEnvCompat(primary, legacy, fallback, min, max) {
  const raw = process.env[primary] ?? (legacy ? process.env[legacy] : undefined) ?? fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    const suffix = legacy ? ` (legacy ${legacy} is also accepted)` : '';
    throw new Error(`${primary} must be an integer between ${min} and ${max}${suffix}`);
  }
  return value;
}

function enumEnv(name, fallback, allowed) {
  const value = (process.env[name] || fallback).trim().toLowerCase();
  if (!allowed.includes(value)) {
    throw new Error(`${name} must be one of: ${allowed.join(', ')}`);
  }
  return value;
}

export function loadConfig() {
  const publicBaseUrl = requireEnv('PUBLIC_BASE_URL').replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(publicBaseUrl)) {
    throw new Error('PUBLIC_BASE_URL must start with http:// or https://');
  }

  const dataDir = (process.env.DATA_DIR || '/app/data').trim();
  const port = intEnv('PORT', 7000, 1, 65535);
  const pullCacheTtlSeconds = intEnvCompat('PULL_CACHE_TTL_SECONDS', 'PULL_TTL_SECONDS', 60, 15, 3600);
  const pullHintSeconds = intEnvCompat('PULL_HINT_SECONDS', null, pullCacheTtlSeconds, 15, 3600);

  return {
    publicBaseUrl,
    redirectUri: `${publicBaseUrl}/oauth/callback`,
    traktClientId: requireEnv('TRAKT_CLIENT_ID'),
    traktClientSecret: requireEnv('TRAKT_CLIENT_SECRET'),
    bridgeSecret: requireEnv('BRIDGE_SECRET_KEY'),
    adminKey: requireEnv('ADMIN_KEY'),
    dataDir,
    dbPath: path.join(dataDir, 'bridge.db'),
    port,
    logLevel: (process.env.LOG_LEVEL || 'info').toLowerCase(),
    displayTimeZone: (process.env.DISPLAY_TIMEZONE || 'Asia/Ho_Chi_Minh').trim(),
    pullCacheTtlSeconds,
    pullHintSeconds,
    // Compatibility alias for older tests/local tooling. Runtime code uses the
    // split cache TTL + manifest hint fields above.
    pullTtlSeconds: pullCacheTtlSeconds,
    pullStaleIfErrorSeconds: intEnv('PULL_STALE_IF_ERROR_SECONDS', 3600, 60, 86400),
    pullMaxPages: intEnv('PULL_MAX_PAGES', 500, 1, 1000),
    bulkSingleDedupeSeconds: intEnv('BULK_SINGLE_DEDUPE_SECONDS', 300, 30, 1800),
    historyDedupeSeconds: intEnv('HISTORY_DEDUPE_SECONDS', 300, 0, 1800),
    pullIdentityMode: enumEnv('PULL_IDENTITY_MODE', 'trakt', ['trakt', 'aiostreams']),
    aioReconcilerMode: enumEnv('AIO_RECONCILER_MODE', 'off', ['off', 'detect']),
    aioDbPath: (process.env.AIO_DB_PATH || '/aio-data/db.sqlite').trim(),
    aioReconcileIntervalSeconds: intEnv('AIO_RECONCILE_INTERVAL_SECONDS', 15, 5, 3600),
    aioReconcileGraceSeconds: intEnv('AIO_RECONCILE_GRACE_SECONDS', 30, 5, 3600),
    aioReconcileMaxRows: intEnv('AIO_RECONCILE_MAX_ROWS', 100, 1, 5000),
    userAgent: (process.env.USER_AGENT || `${APP_NAME.replaceAll(' ', '-')}/${APP_VERSION}`).trim(),
  };
}
