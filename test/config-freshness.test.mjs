import test from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from '../src/config.mjs';

const REQUIRED = {
  PUBLIC_BASE_URL: 'https://bridge.example.test',
  TRAKT_CLIENT_ID: 'client',
  TRAKT_CLIENT_SECRET: 'secret',
  BRIDGE_SECRET_KEY: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  ADMIN_KEY: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
};

function withEnv(values, fn) {
  const keys = new Set([
    ...Object.keys(REQUIRED),
    ...Object.keys(values),
    'PULL_CACHE_TTL_SECONDS',
    'PULL_HINT_SECONDS',
    'PULL_TTL_SECONDS',
  ]);
  const before = new Map([...keys].map((key) => [key, process.env[key]]));
  try {
    for (const [key, value] of Object.entries(REQUIRED)) process.env[key] = value;
    for (const key of ['PULL_CACHE_TTL_SECONDS', 'PULL_HINT_SECONDS', 'PULL_TTL_SECONDS']) delete process.env[key];
    for (const [key, value] of Object.entries(values)) {
      if (value == null) delete process.env[key];
      else process.env[key] = String(value);
    }
    return fn();
  } finally {
    for (const [key, value] of before) {
      if (value == null) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test('freshness defaults are split but aligned at 60 seconds', () => {
  withEnv({}, () => {
    const config = loadConfig();
    assert.equal(config.pullCacheTtlSeconds, 60);
    assert.equal(config.pullHintSeconds, 60);
    assert.equal(config.pullTtlSeconds, 60);
  });
});

test('manifest hint can be lower than bridge cache TTL', () => {
  withEnv({ PULL_CACHE_TTL_SECONDS: 45, PULL_HINT_SECONDS: 30 }, () => {
    const config = loadConfig();
    assert.equal(config.pullCacheTtlSeconds, 45);
    assert.equal(config.pullHintSeconds, 30);
    assert.equal(config.pullTtlSeconds, 45);
  });
});

test('legacy PULL_TTL_SECONDS remains an upgrade fallback', () => {
  withEnv({ PULL_TTL_SECONDS: 90 }, () => {
    const config = loadConfig();
    assert.equal(config.pullCacheTtlSeconds, 90);
    assert.equal(config.pullHintSeconds, 90);
  });
});
