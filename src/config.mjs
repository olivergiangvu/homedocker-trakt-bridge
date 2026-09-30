import path from 'node:path';

export const APP_NAME = 'HomeDocker Trakt Bridge';
export const APP_VERSION = '0.2.0';

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

export function loadConfig() {
  const publicBaseUrl = requireEnv('PUBLIC_BASE_URL').replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(publicBaseUrl)) {
    throw new Error('PUBLIC_BASE_URL must start with http:// or https://');
  }

  const dataDir = (process.env.DATA_DIR || '/app/data').trim();
  const port = intEnv('PORT', 7000, 1, 65535);

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
    pullTtlSeconds: intEnv('PULL_TTL_SECONDS', 300, 30, 3600),
    pullMaxPages: intEnv('PULL_MAX_PAGES', 500, 1, 1000),
    userAgent: (process.env.USER_AGENT || `${APP_NAME.replaceAll(' ', '-')}/${APP_VERSION}`).trim(),
  };
}
