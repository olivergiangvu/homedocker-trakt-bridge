import test from 'node:test';
import assert from 'node:assert/strict';
import {
  validateGatewayConfig, validatedNextUpTimeoutMs, upstreamTimeoutForRoute,
} from '../src/jellyfin-gateway.mjs';
import { canaryGatewayConfig } from '../src/gateway-standalone.mjs';
import { loadConfig } from '../src/config.mjs';

const config = (nextUpTimeoutMs) => ({
  jellyfinGatewayEnabled: true,
  jellyfinGatewayUpstreamUrl: 'http://127.0.0.1:3240/jellyfin',
  jellyfinGatewayUserId: '12345678abcdef',
  jellyfinGatewayProfileId: 'a'.repeat(24),
  ...(nextUpTimeoutMs === undefined
    ? {} : { jellyfinGatewayNextUpTimeoutMs: nextUpTimeoutMs }),
});

const proof = { profileId: 'a'.repeat(24) };
const env = {
  JELLYFIN_GATEWAY_AIO_USER_ID: '12345678abcdef',
  JELLYFIN_GATEWAY_UPSTREAM_URL: 'http://127.0.0.1:3240/jellyfin',
};

test('Gateway v1.4 unchanged default is 8s for all upstream fetches', () => {
  const options = validateGatewayConfig(config());
  assert.equal(options.nextUpTimeoutMs, 8000);
  assert.equal(validatedNextUpTimeoutMs(), 8000);
  assert.equal(upstreamTimeoutForRoute(options, true), 8000);
  assert.equal(upstreamTimeoutForRoute(options, false), 8000);
});

test('only first NextUp upstream request can opt into bounded 15s timeout', () => {
  const options = validateGatewayConfig(config(15000));
  assert.equal(options.nextUpTimeoutMs, 15000);
  assert.equal(upstreamTimeoutForRoute(options, true), 15000);
  assert.equal(upstreamTimeoutForRoute(options, false), 8000);
  // Resume passthrough and separate series metadata/Users-Me keep 8s.
});

test('rejects unsafe/unbounded/malformed timeout without silent clamping', () => {
  for (const timeout of [0, 1, 7999, 20001, 60000, -1, NaN, Infinity,
                         '15000', 15000.5, false, null]) {
    assert.throws(() => validateGatewayConfig(config(timeout)),
                  /NextUp timeout/);
  }
  assert.equal(validatedNextUpTimeoutMs(20000), 20000);
});

test('standalone canary validates environment independently and defaults to 8s', () => {
  assert.equal(canaryGatewayConfig(proof, env).jellyfinGatewayNextUpTimeoutMs, 8000);
  assert.equal(canaryGatewayConfig(proof, {
    ...env, JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS: '15000',
  }).jellyfinGatewayNextUpTimeoutMs, 15000);
  for (const value of ['7999', '20001', 'Infinity', 'NaN', '12000.5', 'false']) {
    assert.throws(() => canaryGatewayConfig(proof, {
      ...env, JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS: value,
    }), /timeout/);
  }
});

test('Bridge env is strict and the default does not silently change', () => {
  const previous = new Map();
  const values = {
    PUBLIC_BASE_URL: 'http://127.0.0.1:17041',
    TRAKT_CLIENT_ID: 'only-synthetic',
    TRAKT_CLIENT_SECRET: 'only-synthetic',
    BRIDGE_SECRET_KEY: 'only-synthetic',
    ADMIN_KEY: 'only-synthetic',
  };
  const keys = [...Object.keys(values), 'JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS'];
  for (const k of keys) previous.set(k, process.env[k]);
  try {
    for (const [k, v] of Object.entries(values)) process.env[k] = v;
    delete process.env.JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS;
    assert.equal(loadConfig().jellyfinGatewayNextUpTimeoutMs, 8000);
    process.env.JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS = '15000';
    assert.equal(loadConfig().jellyfinGatewayNextUpTimeoutMs, 15000);
    for (const bad of ['1000', '60000', '15.5', 'bogus']) {
      process.env.JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS = bad;
      assert.throws(() => loadConfig(), /JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS/);
    }
  } finally {
    for (const k of keys) {
      if (previous.get(k) === undefined) delete process.env[k];
      else process.env[k] = previous.get(k);
    }
  }
});
