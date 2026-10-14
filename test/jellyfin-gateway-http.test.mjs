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
} = {}) {
  const hits = [];
  const upstream = await serve((req, res) => {
    hits.push({
      path: req.url, auth: req.headers['x-emby-token'] ? 'present' : 'absent',
    });
    if (req.url?.startsWith('/Shows/NextUp')) {
      if (upstreamError) return response(res, 503, { error: 'aio_down' });
      return response(res, 200, nextUp);
    }
    if (req.url === '/Users/Me') {
      if (req.headers['x-emby-token'] !== AIO_TOKEN) {
        return response(res, 401, { Message: 'Unauthorized' });
      }
      return response(res, 200, { Id: USER });
    }
    const match = req.url?.match(/^\/Items\/(seriesCanon|seriesOld)$/);
    if (match) {
      if (metadataFails) return response(res, 500, { error: 'metadata_down' });
      return response(res, 200, {
        Type: 'Series', Id: match[1],
        ProviderIds: { Imdb: match[1] === 'seriesOld' ? 'tt44094505' : 'tt44051354' },
      });
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
    jellyfinGatewayUpstreamUrl: upstream.url,
    jellyfinGatewayProfileId: PROFILE,
    jellyfinGatewayUserId: expectedUser,
  };
  const server = createJellyfinGateway({ config, db });
  t.after(() => server.close());
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = 'http://127.0.0.1:' + server.address().port;
  return { base, hits, db };
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

test('new playback with old alias uses same verified group and wins on recency', async t => {
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
  const result = await r.json();
  assert.equal(result.Items.length, 1);
  assert.equal(result.Items[0].Id, 'episodeNewAlias');
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
