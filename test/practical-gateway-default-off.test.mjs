import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadConfig } from '../src/config.mjs';

test('practical Gateway is disabled and graph import is off by default', () => {
  const settings = {
    PUBLIC_BASE_URL: 'http://127.0.0.1:7777',
    TRAKT_CLIENT_ID: 'synthetic-no-trakt',
    TRAKT_CLIENT_SECRET: 'synthetic-no-trakt',
    BRIDGE_SECRET_KEY: 'synthetic-no-real-secret',
    ADMIN_KEY: 'synthetic-no-real-admin',
    JELLYFIN_GATEWAY_ENABLED: undefined,
    JELLYFIN_GATEWAY_LEGACY_IMPORT: undefined,
    JELLYFIN_GATEWAY_RESUME_PASSTHROUGH: undefined,
  };
  const previous = Object.fromEntries(
    Object.keys(settings).map(key => [key, process.env[key]]));
  try {
    for (const [key, value] of Object.entries(settings)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    const config = loadConfig();
    assert.equal(config.jellyfinGatewayEnabled, false);
    assert.equal(config.jellyfinGatewayLegacyImport, 'off');
    assert.equal(config.jellyfinGatewayResumePassThrough, false);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test('R23D lab entrypoints are not imported by the unified Bridge', () => {
  const index = readFileSync(new URL('../src/index.mjs', import.meta.url), 'utf8');
  const trakt = readFileSync(new URL('../src/trakt.mjs', import.meta.url), 'utf8');
  assert.match(index, /createJellyfinGateway\(\{ config, db \}\)/);
  assert.doesNotMatch(index, /gateway-standalone|guardian|docker\.sock|systemd/);
  assert.match(trakt, /if \(this\.config\.jellyfinGatewayEnabled === true\) \{\s*identityContinuity = observeVerifiedShowIdentity/);
});
