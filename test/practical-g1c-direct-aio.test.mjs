import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { runDirectAioCandidate } from '../scripts/probe-v140-g1c-direct-aio.mjs';

const PROFILE = 'a'.repeat(24);
const USER = 'g1c_user_realshape123';
const TOKEN = 'synthetic-G1C-token-only-123456789';
function payload() {
  return {
    user: USER, profile: PROFILE, token: TOKEN,
    legacy: { shows: {
      '263102': {
        traktImdb: 'tt44051354', preferredMetaId: 'tt44094505',
        updatedAt: 1720000000000,
      },
      '1': {
        traktImdb: 'tt12345678', preferredMetaId: 'tt12345678',
        updatedAt: 1720000000000,
      },
    }},
  };
}
async function fakeAio(t, { user = USER, duo = false, drift = false } = {}) {
  const calls = [];
  let nextCount = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const route = url.pathname;
    calls.push({ route, method: req.method, token: req.headers['x-emby-token'] });
    const ok = req.method === 'GET' && req.headers['x-emby-token'] === TOKEN;
    if (!ok) { res.writeHead(401).end('{}'); return; }
    const records = duo ? [
      { Id: 'e_a', SeriesId: 'series_alias', Type: 'Episode', UserData: {
        PlaybackPositionTicks: 0, LastPlayedDate: '2026-10-01T10:00:00Z' }},
      { Id: 'e_b', SeriesId: 'series_canon', Type: 'Episode', UserData: {
        PlaybackPositionTicks: 10000, LastPlayedDate: '2026-10-02T10:00:00Z' }},
    ] : [
      { Id: 'e_a', SeriesId: 'series_alias', Type: 'Episode',
        UserData: { PlaybackPositionTicks: 1000 }},
      { Id: 'e_b', SeriesId: 'series_other', Type: 'Episode',
        UserData: { PlaybackPositionTicks: 0 }},
    ];
    let result;
    if (route === '/jellyfin/Users/Me') result = { Id: user };
    else if (route === '/jellyfin/Shows/NextUp') {
      nextCount++;
      result = { Items: drift && nextCount > 1 ? records.slice(1) : records,
        TotalRecordCount: drift && nextCount > 1 ? 1 : 2,
        OtherOpaque: 'UNCHANGED_FROM_AIO' };
    } else if (route.startsWith('/jellyfin/Items/')) {
      const id = route.split('/').pop();
      result = { Id: id, Type: 'Series', ProviderIds: {
        Imdb: ({
          series_alias: 'tt44094505',
          series_canon: 'tt44051354',
          series_other: 'tt12345678',
        })[id] || null,
      }};
    } else { res.writeHead(404).end('{}'); return; }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(result));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  return { base: 'http://127.0.0.1:' + server.address().port + '/jellyfin',
    calls };
}

test('G1C exact same user + real network Gateway GET preserves nondeduplicated NextUp', async t => {
  const a = await fakeAio(t);
  const result = await runDirectAioCandidate(payload(), { upstreamBase: a.base });
  assert.equal(result.status, 'DIRECT_AIO_GATEWAY_GET_PASS');
  assert.equal(result.real_nextup_source_items, 2);
  assert.equal(result.gateway_preserved_real_nextup, true);
  assert.equal(result.series_metadata_same_session_gets, 2);
  assert.equal(result.explicit_user_to_profile_technical_configuration, true);
  assert.equal(result.independent_user_to_profile_ownership_proven, false);
  assert.equal(result.production_sqlite_writes, 0);
  assert.deepEqual(result.aio_http_methods, ['GET']);
  assert.ok(a.calls.every(x => x.method === 'GET' && x.token === TOKEN));
  assert.ok(a.calls.some(x => x.route === '/jellyfin/Users/Me'));
  assert.ok(a.calls.some(x => x.route === '/jellyfin/Shows/NextUp'));
  assert.ok(a.calls.some(x => x.route === '/jellyfin/Items/series_alias'));
  const json = JSON.stringify(result);
  for (const forbidden of [TOKEN, USER, PROFILE, 'tt44094505', 'e_a']) {
    assert.ok(!json.includes(forbidden));
  }
});

test('G1C direct candidate rejects a different upstream user', async t => {
  const a = await fakeAio(t, { user: 'other_aio_user_1234' });
  await assert.rejects(
    runDirectAioCandidate(payload(), { upstreamBase: a.base }),
    /AIO_USER_ID_MISMATCH/,
  );
  assert.ok(a.calls.every(x => x.method === 'GET'));
});

test('G1C refuses to call a real alias removal in an unexpected live sample a PASS', async t => {
  const a = await fakeAio(t, { duo: true });
  await assert.rejects(
    runDirectAioCandidate(payload(), { upstreamBase: a.base }),
    /LIVE_NEXTUP_CHANGED_REVIEW_SOURCE_BEFORE_ANY_FILTER/,
  );
  assert.ok(a.calls.every(x => x.method === 'GET'));
});

test('G1C refuses stale/changing source NextUp snapshot', async t => {
  const a = await fakeAio(t, { drift: true });
  await assert.rejects(
    runDirectAioCandidate(payload(), { upstreamBase: a.base }),
    /AIO_SOURCE_CHANGED_DURING_PROBE|LIVE_NEXTUP_CHANGED_REVIEW_SOURCE_BEFORE_ANY_FILTER/,
  );
  assert.ok(a.calls.every(x => x.method === 'GET'));
});

test('G1C rejects malformed source identifiers before any API call', async () => {
  for (const broken of [{ ...payload(), profile: 'wrong' },
    { ...payload(), user: 'x' }, { ...payload(), token: 'tiny' }]) {
    await assert.rejects(runDirectAioCandidate(broken),
      /input|Expected|match|Assertion|String/);
  }
});
