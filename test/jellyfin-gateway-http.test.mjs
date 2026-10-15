import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import {
  createJellyfinGateway, validateGatewayConfig,
} from '../src/jellyfin-gateway.mjs';
import { observeVerifiedShowIdentity } from '../src/identity-continuity.mjs';

const PROFILE = 'a'.repeat(24);
const USER = '11111111abcdef00';
const AIO_TOKEN = 'synthetic-AIO-token';
const mockRows = [
  {
    Id: 'episodeOld', SeriesId: 'seriesOld', Type: 'Episode',
    UserData: { PlaybackPositionTicks: 0 }, IndexNumber: 9,
  },
  {
    Id: 'episodeCanon', SeriesId: 'seriesCanon', Type: 'Episode',
    UserData: { PlaybackPositionTicks: 30000000 }, IndexNumber: 10,
  },
];
const reply = { Items: mockRows, TotalRecordCount: 2, StartIndex: 0 };

const fakeDb = () => {
  const map = new Map();
  return {
    cacheGet: (key) => structuredClone(map.get(key) ?? null),
    cacheSet: (key, value) => map.set(key, structuredClone(value)),
  };
};

function serve(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        url: 'http://127.0.0.1:' + server.address().port,
      });
    });
  });
}

function response(res, code, body, headers = {}) {
  res.writeHead(code, {
    'content-type': 'application/json',
    ...headers,
  });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
}

async function fixture(t, {
  enabled = true, expectedUser = USER, metadataFails = false,
  nextUp = reply, graph = true, upstreamError = false,
  upstreamPrefix = '', resumeEnabled = false,
  resumeUpstreamStatus = 200, metadataDelayMs = 0,
} = {}) {
  const hits = [];
  const upstream = await serve((req, res) => {
    hits.push({
      path: req.url, auth: req.headers['x-emby-token'] ? 'present' : 'absent',
    });
    const route = req.url?.startsWith(upstreamPrefix + '/')
      ? req.url.slice(upstreamPrefix.length) : null;
    if (route?.startsWith('/Shows/NextUp')) {
      if (upstreamError) return response(res, 503, { error: 'aio_down' });
      return response(res, 200, nextUp);
    }
    const resumePath = route?.split('?', 1)[0];
    if (resumePath === '/UserItems/Resume'
        || /^\/Users\/[A-Za-z0-9_-]{8,128}\/Items\/Resume$/.test(resumePath ?? '')) {
      if (req.headers['x-emby-token'] !== AIO_TOKEN) {
        return response(res, 401, { error: 'unauthorized' });
      }
      if (resumeUpstreamStatus !== 200) {
        return response(res, resumeUpstreamStatus, { error: 'upstream_status' });
      }
      const query = new URL(route, 'http://mock.invalid').searchParams;
      const start = Number(query.get('StartIndex') ?? 0);
      const limit = Number(query.get('Limit') ?? 12);
      if (!Number.isSafeInteger(start) || start < 0 || limit !== 20) {
        return response(res, 400, { error: 'unsupported_synthetic_page' });
      }
      const Items = Array.from({ length: limit }, (_, i) => {
        const n = start + i;
        return n % 3 === 0
          ? { Id: 'movie' + n, Type: 'Movie',
              UserData: { PlaybackPositionTicks: 100 + n } }
          : { Id: 'episode' + n, SeriesId: 'seriesCanon', Type: 'Episode',
              UserData: { PlaybackPositionTicks: 200 + n } };
      });
      return response(res, 200, {
        Items, TotalRecordCount: start + Items.length, StartIndex: start,
      }, { 'x-aio-resume-contract': 'prefix-count' });
    }
    if (route === '/Users/Me') {
      if (req.headers['x-emby-token'] !== AIO_TOKEN) {
        return response(res, 401, { Message: 'Unauthorized' });
      }
      return response(res, 200, { Id: USER });
    }
    const match = route?.match(/^\/Items\/(seriesCanon|seriesOld)$/);
    if (match) {
      if (metadataFails) return response(res, 500, { error: 'metadata_down' });
      const payload = {
        Type: 'Series', Id: match[1],
        ProviderIds: { Imdb: match[1] === 'seriesOld' ? 'tt44094505' : 'tt44051354' },
      };
      if (metadataDelayMs > 0) {
        setTimeout(() => response(res, 200, payload), metadataDelayMs);
      } else {
        response(res, 200, payload);
      }
      return;
    }
    return response(res, 404, { error: 'no_mock_route' });
  });
  t.after(() => upstream.server.close());

  const db = fakeDb();
  if (graph) {
    observeVerifiedShowIdentity(db, PROFILE, {
      trakt: 263102, imdb: 'tt44051354', tmdb: 276470,
    }, 'tt44094505');
  }

  const config = {
    jellyfinGatewayEnabled: enabled,
    jellyfinGatewayUpstreamUrl: upstream.url + upstreamPrefix,
    jellyfinGatewayProfileId: PROFILE,
    jellyfinGatewayUserId: expectedUser,
    jellyfinGatewayResumePassThrough: resumeEnabled,
  };
  const server = createJellyfinGateway({ config, db });
  t.after(() => server.close());
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = 'http://127.0.0.1:' + server.address().port;
  return { base, hits, db, gateway: server };
}

test('unconfigured gateway is disabled and rejects unexpected upstream URL', () => {
  assert.equal(validateGatewayConfig({ jellyfinGatewayEnabled: false }), null);
  assert.throws(() => validateGatewayConfig({
    jellyfinGatewayEnabled: true,
    jellyfinGatewayUpstreamUrl: 'http://user:pw@127.0.0.1:1234',
    jellyfinGatewayUserId: USER, jellyfinGatewayProfileId: PROFILE,
  }), /upstream/);
  assert.throws(() => createJellyfinGateway({
    config: { jellyfinGatewayEnabled: false }, db: fakeDb(),
  }), /explicitly enabled/);
});

test('authenticated matching user receives one canonical NextUp card', async t => {
  const { base, hits } = await fixture(t);
  const r = await fetch(base + '/Shows/NextUp?Limit=20', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(r.status, 200);
  const output = await r.json();
  assert.deepEqual(output.Items, [mockRows[1]]);
  assert.equal(output.TotalRecordCount, 1);
  assert.equal(output.StartIndex, 0);
  assert.equal(r.headers.get('cache-control'), 'private, no-store');
  assert.ok(hits.some(h => h.path === '/Users/Me'));
  assert.ok(hits.some(h => h.path === '/Items/seriesOld'));
  assert.ok(hits.some(h => h.path === '/Items/seriesCanon'));
  assert.ok(hits.every(h => h.auth === 'present'));
  assert.ok(hits.every(h => h.path.startsWith('/')));
});

test('AIO official /jellyfin prefix is preserved for NextUp, Users/Me and Series details', async t => {
  const { base, hits } = await fixture(t, { upstreamPrefix: '/jellyfin' });
  const result = await fetch(base + '/Shows/NextUp?Limit=20', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(result.status, 200);
  const parsed = await result.json();
  assert.equal(parsed.Items.length, 1);
  assert.equal(parsed.Items[0].Id, 'episodeCanon');
  assert.ok(hits.some(h => h.path === '/jellyfin/Shows/NextUp?Limit=20'));
  assert.ok(hits.some(h => h.path === '/jellyfin/Users/Me'));
  assert.ok(hits.some(h => h.path === '/jellyfin/Items/seriesOld'));
  assert.ok(hits.some(h => h.path === '/jellyfin/Items/seriesCanon'));
  assert.ok(hits.every(h => h.path.startsWith('/jellyfin/')));
});

test('verified AIO user reuses bounded series metadata cache across NextUp refresh', async t => {
  const { base, hits } = await fixture(t);
  for (let n = 0; n < 2; n++) {
    const r = await fetch(base + '/Shows/NextUp?Limit=20', {
      headers: { 'X-Emby-Token': AIO_TOKEN },
    });
    assert.equal(r.status, 200);
    assert.deepEqual((await r.json()).Items, [mockRows[1]]);
  }
  assert.equal(hits.filter(x => x.path === '/Users/Me').length, 2);
  assert.equal(hits.filter(x => x.path === '/Items/seriesOld').length, 1);
  assert.equal(hits.filter(x => x.path === '/Items/seriesCanon').length, 1);
  assert.equal(hits.filter(x => x.path.startsWith('/Shows/NextUp')).length, 2);
});

test('new alias resume is chosen only when canonical is not actively resumed', async t => {
  const altered = {
    Items: [
      { ...mockRows[1], UserData: {
        PlaybackPositionTicks: 0, LastPlayedDate: '2026-10-13T16:00:00Z',
      } },
      { ...mockRows[0], Id: 'episodeNewAlias',
        UserData: {
          PlaybackPositionTicks: 500000,
          LastPlayedDate: '2026-10-14T16:00:00Z',
        } },
    ],
    TotalRecordCount: 2,
  };
  const { base } = await fixture(t, { nextUp: altered });
  const r = await fetch(base + '/Shows/NextUp', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  const result = await r.json();
  assert.equal(result.Items.length, 1);
  assert.equal(result.Items[0].Id, 'episodeNewAlias');
});

test('HTTP gateway preserves both active alias and canonical resumes', async t => {
  const altered = {
    Items: [
      { ...mockRows[1], UserData: {
        PlaybackPositionTicks: 10, LastPlayedDate: '2026-10-13T16:00:00Z',
      } },
      { ...mockRows[0], Id: 'episodeNewAlias',
        UserData: {
          PlaybackPositionTicks: 500000,
          LastPlayedDate: '2026-10-14T16:00:00Z',
        } },
    ],
    TotalRecordCount: 2,
  };
  const { base } = await fixture(t, { nextUp: altered });
  const r = await fetch(base + '/Shows/NextUp', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), altered);
});

test('HTTP gateway preserves two episodes of the same IMDb spelling', async t => {
  const altered = {
    Items: [
      { ...mockRows[1], Id: 'episodeCanonA' },
      { ...mockRows[1], Id: 'episodeCanonB' },
    ],
    TotalRecordCount: 2,
  };
  const { base } = await fixture(t, { nextUp: altered });
  const r = await fetch(base + '/Shows/NextUp', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), altered);
});

test('different Jellyfin account and unauthenticated caller retain AIO response', async t => {
  const { base, hits } = await fixture(t, { expectedUser: 'different-user-99' });
  const a = await fetch(base + '/Shows/NextUp', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.deepEqual(await a.json(), reply);
  const n = hits.length;
  const b = await fetch(base + '/Shows/NextUp');
  assert.deepEqual(await b.json(), reply);
  assert.equal(hits.length, n + 1); // no /Users/Me or metadata
});

test('missing graph, failed series metadata and AIO 503 never hide watched cards', async t => {
  const scenarios = [
    { graph: false }, { metadataFails: true }, { upstreamError: true },
  ];
  for (const scenario of scenarios) {
    await t.test(JSON.stringify(scenario), async sub => {
      const { base } = await fixture(sub, scenario);
      const r = await fetch(base + '/Shows/NextUp', {
        headers: { 'X-Emby-Token': AIO_TOKEN },
      });
      if (scenario.upstreamError) {
        assert.equal(r.status, 503);
      } else {
        assert.equal(r.status, 200);
        assert.deepEqual(await r.json(), reply);
      }
    });
  }
});

test('series-specific and paging requests are upstream passthrough', async t => {
  const { base, hits } = await fixture(t);
  const a = await fetch(base + '/Shows/NextUp?SeriesId=seriesOld', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.deepEqual(await a.json(), reply);
  const b = await fetch(base + '/Shows/NextUp?StartIndex=25', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.deepEqual(await b.json(), reply);
  assert.equal(hits.filter(x => x.path === '/Users/Me').length, 0);
});

test('Resume routes remain 404 unless explicitly opted in', async t => {
  const { base, hits } = await fixture(t);
  for (const route of [
    '/UserItems/Resume?Limit=20&StartIndex=0',
    '/Users/' + USER + '/Items/Resume?Limit=20&StartIndex=20',
  ]) {
    const r = await fetch(base + route, {
      headers: { 'X-Emby-Token': AIO_TOKEN },
    });
    assert.equal(r.status, 404);
  }
  assert.equal(hits.length, 0);
});

test('opt-in Resume passthrough preserves modern/legacy, pages, movies and prefix totals', async t => {
  const { base, hits } = await fixture(t, {
    resumeEnabled: true, upstreamPrefix: '/jellyfin',
  });
  for (const index of [0, 20]) {
    const suffix = '?Limit=20&StartIndex=' + index + '&MediaTypes=Video';
    const modern = await fetch(base + '/UserItems/Resume' + suffix, {
      headers: { 'X-Emby-Token': AIO_TOKEN },
    });
    const legacy = await fetch(base + '/Users/' + USER + '/Items/Resume' + suffix, {
      headers: { 'X-Emby-Token': AIO_TOKEN },
    });
    assert.equal(modern.status, 200);
    assert.equal(legacy.status, 200);
    const a = await modern.json();
    const b = await legacy.json();
    assert.deepEqual(a, b);
    assert.equal(a.Items.length, 20);
    assert.equal(a.TotalRecordCount, index + 20);
    assert.equal(a.StartIndex, index);
    assert.ok(a.Items.some(x => x.Type === 'Movie'));
    assert.ok(a.Items.some(x => x.Type === 'Episode'));
    assert.ok(a.Items.every(x => x.UserData.PlaybackPositionTicks > 0));
    assert.equal(modern.headers.get('x-aio-resume-contract'), 'prefix-count');
  }
  assert.equal(hits.length, 4);
  assert.ok(hits.every(h => h.path.startsWith('/jellyfin/')));
  assert.ok(hits.every(h => h.auth === 'present'));
  assert.ok(hits.every(h => !h.path.includes('/Users/Me')
    && !h.path.includes('/Items/series')));
});

test('Resume propagates upstream authorization and service statuses untouched', async t => {
  const { base } = await fixture(t, { resumeEnabled: true });
  const unauthorized = await fetch(
    base + '/UserItems/Resume?Limit=20&StartIndex=0');
  assert.equal(unauthorized.status, 401);
  assert.deepEqual(await unauthorized.json(), { error: 'unauthorized' });
  const wrongVerb = await fetch(base + '/UserItems/Resume', {
    method: 'POST',
  });
  assert.equal(wrongVerb.status, 405);
  const unrelated = await fetch(base + '/Items/Resume', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(unrelated.status, 404);
});

test('Resume upstream 503 is forwarded, not converted into success or empty list', async t => {
  const { base } = await fixture(t, {
    resumeEnabled: true, resumeUpstreamStatus: 503,
  });
  const r = await fetch(base + '/UserItems/Resume?Limit=20&StartIndex=20', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(r.status, 503);
  assert.deepEqual(await r.json(), { error: 'upstream_status' });
});

test('other Jellyfin endpoints are not accidentally proxied', async t => {
  const { base, hits } = await fixture(t);
  const r = await fetch(base + '/Users/Me');
  assert.equal(r.status, 404);
  const u = await fetch(base + '/Shows/NextUp', { method: 'POST' });
  assert.equal(u.status, 405);
  assert.equal(u.headers.get('allow'), 'GET');
  const h = await fetch(base + '/health');
  assert.equal(h.status, 200);
  assert.equal((await h.json()).status, 'ok');
  assert.equal(hits.length, 0);
});

test('Gateway metrics are in-memory, aggregate-only, and not public HTTP routes', async t => {
  const { base, gateway } = await fixture(t, { resumeEnabled: true });
  assert.deepEqual(gateway.gatewayMetricsSnapshot(), {
    nextUpRequests: 0,
    resumePassthroughRequests: 0,
    nextUpNormalizedResponses: 0,
    nextUpUnchangedResponses: 0,
    nextUpRemovedItems: 0,
    failOpenNormalizationErrors: 0,
    upstreamTransportFailures: 0,
    upstreamNon200Responses: 0,
    responseFailures: 0,
    oversizedPassThroughResponses: 0,
    invalidJsonPassThroughResponses: 0,
    metadataActive: 0,
    metadataQueued: 0,
    metadataPeakActive: 0,
    metadataCacheEntries: 0,
  });
  const next = await fetch(base + '/Shows/NextUp?Limit=20', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(next.status, 200);
  assert.equal((await next.json()).Items.length, 1);
  const resume = await fetch(base + '/UserItems/Resume?Limit=20&StartIndex=20', {
    headers: { 'X-Emby-Token': AIO_TOKEN },
  });
  assert.equal(resume.status, 200);
  assert.equal((await resume.json()).TotalRecordCount, 40);
  const unauth = await fetch(base + '/UserItems/Resume?Limit=20');
  assert.equal(unauth.status, 401);
  const stats = gateway.gatewayMetricsSnapshot();
  assert.equal(stats.nextUpRequests, 1);
  assert.equal(stats.resumePassthroughRequests, 2);
  assert.equal(stats.nextUpNormalizedResponses, 1);
  assert.equal(stats.nextUpRemovedItems, 1);
  assert.equal(stats.upstreamNon200Responses, 1);
  assert.equal(stats.failOpenNormalizationErrors, 0);
  const raw = JSON.stringify(stats);
  for (const secret of [AIO_TOKEN, USER, 'tt44094505']) {
    assert.ok(!raw.includes(secret));
  }
  const endpoint = await fetch(base + '/gateway/metrics');
  assert.equal(endpoint.status, 404);
});

test('concurrent NextUp requests share only verified same-user metadata lookups', async t => {
  const { base, hits } = await fixture(t, { metadataDelayMs: 60 });
  const auth = { headers: { 'X-Emby-Token': AIO_TOKEN } };
  const replies = await Promise.all(Array.from({ length: 5 }, () =>
    fetch(base + '/Shows/NextUp?Limit=20', auth)
      .then(async r => ({ code: r.status, json: await r.json() }))
  ));
  for (const reply of replies) {
    assert.equal(reply.code, 200);
    assert.deepEqual(reply.json.Items, [mockRows[1]]);
  }
  // Each request independently proves the AIO user identity. ONLY series
  // metadata is coalesced; never share a completed Jellyfin auth session.
  assert.equal(hits.filter(x => x.path === '/Users/Me').length, 5);
  assert.equal(hits.filter(x => x.path === '/Items/seriesOld').length, 1);
  assert.equal(hits.filter(x => x.path === '/Items/seriesCanon').length, 1);
  assert.ok(hits.every(x => x.auth === 'present'));
});

test('single-flight metadata error fails open for each client without poisoning cache', async t => {
  const { base, hits } = await fixture(t, {
    metadataDelayMs: 45, metadataFails: true,
  });
  const auth = { headers: { 'X-Emby-Token': AIO_TOKEN } };
  const replies = await Promise.all(Array.from({ length: 3 }, () =>
    fetch(base + '/Shows/NextUp?Limit=20', auth).then(r => r.json())
  ));
  assert.ok(replies.every(x => JSON.stringify(x) === JSON.stringify(reply)));
  // No failed metadata lookup may hide NextUp progress.
  assert.ok(hits.filter(x => x.path.startsWith('/Items/series')).length >= 2);
});
