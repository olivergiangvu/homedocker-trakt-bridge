/**
 * Full G2-1B mock-network chain using ACTUAL v1.4 Gateway code, not a
 * stand-in Gateway: fake AIO → real Gateway + Odin shadow ingress.
 * All identities and responses synthetic; zero HomeDocker/Trakt contact.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { createOdinShadowIngress } from '../src/odin-g2-shadow-ingress.mjs';

const PROFILE = 'b'.repeat(24);
const USER = 'synthetic_odin_user_1234';
const TOKEN = 'synthetic-session-token-not-real';
const ALIAS = 'tt87654321';
const CANONICAL = 'tt12345678';

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return 'http://127.0.0.1:' + server.address().port;
}
async function close(server) {
  if (!server.listening) return;
  server.closeAllConnections();
  await new Promise(r => server.close(r));
}
async function waitFor(check) {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > 3000) throw new Error('GATEWAY_SHADOW_TIMEOUT');
    await new Promise(r => setTimeout(r, 15));
  }
}

async function chain({ wrongUser = false, singleItem = false } = {}) {
  const state = { aioMethods: [], metadata: 0 };
  const original = {
    Items: [
      { Id: 'episode_synthetic_alias', Type: 'Episode',
        SeriesId: 'series_synthetic_alias', UserData: {
          PlaybackPositionTicks: 0, LastPlayedDate: '2026-10-01T00:00:00Z',
        } },
      { Id: 'episode_synthetic_canonical', Type: 'Episode',
        SeriesId: 'series_synthetic_canonical', UserData: {
          PlaybackPositionTicks: 6000000,
          LastPlayedDate: '2026-10-02T00:00:00Z',
        } },
    ],
    TotalRecordCount: 2,
  };
  if (singleItem) {
    original.Items = original.Items.slice(0, 1);
    original.TotalRecordCount = 1;
  }
  const aio = http.createServer((req, res) => {
    state.aioMethods.push(req.method);
    if (req.headers['x-emby-token'] !== TOKEN) {
      res.writeHead(401).end('{}'); return;
    }
    const path = req.url.split('?')[0];
    let data;
    if (path === '/jellyfin/Shows/NextUp') data = original;
    else if (path === '/jellyfin/Users/Me') {
      data = { Id: wrongUser ? 'not_the_same_odin_user' : USER };
    } else if (path === '/jellyfin/Items/series_synthetic_alias'
      || path === '/jellyfin/Items/series_synthetic_canonical') {
      state.metadata++;
      data = {
        Id: path.split('/').pop(), Type: 'Series',
        ProviderIds: { Imdb: path.endsWith('_alias') ? ALIAS : CANONICAL },
      };
    } else if (path === '/jellyfin/UserItems/Resume') {
      data = { Items: [{ Id: 'resume_independent' }],
        TotalRecordCount: 1 };
    } else { res.writeHead(404).end('{}'); return; }
    res.writeHead(200, { 'content-type': 'application/json',
      'cache-control': 'private', 'etag': '"AIO_DIRECT_SOURCE"' });
    res.end(JSON.stringify(data));
  });
  const aioUrl = await listen(aio);
  const db = {
    cacheGet(key) {
      return key === 'identity-graph:v1:' + PROFILE ? {
        revision: 1, conflicts: [],
        shows: { '7': { canonicalImdb: CANONICAL, aliases: [ALIAS] } },
      } : null;
    },
  };
  const gateway = createJellyfinGateway({
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayUpstreamUrl: aioUrl + '/jellyfin',
      jellyfinGatewayUserId: USER,
      jellyfinGatewayProfileId: PROFILE,
      jellyfinGatewayResumePassThrough: false,
    }, db,
  });
  const gatewayUrl = await listen(gateway);
  const ingress = createOdinShadowIngress({
    aioBase: aioUrl + '/jellyfin', gatewayBase: gatewayUrl,
    shadowApproved: true, trustedGatewayServer: gateway,
  });
  const ingressUrl = await listen(ingress);
  return { aio, gateway, ingress, ingressUrl, state, original,
    async shutdown() { await close(ingress); await close(gateway); await close(aio); } };
}

test('ACTUAL Gateway shadow can filter synthetic alias 2→1 while Odin receives ORIGINAL 2 untouched', async (t) => {
  const h = await chain();
  t.after(() => h.shutdown());
  const res = await fetch(h.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20', {
    headers: { 'x-emby-token': TOKEN }, redirect: 'manual',
  });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('etag'), '"AIO_DIRECT_SOURCE"');
  assert.deepEqual(await res.json(), h.original);
  await waitFor(() => h.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  const g = h.gateway.gatewayMetricsSnapshot();
  assert.equal(g.nextUpRequests, 1);
  assert.equal(g.nextUpNormalizedResponses, 1);
  assert.equal(g.nextUpRemovedItems, 1);
  assert.equal(g.nextUpIdentityEligible, 1);
  assert.equal(g.nextUpIdentityUserVerified, 1);
  assert.equal(g.nextUpIdentityBelowTwoItems, 0);
  assert.equal(g.nextUpIdentityExcludedQuery, 0);
  assert.equal(g.failOpenNormalizationErrors, 0);
  assert.equal(h.state.metadata, 2);
  assert.ok(h.state.aioMethods.every(m => m === 'GET'));
  assert.equal(h.ingress.shadowMetricsSnapshot().gateway_client_responses, 0);
  assert.equal(h.ingress.shadowMetricsSnapshot().playback_writes_forwarded, 0);
  assert.equal(h.ingress.shadowMetricsSnapshot().production_database_writes, 0);
  const json = JSON.stringify({ ...h.ingress.shadowMetricsSnapshot(), ...g });
  for (const value of [TOKEN, USER, PROFILE, ALIAS, CANONICAL]) {
    assert.equal(json.includes(value), false);
  }

  const resume = await fetch(h.ingressUrl + '/jellyfin/UserItems/Resume', {
    headers: { 'x-emby-token': TOKEN },
  });
  assert.equal(resume.status, 200);
  assert.deepEqual((await resume.json()).Items,
    [{ Id: 'resume_independent' }]);
  assert.equal(h.gateway.gatewayMetricsSnapshot().resumePassthroughRequests, 0);
});

test('different AIO /Users/Me identity fails open: shadow leaves synthetic NextUp unfiltered', async (t) => {
  const h = await chain({ wrongUser: true });
  t.after(() => h.shutdown());
  const res = await fetch(h.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20', {
    headers: { 'x-emby-token': TOKEN }, redirect: 'manual',
  });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), h.original);
  await waitFor(() => h.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  const g = h.gateway.gatewayMetricsSnapshot();
  assert.equal(g.nextUpRequests, 1);
  assert.equal(g.nextUpNormalizedResponses, 0);
  assert.equal(g.nextUpRemovedItems, 0);
  assert.equal(g.nextUpUnchangedResponses, 1);
  assert.equal(g.nextUpIdentityEligible, 1);
  assert.equal(g.nextUpIdentityUserVerified, 0);
  assert.equal(g.nextUpIdentityUserMismatch, 1);
  assert.equal(h.state.metadata, 0);
  assert.ok(h.state.aioMethods.every(m => m === 'GET'));
  assert.equal(h.ingress.shadowMetricsSnapshot().gateway_client_responses, 0);
});

test('Odin single-episode NextUp stays unchanged, anonymous below-two reason increments', async t => {
  const h = await chain({ singleItem: true });
  t.after(() => h.shutdown());
  const response = await fetch(h.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20',
    { headers: { 'x-emby-token': TOKEN } });
  assert.deepEqual(await response.json(), h.original);
  await waitFor(() => h.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  const g = h.gateway.gatewayMetricsSnapshot();
  assert.equal(g.nextUpIdentityBelowTwoItems, 1);
  assert.equal(g.nextUpIdentityEligible, 0);
  assert.equal(g.nextUpIdentityUserVerified, 0);
  assert.equal(g.nextUpRemovedItems, 0);
  assert.equal(h.state.metadata, 0);
  assert.equal(h.ingress.shadowMetricsSnapshot().gateway_client_responses, 0);
});

test('SeriesId-constrained NextUp stays upstream-defined and reports only excluded-query count', async t => {
  const h = await chain();
  t.after(() => h.shutdown());
  const response = await fetch(h.ingressUrl +
    '/jellyfin/Shows/NextUp?SeriesId=synthetic_series&Limit=20',
    { headers: { 'x-emby-token': TOKEN } });
  assert.deepEqual(await response.json(), h.original);
  await waitFor(() => h.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  const g = h.gateway.gatewayMetricsSnapshot();
  assert.equal(g.nextUpIdentityExcludedQuery, 1);
  assert.equal(g.nextUpIdentityEligible, 0);
  assert.equal(g.nextUpIdentityUserVerified, 0);
  assert.equal(h.state.metadata, 0);
  assert.equal(g.nextUpRemovedItems, 0);
  const report = JSON.stringify(g);
  assert.equal(report.includes('synthetic_series'), false);
  assert.equal(report.includes(USER), false);
  assert.equal(report.includes(TOKEN), false);
});
