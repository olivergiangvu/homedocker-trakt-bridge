import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from '../src/server.mjs';
import { deriveProfileKey } from '../src/crypto.mjs';
import { buildGatewayDashboardStatus } from '../src/gateway-dashboard.mjs';

const OWNER = 'a'.repeat(24);
const OTHER = 'b'.repeat(24);
const USER = '11111111abcdef00';
const CANONICAL = 'tt44051354';
const ALIAS = 'tt44094505';

function context(overrides = {}) {
  const calls = { peek: 0, writes: 0, get: 0 };
  const graph = {
    revision: 4,
    shows: {
      '263102': {
        canonicalImdb: CANONICAL, aliases: [ALIAS],
        tmdb: 276470, tvdb: null,
      },
      '263103': {
        canonicalImdb: 'tt12345678', aliases: [],
        tmdb: null, tvdb: null,
      },
    },
    conflicts: [{ id: 'tt99999999', reason: 'quarantined', observedShow: 263103 }],
  };
  const db = {
    getProfile(id) {
      return [OWNER, OTHER].includes(id)
        ? { id, name: 'Trakt account', connected_at: 1 } : null;
    },
    cachePeek(key) {
      calls.peek++;
      assert.equal(key, 'identity-graph:v1:' + OWNER);
      return graph;
    },
    cacheGet() {
      calls.get++;
      throw new Error('UI MUST NOT call cacheGet (may DELETE rows)');
    },
    cacheSet() { calls.writes++; throw new Error('NO_WRITES'); },
    cacheDelete() { calls.writes++; throw new Error('NO_WRITES'); },
  };
  const config = {
    publicBaseUrl: 'http://127.0.0.1:7000',
    adminKey: 'private-admin-key',
    bridgeSecret: 'private-bridge-secret',
    jellyfinGatewayEnabled: false,
    jellyfinGatewayResumePassThrough: false,
    jellyfinGatewayProfileId: OWNER,
    jellyfinGatewayUserId: USER,
    jellyfinGatewayUpstreamUrl: 'http://aiostreams:3000/jellyfin',
    ...overrides,
  };
  return { calls, db, config };
}

test('disabled gateway is accurately shown in same Bridge dashboard status', () => {
  const { calls, db, config } = context();
  const actual = buildGatewayDashboardStatus({
    db, config, profileId: OWNER,
  });
  assert.equal(actual.state, 'disabled');
  assert.equal(actual.enabled, false);
  assert.equal(actual.listening, false);
  assert.equal(actual.identity.verifiedShows, 2);
  assert.equal(actual.identity.aliasGroups, 1);
  assert.equal(actual.identity.verifiedAliases, 1);
  assert.equal(actual.identity.quarantinedConflicts, 1);
  assert.equal(actual.identity.revision, 4);
  assert.equal(actual.resumeMode, 'off');
  assert.equal(actual.productionRoutingQualified, false);
  assert.equal(actual.viewOnly, true);
  assert.equal(calls.peek, 1);
  assert.equal(calls.get, 0);
  assert.equal(calls.writes, 0);
  const json = JSON.stringify(actual);
  for (const secret of [USER, OWNER, CANONICAL, ALIAS, config.adminKey]) {
    assert.ok(!json.includes(secret), 'response must not leak identifiers/secrets');
  }
});

test('other profile is not allowed to read mapped profile graph', () => {
  const { calls, db, config } = context({ jellyfinGatewayEnabled: true });
  const result = buildGatewayDashboardStatus({
    db, config, profileId: OTHER, gateway: { listening: true },
  });
  assert.equal(result.state, 'not_mapped_to_this_profile');
  assert.equal(result.identity.available, false);
  assert.equal(result.identity.aliasGroups, 0);
  assert.equal(result.listening, false);
  assert.equal(calls.peek, 0);
  assert.equal(calls.get, 0);
});

test('configured listener is not incorrectly described as live-client qualified', () => {
  const { db, config } = context({
    jellyfinGatewayEnabled: true,
    jellyfinGatewayResumePassThrough: true,
  });
  const result = buildGatewayDashboardStatus({
    db, config, profileId: OWNER, gateway: { listening: true },
  });
  assert.equal(result.state, 'listener_active_not_client_qualified');
  assert.equal(result.resumeMode, 'raw_passthrough');
  assert.equal(result.productionRoutingQualified, false);
  assert.equal(result.userMappingConfigured, true);
});

test('missing user mapping does not produce a false ready state', () => {
  const { db, config } = context({
    jellyfinGatewayEnabled: true,
    jellyfinGatewayUserId: '',
  });
  const result = buildGatewayDashboardStatus({
    db, config, profileId: OWNER, gateway: { listening: false },
  });
  assert.equal(result.state, 'mapping_incomplete');
  assert.equal(result.userMappingConfigured, false);
});

test('profile-only JSON status API requires existing setup key and is non-mutating', async t => {
  const { config, db, calls } = context();
  const server = createServer({ config, db, trakt: null });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const base = 'http://127.0.0.1:' + server.address().port;
  const endpoint = '/u/' + OWNER + '/gateway/status.json';
  const unauth = await fetch(base + endpoint);
  assert.equal(unauth.status, 404);
  assert.deepEqual(await unauth.json(), { error: 'not_found', message: 'Not found' });

  const wrong = await fetch(base + endpoint + '?key=wrong');
  assert.equal(wrong.status, 404);
  const method = await fetch(base + endpoint + '?key='
    + encodeURIComponent(deriveProfileKey(config.bridgeSecret, 'setup', OWNER)), {
    method: 'POST',
  });
  assert.equal(method.status, 405);

  const key = deriveProfileKey(config.bridgeSecret, 'setup', OWNER);
  const valid = await fetch(base + endpoint + '?key=' + encodeURIComponent(key));
  assert.equal(valid.status, 200);
  assert.equal(valid.headers.get('cache-control'), 'no-store');
  assert.equal(valid.headers.get('referrer-policy'), 'no-referrer');
  const body = await valid.json();
  assert.equal(body.identity.aliasGroups, 1);
  assert.equal(body.productionRoutingQualified, false);
  assert.equal(calls.peek, 1);
  assert.equal(calls.writes, 0);
  assert.equal(calls.get, 0);

  const secondary = await fetch(base + '/u/' + OTHER
    + '/gateway/status.json?key='
    + encodeURIComponent(deriveProfileKey(config.bridgeSecret, 'setup', OTHER)));
  assert.equal(secondary.status, 200);
  assert.equal((await secondary.json()).identity.aliasGroups, 0);
  assert.equal(calls.peek, 1);
});
