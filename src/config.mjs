import path from 'node:path';

export const APP_NAME = 'HomeDocker Trakt Bridge';
export const APP_VERSION = '0.1.0';

function requireEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

export function loadConfig() {
  const publicBaseUrl = requireEnv('PUBLIC_BASE_URL').replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(publicBaseUrl)) {
    throw new Error('PUBLIC_BASE_URL must start with http:// or https://');
  }

  const dataDir = (process.env.DATA_DIR || '/app/data').trim();
  const port = Number(process.env.PORT || 7000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be 1..65535');

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
    userAgent: (process.env.USER_AGENT || `${APP_NAME.replaceAll(' ', '-')}/${APP_VERSION}`).trim(),
  };
}
