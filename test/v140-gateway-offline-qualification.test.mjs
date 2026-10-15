import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { observeVerifiedShowIdentity } from '../src/identity-continuity.mjs';

// These are synthetic identity fixtures derived from a verified Trakt/AIO
// alias *shape*, not a snapshot of any HomeDocker user's media or watch-state.
const PROFILE = 'a'.repeat(24);
const USER = '12345678abcdef00';
const TOKEN = 'synthetic-session-only';
const CANON = 'tt44051354';
const ALIAS = 'tt44094505';
const row = (id, series, ticks, when = undefined) => ({
  Id: id, SeriesId: series, Type: 'Episode', IndexNumber: 11,
  UserData: {
    PlaybackPositionTicks: ticks,
    ...(when ? { LastPlayedDate: when } : {}),
  },
});
const twin = [
  row('episodeAlias', 'seriesAlias', 0, '2026-10-01T00:00:00Z'),
  row('episodeCanonical', 'seriesCanonical', 900, '2026-10-02T00:00:00Z'),
];
const data = entries => ({
  Items: entries,
  TotalRecordCount: entries.length,
  StartIndex: 0,
  PrefixSnapshot: 'synthetic-only',
});

function syntheticDb() {
  const cache = new Map();
  const db = {
    cacheGet: k => structuredClone(cache.get(k) ?? null),
    cacheSet: (k, v) => cache.set(k, structuredClone(v)),
  };
  observeVerifiedShowIdentity(db, PROFILE, {
    trakt: 263102, imdb: CANON, tmdb: 276470,
  }, ALIAS);
  return db;
}

async function fixture(t, {
  nextup = data(twin), expectedUser = USER, responseUser = USER,
  missingGraph = false, metadataHttp = 200,
  virtualLatencyMs = 0, nextupDeadlineMs = 8000,
  metadataDelayBeyondBudget = false,
  activeResume = false,
} = {}) {
  const observed = [];
  const budgets = new WeakMap();
  // Virtual upstream milliseconds are compared to actual configured AbortSignal
  // budgets. No 20-second wall-clock wait; NOT a real latency percentile.
  t.mock.method(AbortSignal, 'timeout', ms => {
    const controller = new AbortController();
    budgets.set(controller.signal, ms);
    return controller.signal;
  });
  const fetchImpl = async (url, options) => {
    const path = new URL(url).pathname;
    assert.equal(options.method, 'GET');
    assert.equal(options.redirect, 'manual');
    const token = options.headers.get('x-emby-token');
    observed.push({ path, budget: budgets.get(options.signal), authPresent: !!token });
    if (path === '/jellyfin/Shows/NextUp') {
      if (virtualLatencyMs > budgets.get(options.signal)) {
        throw new Error('synthetic upstream exceeded its deadline');
      }
      return new Response(JSON.stringify(nextup), {
        status: 200, headers: { 'content-type': 'application/json', etag: 'synthetic' },
      });
    }
    if (path === '/jellyfin/Users/Me') {
      return new Response(JSON.stringify({ Id: responseUser }), { status: 200 });
    }
    if (path.startsWith('/jellyfin/Items/')) {
      if (metadataDelayBeyondBudget && budgets.get(options.signal) <= 8000) {
        throw new Error('synthetic metadata deadline');
      }
      if (metadataHttp !== 200) return new Response('{}', { status: metadataHttp });
      const id = path.slice('/jellyfin/Items/'.length);
      const imdb = id === 'seriesAlias' ? ALIAS
        : id === 'seriesCanonical' ? CANON
        : 'tt12345678';
      return new Response(JSON.stringify({
        Id: id, Type: 'Series', ProviderIds: { Imdb: imdb },
      }), { status: 200 });
    }
    throw new Error('unexpected route in synthetic fixture');
  };

  const gateway = createJellyfinGateway({
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayUpstreamUrl: 'http://127.0.0.1:3240/jellyfin',
      jellyfinGatewayUserId: expectedUser,
      jellyfinGatewayProfileId: PROFILE,
      jellyfinGatewayNextUpTimeoutMs: nextupDeadlineMs,
      jellyfinGatewayResumePassThrough: false,
    },
    db: missingGraph ? { cacheGet: () => null } : syntheticDb(),
    fetchImpl,
  });
  gateway.listen(0, '127.0.0.1');
  t.after(() => { if (gateway.listening) gateway.close(); });
  await once(gateway, 'listening');
  return {
    base: 'http://127.0.0.1:' + gateway.address().port,
    observed, gateway, original: nextup,
  };
}

const request = base => fetch(base + '/Shows/NextUp?Limit=50', {
  headers: { 'X-Emby-Token': TOKEN },
});

test('R23C1: exactly one active verified twin wins; upstream ID, UserData and raw reply remain unchanged', async t => {
  const original = data(twin);
  const originalCopy = structuredClone(original);
  const { base, observed, gateway } = await fixture(t, { nextup: original });
  const response = await request(base);
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.Items.length, 1);
  assert.deepEqual(payload.Items[0], twin[1]);
  assert.equal(payload.TotalRecordCount, 1);
  assert.equal(payload.PrefixSnapshot, 'synthetic-only');
  assert.deepEqual(original, originalCopy);
  assert.equal(gateway.gatewayMetricsSnapshot().nextUpRemovedItems, 1);
  assert.deepEqual(observed.map(x => x.path), [
    '/jellyfin/Shows/NextUp', '/jellyfin/Users/Me',
    '/jellyfin/Items/seriesAlias', '/jellyfin/Items/seriesCanonical',
  ]);
  assert.ok(observed.every(x => x.authPresent));
  assert.ok(observed.every(x => x.budget === 8000));
});

test('R23C1: two competing active positions preserve BOTH cards with original bytes', async t => {
  const original = data([
    row('episodeAlias', 'seriesAlias', 500000, '2026-10-15T01:00:00Z'),
    row('episodeCanonical', 'seriesCanonical', 900000, '2026-10-01T01:00:00Z'),
  ]);
  const { base, gateway } = await fixture(t, { nextup: original });
  const reply = await request(base);
  assert.equal(reply.status, 200);
  assert.equal(await reply.text(), JSON.stringify(original));
  assert.equal(gateway.gatewayMetricsSnapshot().nextUpRemovedItems, 0);
});

test('R23C1: unrelated 50-series shelf remains unchanged, despite bounded cold metadata amplification', async t => {
  const fifty = data(Array.from({ length: 50 }, (_, i) => row(
    'episodeUnrelated' + i, 'seriesUnrelated' + i, 100 + i,
  )));
  const { base, observed, gateway } = await fixture(t, { nextup: fifty });
  const response = await request(base);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), JSON.stringify(fifty));
  assert.equal(observed.filter(x => x.path.startsWith('/jellyfin/Items/')).length, 50);
  assert.equal(observed.length, 52); // 1 NextUp + 1 Users/Me + 50 cold-series GET
  assert.equal(gateway.gatewayMetricsSnapshot().nextUpRemovedItems, 0);
  assert.equal(gateway.gatewayMetricsSnapshot().metadataCacheEntries, 50);
});

test('R23C1: wrong user, missing graph and unavailable metadata always preserve original items', async t => {
  for (const variation of [
    { expectedUser: 'differentUserId000', expectation: 'wrong-user' },
    { missingGraph: true, expectation: 'no-graph' },
    { metadataHttp: 429, expectation: 'metadata-429' },
    { metadataHttp: 503, expectation: 'metadata-503' },
    { metadataDelayBeyondBudget: true, expectation: 'metadata-timeout' },
  ]) {
    await t.test(variation.expectation, async inner => {
      const { base, observed, gateway, original } = await fixture(inner, variation);
      const reply = await request(base);
      assert.equal(reply.status, 200);
      assert.equal(await reply.text(), JSON.stringify(original));
      assert.equal(gateway.gatewayMetricsSnapshot().nextUpRemovedItems, 0);
      if (variation.metadataHttp !== undefined || variation.metadataDelayBeyondBudget) {
        assert.equal(gateway.gatewayMetricsSnapshot().failOpenNormalizationErrors, 1);
      }
      if (variation.expectation === 'wrong-user') {
        assert.equal(observed.filter(x => x.path.startsWith('/jellyfin/Items/')).length, 0);
      }
      assert.ok(observed.every(x => x.authPresent));
    });
  }
});

test('R23C2: virtual AIO NextUp latency 8s, 10.5s, 15s, 20s respects opt-in bounded deadline', async t => {
  for (const [virtual, deadline, httpStatus] of [
    [8000, 8000, 200],
    [10507, 8000, 502],
    [10507, 15000, 200],
    [15000, 15000, 200],
    [20000, 15000, 502],
    [20000, 20000, 200],
    [20001, 20000, 502],
  ]) {
    await t.test(`virtual=${virtual}; deadline=${deadline}`, async inner => {
      const { base, observed, gateway } = await fixture(inner, {
        virtualLatencyMs: virtual,
        nextupDeadlineMs: deadline,
        // Missing graph isolates the initial NextUp transport deadline.
        missingGraph: true,
      });
      const reply = await request(base);
      assert.equal(reply.status, httpStatus);
      if (httpStatus === 200) assert.equal((await reply.json()).Items.length, 2);
      else assert.deepEqual(await reply.json(), { error: 'upstream_unavailable' });
      assert.equal(observed.length, 1);
      assert.equal(observed[0].budget, deadline);
      assert.equal(gateway.gatewayMetricsSnapshot().upstreamTransportFailures,
                   httpStatus === 502 ? 1 : 0);
    });
  }
});

test('R23C2: larger NextUp budget never widens user/metadata deadline or unsafe routes', async t => {
  const { base, observed, gateway, original } = await fixture(t, {
    nextupDeadlineMs: 15000, metadataDelayBeyondBudget: true,
  });
  const result = await request(base);
  assert.equal(result.status, 200);
  assert.equal(await result.text(), JSON.stringify(original));
  assert.deepEqual(observed.map(x => x.budget), [15000, 8000, 8000, 8000]);
  assert.equal(gateway.gatewayMetricsSnapshot().failOpenNormalizationErrors, 1);
  const other = await fetch(base + '/Items/sensitive/playbackinfo');
  assert.equal(other.status, 404);
  assert.equal(observed.length, 4);
});
