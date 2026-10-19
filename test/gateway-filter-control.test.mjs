import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from '../src/server.mjs';
import { deriveProfileKey } from '../src/crypto.mjs';
import { gatewayFilteringEnabled, setGatewayFilteringEnabled } from '../src/gateway-control.mjs';
import { renderUnifiedGatewayPanel } from '../src/unified-gateway-panel.mjs';

const PROFILE = 'a'.repeat(24), OTHER = 'b'.repeat(24);
const origin = 'http://127.0.0.1:7000';
function fixture() {
  const map = new Map();
  const counters = { writes: 0, events: 0 };
  const db = {
    getProfile(id) { return id === PROFILE || id === OTHER ? { id, name: 'Synthetic' } : null; },
    cachePeek(key) { return structuredClone(map.get(key) ?? null); },
    cacheSet(key, value) {
      if (!key.startsWith('gateway-filter:v1:')) throw new Error('NON_GATEWAY_WRITE');
      counters.writes++;
      map.set(key, structuredClone(value));
    },
    logEvent() { counters.events++; throw new Error('NO_WATCH_EVENT'); },
  };
  const config = {
    publicBaseUrl: origin, adminKey: 'synthetic-admin-key',
    bridgeSecret: 'synthetic-bridge-secret',
    jellyfinGatewayEnabled: true,
    jellyfinGatewayProfileId: PROFILE,
    jellyfinGatewayUserId: 'userid1234567890',
    jellyfinGatewayUpstreamUrl: 'http://127.0.0.1:5000/jellyfin',
    jellyfinGatewaySourceKind: 'aiostreams',
  };
  return { db, config, gateway: { listening: true }, counters };
}
async function httpFixture(t, opts = {}) {
  const ctx = fixture();
  Object.assign(ctx, opts);
  const srv = createServer({ ...ctx, trakt: null });
  srv.listen(0, '127.0.0.1');
  await once(srv, 'listening');
  t.after(() => srv.close());
  return { ...ctx, url: 'http://127.0.0.1:' + srv.address().port };
}
const form = (url, key, state, opts = {}) => fetch(url + '/u/' + PROFILE
  + '/gateway/filter?key=' + encodeURIComponent(key), {
  method: 'POST', redirect: 'manual',
  headers: { 'content-type': 'application/x-www-form-urlencoded',
    origin, ...(opts.headers || {}) },
  body: 'state=' + state,
});

test('persistent profile ON/OFF preserves preference without histories or Trakt writes', async t => {
  const { db, config, counters, url, gateway } = await httpFixture(t);
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  for (const desired of ['on', 'off', 'on']) {
    const response = await form(url, key, desired);
    assert.equal(response.status, 302);
    assert.match(response.headers.get('location'), /#aio-identity-gateway$/);
    assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }),
      desired === 'on');
  }
  assert.equal(counters.writes, 3);
  assert.equal(counters.events, 0);
  assert.equal(gateway.listening, true);
  const html = renderUnifiedGatewayPanel({
    enabled: true, listening: true, filteringEnabled: true, filterControlAvailable: true,
    userMappingConfigured: true, upstreamConfigured: true, sourceKind: 'aiostreams',
    identity: {verifiedShows:0, verifiedAliases:0, aliasGroups:0, quarantinedConflicts:0},
    telemetry: null,
  }, { controlUrl: '/u/' + PROFILE + '/gateway/filter?key=synthetic'});
  assert.match(html, /Turn OFF/);
  assert.match(html, /name="state" value="off"/);
  assert.match(html, /AIOStreams \(primary\)/);
});

test('control refuses cross-origin, absent origin, wrong bearer, GET and malformed form', async t => {
  const { db, config, url, counters } = await httpFixture(t);
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  assert.equal((await form(url, 'invalid', 'on')).status, 404);
  assert.equal((await form(url, key, 'on', { headers: { origin: 'https://evil.invalid' } })).status, 403);
  assert.equal((await form(url, key, 'on', { headers: { origin: '' } })).status, 403);
  assert.equal((await form(url, key, 'on', { headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
  assert.equal((await form(url, key, 'maybe')).status, 400);
  assert.equal((await fetch(url + '/u/' + PROFILE + '/gateway/filter?key=' + key)).status, 405);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  assert.equal(counters.writes, 0);
});

test('Safari same-origin POST without Origin uses only a strictly matching Referer', async t => {
  const { db, config, url, counters } = await httpFixture(t);
  // Browser form navigation: valid setup page Referer, Origin genuinely absent.
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  const action = url + '/u/' + PROFILE + '/gateway/filter?key=' + encodeURIComponent(key);
  const send = (headers, state = 'on') => fetch(action, {
    method: 'POST', redirect: 'manual',
    headers: { 'content-type': 'application/x-www-form-urlencoded', ...headers },
    body: 'state=' + state,
  });
  const setupReferer = origin + '/u/' + PROFILE + '/setup?key=synthetic-test';
  const valid = await send({ referer: setupReferer, 'sec-fetch-site': 'same-origin' });
  assert.equal(valid.status, 302);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.equal(counters.writes, 1);
  // Ensure all rejections leave the durable preference unchanged.
  for (const headers of [
    {},
    { referer: '' },
    { referer: 'malformed-url' },
    { referer: 'https://evil.invalid/u/' + PROFILE + '/setup' },
    { referer: origin + '.evil.invalid/path' },
    { referer: setupReferer, origin: 'null' },
    { referer: setupReferer, origin: 'https://evil.invalid' },
    { referer: setupReferer, 'sec-fetch-site': 'cross-site' },
  ]) {
    assert.equal((await send(headers, 'off')).status, 403);
    assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  }
  assert.equal((await send({
    referer: setupReferer, 'content-type': 'text/plain',
  }, 'off')).status, 403);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.equal((await send({ referer: setupReferer }, 'off')).status, 302);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  assert.equal(counters.writes, 2);
  assert.equal(counters.events, 0);
});

test('strict Referer fallback also applies to admin switch on HTTPS nonstandard port', async t => {
  const { db, config, url, counters } = await httpFixture(t);
  config.publicBaseUrl = 'https://example.test:18449';
  const adminUrl = url + '/setup/gateway/filter?key=' + encodeURIComponent(config.adminKey);
  const request = (referer, origin) => fetch(adminUrl, {
    method: 'POST', redirect: 'manual',
    headers: {
      'content-type': 'application/x-www-form-urlencoded',
      ...(referer === undefined ? {} : { referer }),
      ...(origin === undefined ? {} : { origin }),
    },
    body: 'state=on',
  });
  assert.equal((await request('https://example.test/setup')).status, 403);
  assert.equal((await request('http://example.test:18449/setup')).status, 403);
  assert.equal((await request('https://example.test:18449/setup')).status, 302);
  assert.equal(counters.writes, 1);
  assert.equal(counters.events, 0);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
});

test('admin can toggle mapped source but another profile cannot', async t => {
  const { db, config, url, counters } = await httpFixture(t);
  const other = deriveProfileKey(config.bridgeSecret, 'setup', OTHER);
  const wrong = await fetch(url + '/u/' + OTHER + '/gateway/filter?key=' + other, {
    method: 'POST', redirect: 'manual',
    headers: { origin, 'content-type': 'application/x-www-form-urlencoded' },
    body: 'state=on',
  });
  assert.equal(wrong.status, 409);
  assert.equal(counters.writes, 0);
  const ok = await fetch(url + '/setup/gateway/filter?key=' + config.adminKey, {
    method: 'POST', redirect: 'manual',
    headers: { origin, 'content-type': 'application/x-www-form-urlencoded' },
    body: 'state=on',
  });
  assert.equal(ok.status, 302);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.equal(counters.writes, 1);
});

test('an ON preference never follows AIO to Remux or another Jellyfin user', () => {
  const { db, config, gateway } = fixture();
  setGatewayFilteringEnabled({ db, config, gateway, profileId: PROFILE, enabled: true });
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  const remux = { ...config, jellyfinGatewaySourceKind: 'remux',
    jellyfinGatewayUpstreamUrl: 'http://remux:3000/jellyfin' };
  assert.equal(gatewayFilteringEnabled({ db, config: remux, profileId: PROFILE }), false);
  const otherUser = { ...config, jellyfinGatewayUserId: 'different-user-999' };
  assert.equal(gatewayFilteringEnabled({ db, config: otherUser, profileId: PROFILE }), false);
  const native = { ...config, jellyfinGatewaySourceKind: 'jellyfin' };
  assert.equal(gatewayFilteringEnabled({ db, config: native, profileId: PROFILE }), false);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
});

test('unprovisioned transport fails closed with no persistent writes', async t => {
  const { db, config, counters, url } = await httpFixture(t, { gateway: null });
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  const r = await form(url, key, 'on');
  assert.equal(r.status, 409);
  assert.equal(counters.writes, 0);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  assert.throws(() => setGatewayFilteringEnabled({
    db, config, gateway: null, profileId: PROFILE, enabled: true,
  }), /listener_or_source/);
});
