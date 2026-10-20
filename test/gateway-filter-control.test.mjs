import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from '../src/server.mjs';
import { deriveProfileKey } from '../src/crypto.mjs';
import { gatewayFilterKey, gatewayFilteringEnabled, setGatewayFilteringEnabled } from '../src/gateway-control.mjs';
import { renderUnifiedGatewayPanel } from '../src/unified-gateway-panel.mjs';

const PROFILE = 'a'.repeat(24), OTHER = 'b'.repeat(24);
const origin = 'http://127.0.0.1:7000';
function fixture() {
  const map = new Map();
  const counters = { writes: 0, events: 0 };
  const db = {
    getProfile(id) { return id === PROFILE || id === OTHER ? { id, name: 'Synthetic' } : null; },
    listProfiles() { return [{ id: PROFILE, name: 'Synthetic', connected_at: 1 }]; },
    ping() { return true; },
    schemaVersion() { return 1; },
    countConnectedProfiles() { return 1; },
    recentEvents() { return []; },
    cacheGet() { return null; },
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

test('valid Gateway source is AUTO ON; manual ON/OFF persists without histories or Trakt writes', async t => {
  const { db, config, counters, url, gateway } = await httpFixture(t);
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.equal(counters.writes, 0); // AUTO ON does not insert a preference
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
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.equal(counters.writes, 0);
});

test('setup HTML sends strict-origin only; health and invalid keys stay no-referrer', async t => {
  const { config, url } = await httpFixture(t);
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  const admin = await fetch(url + '/setup?key=' + encodeURIComponent(config.adminKey));
  assert.equal(admin.status, 200);
  assert.equal(admin.headers.get('referrer-policy'), 'strict-origin');
  const profile = await fetch(url + '/u/' + PROFILE + '/setup?key=' + encodeURIComponent(key));
  assert.equal(profile.status, 200);
  assert.equal(profile.headers.get('referrer-policy'), 'strict-origin');
  // Policy must never ask Safari to send a full URL that includes ?key=.
  assert.notEqual(profile.headers.get('referrer-policy'), 'same-origin');
  assert.notEqual(profile.headers.get('referrer-policy'), 'unsafe-url');
  const health = await fetch(url + '/health');
  assert.equal(health.headers.get('referrer-policy'), 'no-referrer');
  const invalid = await fetch(url + '/setup?key=invalid');
  assert.equal(invalid.status, 404);
  assert.equal(invalid.headers.get('referrer-policy'), 'no-referrer');
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

test('source-scoped manual OFF persists but never suppresses another configured user/source', () => {
  const { db, config, gateway } = fixture();
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  setGatewayFilteringEnabled({ db, config, gateway, profileId: PROFILE, enabled: false });
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  const remux = { ...config, jellyfinGatewaySourceKind: 'remux',
    jellyfinGatewayUpstreamUrl: 'http://remux:3000/jellyfin' };
  assert.equal(gatewayFilteringEnabled({ db, config: remux, profileId: PROFILE }), true);
  const otherUser = { ...config, jellyfinGatewayUserId: 'different-user-999' };
  assert.equal(gatewayFilteringEnabled({ db, config: otherUser, profileId: PROFILE }), true);
  const native = { ...config, jellyfinGatewaySourceKind: 'jellyfin' };
  assert.equal(gatewayFilteringEnabled({ db, config: native, profileId: PROFILE }), true);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
});

test('unprovisioned transport rejects controls despite preconfigured AUTO ON policy', async t => {
  const { db, config, counters, url } = await httpFixture(t, { gateway: null });
  const key = deriveProfileKey(config.bridgeSecret, 'setup', PROFILE);
  const r = await form(url, key, 'on');
  assert.equal(r.status, 409);
  assert.equal(counters.writes, 0);
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), true);
  assert.throws(() => setGatewayFilteringEnabled({
    db, config, gateway: null, profileId: PROFILE, enabled: true,
  }), /listener_or_source/);
});

test('AUTO ON fails closed for disabled, unmapped, incomplete or malformed preference', () => {
  const { db, config, counters } = fixture();
  const disabled = { ...config, jellyfinGatewayEnabled: false };
  const incomplete = { ...config, jellyfinGatewayUserId: '' };
  const wrong = { ...config, jellyfinGatewayProfileId: OTHER };
  for (const item of [disabled, incomplete, wrong]) {
    assert.equal(gatewayFilteringEnabled({ db, config: item, profileId: PROFILE }), false);
  }
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: OTHER }), false);
  assert.equal(gatewayFilteringEnabled({ db: null, config, profileId: PROFILE }), false);
  const key = gatewayFilterKey(PROFILE, config);
  db.cacheSet(key, { enabled: 'yes' });
  assert.equal(gatewayFilteringEnabled({ db, config, profileId: PROFILE }), false);
  assert.equal(counters.events, 0);
});
