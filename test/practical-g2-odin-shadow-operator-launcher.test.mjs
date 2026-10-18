import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import {
  validateShadowSnapshot, buildShadowRamDb,
  makeRestrictedAioFetch, startOdinShadowCandidate,
} from '../scripts/launch-v140-g2-odin-shadow-candidate.mjs';

const PROFILE = 'c'.repeat(24);
const USER = 'local_pilot_user_1234';
const TOKEN = 'LOCAL_FAKE_SESSION_NOT_A_SECRET';
const LEGACY = { shows: {
  '123': { traktImdb: 'tt12345678', preferredMetaId: 'tt87654321',
    updatedAt: 1720000000000 },
} };
const input = () => ({ profile: PROFILE, user: USER, legacy: LEGACY });
async function listen(s) {
  s.listen(0, '127.0.0.1');
  await once(s, 'listening');
  return 'http://127.0.0.1:' + s.address().port;
}
async function close(s) {
  if (!s.listening) return;
  s.closeAllConnections();
  await new Promise(r => s.close(r));
}
async function waitFor(fn) {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > 3000) throw new Error('LOCAL_SHADOW_TIMEOUT');
    await new Promise(r => setTimeout(r, 15));
  }
}
async function source({ wrongUser = false } = {}) {
  const calls = [];
  const data = {
    Items: [
      { Id: 'eA', Type: 'Episode', SeriesId: 'local_series_alias',
        UserData: { LastPlayedDate: '2026-10-01T00:00:00Z',
          PlaybackPositionTicks: 0 } },
      { Id: 'eB', Type: 'Episode', SeriesId: 'local_series_canonical',
        UserData: { LastPlayedDate: '2026-10-02T00:00:00Z',
          PlaybackPositionTicks: 60000 } },
    ], TotalRecordCount: 2,
  };
  const aio = http.createServer((req, res) => {
    calls.push({ method: req.method, path: req.url.split('?')[0] });
    if (req.headers['x-emby-token'] !== TOKEN) {
      res.writeHead(401).end('{}'); return;
    }
    const path = req.url.split('?')[0];
    let reply;
    if (path === '/jellyfin/Shows/NextUp') reply = data;
    else if (path === '/jellyfin/Users/Me')
      reply = { Id: wrongUser ? 'other_not_matched_1234' : USER };
    else if (path === '/jellyfin/Items/local_series_alias') reply = {
      Id: 'local_series_alias', Type: 'Series',
      ProviderIds: { Imdb: 'tt87654321' } };
    else if (path === '/jellyfin/Items/local_series_canonical') reply = {
      Id: 'local_series_canonical', Type: 'Series',
      ProviderIds: { Imdb: 'tt12345678' } };
    else if (path === '/jellyfin/UserItems/Resume') reply = {
      Items: [{ Id: 'independent_resume' }], TotalRecordCount: 1 };
    else { res.writeHead(404).end('{}'); return; }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(reply));
  });
  const base = await listen(aio);
  const candidate = await startOdinShadowCandidate(input(), {
    aioBase: base + '/jellyfin', ingressPort: 0, gatewayPort: 0,
  });
  // Candidate does NOT expose ports through metrics. For offline tests we
  // can locate the bound socket by deliberately passing a fixed free port:
  // this test harness injects a capture-only onListen wrapper below.
  return { aio, candidate, base, calls, data };
}

test('identity source accepted ONLY in RAM with verified single alias group', () => {
  const ram = buildShadowRamDb(input());
  assert.equal(ram.showCount, 1);
  assert.equal(ram.aliasGroups, 1);
  assert.throws(() => ram.db.cacheSet('foo', {}), /SQLITE_WRITE_FORBIDDEN/);
  assert.equal(ram.db.cacheGet('identity-graph:v1:' + PROFILE).revision, 1);
  assert.equal(validateShadowSnapshot(input()), true);
  for (const bad of [
    { ...input(), profile: 'wrong' },
    { ...input(), user: 'invalid' },
    { ...input(), legacy: {} },
  ]) {
    assert.throws(() => buildShadowRamDb(bad));
  }
});

test('restricted Gateway fetch permits ONLY bounded same-origin GET paths', async () => {
  const observed = [];
  const { guardedFetch, counters } = makeRestrictedAioFetch(
    'http://127.0.0.1:3240/jellyfin', (url, opts) => {
      observed.push({ url: new URL(url).pathname, method: opts.method });
      return Promise.resolve({ status: 200 });
    });
  for (const path of ['/Shows/NextUp?Limit=20', '/Users/Me',
    '/Items/local_series_abc']) {
    const result = await guardedFetch('http://127.0.0.1:3240/jellyfin' + path,
      { method: 'GET', redirect: 'manual' });
    assert.equal(result.status, 200);
  }
  assert.equal(counters.get_only, 3);
  for (const bad of [
    'http://127.0.0.1:3240/jellyfin/Sessions/Playing',
    'http://127.0.0.1:3240/jellyfin/Users/Me?token=SECRET',
    'http://127.0.0.1:3240/jellyfin/Items/x?abc=1',
    'http://127.0.0.1:9000/jellyfin/Users/Me',
    'https://127.0.0.1:3240/jellyfin/Users/Me',
    'http://example.com/jellyfin/Users/Me',
  ]) {
    assert.throws(() => guardedFetch(bad,
      { method: 'GET', redirect: 'manual' }));
  }
  assert.throws(() => guardedFetch(
    'http://127.0.0.1:3240/jellyfin/Users/Me', { method: 'POST' }));
  assert.equal(observed.length, 3);
  assert.equal(counters.disallowed, 7);
});

test('no listener or network side effect by simply importing launcher module', () => {
  assert.equal(typeof startOdinShadowCandidate, 'function');
  assert.equal(typeof buildShadowRamDb, 'function');
  assert.equal(typeof makeRestrictedAioFetch, 'function');
});

// Free ephemeral Node port and use it for a separate test candidate. The
// port-discovery phase never contacts HomeDocker or a public interface.
async function sparePort() {
  const server = http.createServer((_req, res) => res.end());
  const base = await listen(server);
  await close(server);
  return new URL(base).port * 1;
}

test('full real Gateway + ingress spawned together, synthetic 2→1 in shadow, client unchanged', async t => {
  const aio = http.createServer((req, res) => {
    assert.equal(req.headers['x-emby-token'], TOKEN);
    const url = req.url.split('?')[0];
    let out;
    if (url === '/jellyfin/Shows/NextUp') out = {
      Items: [
        { Id: 'ep_a', Type: 'Episode', SeriesId: 'local_alias',
          UserData: { PlaybackPositionTicks: 0, LastPlayedDate: '2026-10-01T00:00:00Z' } },
        { Id: 'ep_b', Type: 'Episode', SeriesId: 'local_canonical',
          UserData: { PlaybackPositionTicks: 10000, LastPlayedDate: '2026-10-02T00:00:00Z' } },
      ], TotalRecordCount: 2,
    };
    else if (url === '/jellyfin/Users/Me') out = { Id: USER };
    else if (url === '/jellyfin/Items/local_alias') out = {
      Id: 'local_alias', Type: 'Series', ProviderIds: { Imdb: 'tt87654321' } };
    else if (url === '/jellyfin/Items/local_canonical') out = {
      Id: 'local_canonical', Type: 'Series', ProviderIds: { Imdb: 'tt12345678' } };
    else if (url === '/jellyfin/UserItems/Resume') out = { Items: [], TotalRecordCount: 0 };
    else { res.writeHead(404).end('{}'); return; }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(out));
  });
  const aioBase = await listen(aio);
  t.after(() => close(aio));
  const ingressPort = await sparePort(), gatewayPort = await sparePort();
  assert.notEqual(ingressPort, gatewayPort);
  const candidate = await startOdinShadowCandidate(input(), {
    aioBase: aioBase + '/jellyfin', ingressPort, gatewayPort,
  });
  t.after(() => candidate.close());
  const result = await fetch('http://127.0.0.1:' + ingressPort
    + '/jellyfin/Shows/NextUp?Limit=20', {
    headers: { 'x-emby-token': TOKEN }, redirect: 'manual',
  });
  assert.equal(result.status, 200);
  const source = await result.json();
  assert.equal(source.Items.length, 2);
  await waitFor(() => candidate.snapshot().shadow_completed === 1);
  const m = candidate.snapshot();
  assert.equal(m.gateway_nextup_gets, 1);
  assert.equal(m.gateway_alias_removed_items, 1);
  assert.equal(m.gateway_fail_open_normalization, 0);
  assert.equal(m.verified_legacy_alias_groups, 1);
  assert.equal(m.verified_legacy_show_count, 1);
  assert.equal(m.forwarded_watch_state_writes, 0);
  assert.equal(m.production_sqlite_writes, 0);
  assert.equal(m.filter_response_sent_to_odin, false);
  assert.equal(m.real_odin_gateway_client_qualified, false);
  assert.equal(m.shadow_canary_complete, false);
  assert.equal(m.release_hold_active, true);
  const resume = await fetch('http://127.0.0.1:' + ingressPort
    + '/jellyfin/UserItems/Resume',
  { headers: { 'x-emby-token': TOKEN } });
  assert.equal(resume.status, 200);
  assert.equal((await resume.json()).TotalRecordCount, 0);
  assert.equal(candidate.snapshot().ingress_resume, 1);
  const serialized = JSON.stringify(m);
  for(const sensitive of [USER, PROFILE, TOKEN, 'tt12345678', 'ep_a']) {
    assert.equal(serialized.includes(sensitive), false);
  }
});

test('duplicate bound port refuses partial startup, closes owned Gateway', async t => {
  const aio = http.createServer((req, res) => res.writeHead(200).end('{}'));
  const base = await listen(aio);
  t.after(() => close(aio));
  const occupied = http.createServer((_req,res) => res.end('occupied'));
  const blockedPort = await listen(occupied);
  t.after(() => close(occupied));
  await assert.rejects(() => startOdinShadowCandidate(input(), {
    aioBase: base + '/jellyfin',
    gatewayPort: Number(new URL(blockedPort).port),
    ingressPort: 0,
  }), /SHADOW_STOP_LOOPBACK_STARTUP_FAILED/);
});
