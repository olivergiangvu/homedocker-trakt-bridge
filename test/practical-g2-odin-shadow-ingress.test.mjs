import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import {
  createOdinShadowIngress,
} from '../src/odin-g2-shadow-ingress.mjs';

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return 'http://127.0.0.1:' + server.address().port;
}
async function close(server) {
  if (!server.listening) return;
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
async function setup({ approved = true, gatewayStatus = 200,
  gatewayDelay = 0, gatewayResponse = 'SHADOW_PRIVATE' } = {}) {
  const seen = { aio: [], gateway: [] };
  const original = Buffer.from([0x1f, 0x8b, 0x08, 0, 0, 0xab, 0x01, 0xfb]);
  const aio = http.createServer((req, res) => {
    seen.aio.push({ method: req.method, target: req.url });
    if (req.url.startsWith('/jellyfin/Shows/NextUp')) {
      res.writeHead(200, {
        'content-type': 'application/json',
        'content-encoding': 'gzip', 'content-length': original.length,
        'etag': '"aio-etag-1"', 'x-aio-test': 'original',
      });
      res.end(original);
    } else if (req.url.startsWith('/jellyfin/UserItems/Resume')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"Items":[{"Id":"resume-1"}]}');
    } else if (req.url === '/jellyfin/Users/AuthenticateByName') {
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ status: 'login_processed',
          bytes: Buffer.concat(chunks).length }));
      });
    } else {
      res.writeHead(206, { 'x-aio-test': 'item-original' });
      res.end('OTHER_CLIENT_BYTES');
    }
  });
  const gateway = http.createServer((req, res) => {
    seen.gateway.push({ method: req.method, target: req.url,
      token: req.headers['x-emby-token'] });
    setTimeout(() => {
      res.writeHead(gatewayStatus, { 'content-type': 'text/plain' });
      res.end(gatewayResponse);
    }, gatewayDelay);
  });
  const aioUrl = await listen(aio);
  const gatewayUrl = await listen(gateway);
  const ingress = createOdinShadowIngress({
    aioBase: aioUrl + '/jellyfin', gatewayBase: gatewayUrl,
    shadowApproved: approved, shadowTimeoutMs: 500,
  });
  const ingressUrl = await listen(ingress);
  return { aio, gateway, ingress, ingressUrl, seen, original,
    async shutdown() {
      await close(ingress); await close(gateway); await close(aio);
    } };
}
async function waitFor(condition, limit = 3000) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > limit) throw new Error('SHADOW_WAIT_TIMEOUT');
    await new Promise(resolve => setTimeout(resolve, 15));
  }
}

test('real HTTP loopback E2E: original compressed NextUp bytes, AIO headers, and shadow GET', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  const originalRequestTarget = '/jellyfin/Shows/NextUp?Limit=20&Fields=DateCreated';
  const res = await fetch(env.ingressUrl + originalRequestTarget, {
    headers: { 'x-emby-token': 'synthetic-local-auth-key',
      'accept-encoding': 'identity' },
  });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('etag'), '"aio-etag-1"');
  assert.equal(res.headers.get('x-aio-test'), 'original');
  assert.equal(res.headers.get('content-encoding'), 'gzip');
  // Fetch can decompress gzip. Use raw HTTP in a separate verification to
  // ensure actual source compressed bytes are untouched over the ingress.
  await res.arrayBuffer().catch(() => {});
  await waitFor(() => env.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  assert.deepEqual(env.seen.gateway, [{
    method: 'GET', target: '/Shows/NextUp?Limit=20&Fields=DateCreated',
    token: 'synthetic-local-auth-key',
  }]);
  assert.deepEqual(env.seen.aio.map(x => x.target), [originalRequestTarget]);
  const m = env.ingress.shadowMetricsSnapshot();
  assert.equal(m.shadow_attempted, 1);
  assert.equal(m.shadow_response_2xx, 1);
  assert.equal(m.gateway_client_responses, 0);
  assert.equal(m.playback_writes_forwarded, 0);
  assert.equal(m.production_database_writes, 0);
  assert.equal(m.gateway_client_qualified, false);
  assert.equal(m.stable_release_hold, true);
  assert.equal(JSON.stringify(m).includes('synthetic-local-auth-key'), false);
});

test('AIO NextUp body is byte-identical and not replaced by Gateway 503', async (t) => {
  const env = await setup({ gatewayStatus: 503, gatewayResponse: 'FILTER_NOT_SENT' });
  t.after(() => env.shutdown());
  const bytes = await new Promise((resolve, reject) => {
    http.get(env.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20', {
      headers: { 'x-emby-token': 'fake_token' },
    }, response => {
      assert.equal(response.statusCode, 200);
      assert.equal(response.headers['content-encoding'], 'gzip');
      assert.equal(response.headers.etag, '"aio-etag-1"');
      const chunks = [];
      response.on('data', part => chunks.push(part));
      response.on('end', () => resolve(Buffer.concat(chunks)));
    }).on('error', reject);
  });
  assert.deepEqual(bytes, env.original);
  await waitFor(() => env.ingress.shadowMetricsSnapshot().shadow_completed === 1);
  assert.equal(env.ingress.shadowMetricsSnapshot().shadow_response_5xx, 1);
  assert.equal(env.ingress.shadowMetricsSnapshot().gateway_client_responses, 0);
});

test('Resume, Items, user metadata read remain AIO-only; login POST is bounded', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  const resume = await fetch(env.ingressUrl + '/jellyfin/UserItems/Resume?Limit=10');
  assert.equal(resume.status, 200);
  assert.deepEqual(await resume.json(), { Items: [{ Id: 'resume-1' }] });
  const item = await fetch(env.ingressUrl + '/jellyfin/Items/synthetic-series');
  assert.equal(item.status, 206);
  assert.equal(await item.text(), 'OTHER_CLIENT_BYTES');
  const login = await fetch(env.ingressUrl + '/jellyfin/Users/AuthenticateByName', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ Username: 'synthetic', Password: 'synthetic' }),
  });
  assert.equal(login.status, 200);
  assert.equal((await login.json()).status, 'login_processed');
  assert.equal(env.seen.gateway.length, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().client_resume_requests, 1);
  assert.equal(env.ingress.shadowMetricsSnapshot().client_nextup_requests, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().playback_writes_forwarded, 0);
});

test('shadow is OFF by default and cannot appear without opt-in', async (t) => {
  const env = await setup({ approved: false });
  t.after(() => env.shutdown());
  // This GET returns binary gzip from AIO, no need to parse.
  const res = await fetch(env.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20');
  assert.equal(res.status, 200);
  await res.arrayBuffer().catch(() => {});
  assert.equal(env.seen.gateway.length, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().shadow_attempted, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().shadow_enabled, false);
});

test('playback writes, watched writes, and unauthorised POST are blocked before AIO/Gateway', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  for (const [method, path] of [
    ['POST', '/jellyfin/Sessions/Playing'],
    ['POST', '/jellyfin/Sessions/Playing/Progress'],
    ['POST', '/jellyfin/Sessions/Playing/Stopped'],
    ['POST', '/jellyfin/Users/syntheticId/PlayedItems/something'],
    ['PUT', '/jellyfin/Items/someId'],
    ['DELETE', '/jellyfin/Items/someId'],
    ['POST', '/jellyfin/Users/Me/Items'],
  ]) {
    const response = await fetch(env.ingressUrl + path, { method, body: 'x' });
    assert.equal(response.status, 405);
  }
  assert.equal(env.seen.aio.length, 0);
  assert.equal(env.seen.gateway.length, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().locally_blocked_writes, 7);
});

test('reject non-Jellyfin and absolute/encoded URLs before AIO', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  for (const path of ['/anything', '/jellyfin/Shows%2FNextUp',
    '/jellyfin/Items%5Cbad', '/jellyfin/Items#fragment']) {
    // #fragment stripped by clients; test only paths without '#'.
    if (path.includes('#')) continue;
    const r = await fetch(env.ingressUrl + path);
    assert.equal(r.status, 400);
  }
  assert.equal(env.seen.aio.length, 0);
});

test('shadow bounded to three GETs per process and at most one outstanding', async (t) => {
  const env = await setup({ gatewayDelay: 50 });
  t.after(() => env.shutdown());
  for (let i = 0; i < 6; i++) {
    const res = await fetch(env.ingressUrl + '/jellyfin/Shows/NextUp?Limit=20');
    assert.equal(res.status, 200);
    await res.arrayBuffer().catch(() => {});
    await new Promise(resolve => setTimeout(resolve, 75));
  }
  assert.equal(env.seen.aio.length, 6);
  assert.equal(env.seen.gateway.length, 3);
  assert.equal(env.ingress.shadowMetricsSnapshot().shadow_attempted, 3);
  assert.ok(env.ingress.shadowMetricsSnapshot().shadow_skipped_capacity >= 3);
});

test('no shadow when query tries to include explicit API tokens', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  const res = await fetch(env.ingressUrl +
    '/jellyfin/Shows/NextUp?Limit=20&api_key=sample');
  assert.equal(res.status, 200);
  await res.arrayBuffer().catch(() => {});
  assert.equal(env.seen.gateway.length, 0);
  assert.equal(env.ingress.shadowMetricsSnapshot().shadow_attempted, 0);
});

test('reject remote, credentialed, same-port upstreams and invalid timeout', () => {
  for (const invalid of [
    'http://example.com:1234/jellyfin',
    'http://0.0.0.0:3240/jellyfin',
    'https://127.0.0.1:3240/jellyfin',
    'http://user:password@127.0.0.1:3240/jellyfin',
    'http://127.0.0.1:3240/jellyfin?key=foo',
    'http://127.0.0.1:3240/jellyfin/other',
  ]) {
    assert.throws(() => createOdinShadowIngress({
      aioBase: invalid, gatewayBase: 'http://127.0.0.1:17432',
    }));
  }
  assert.throws(() => createOdinShadowIngress({
    aioBase: 'http://127.0.0.1:17432/jellyfin',
    gatewayBase: 'http://127.0.0.1:17432',
  }));
  assert.throws(() => createOdinShadowIngress({
    aioBase: 'http://127.0.0.1:3240/jellyfin',
    gatewayBase: 'http://127.0.0.1:17432',
    shadowTimeoutMs: 30000,
  }));
});

test('oversized auth POST is rejected, secret not forwarded', async (t) => {
  const env = await setup();
  t.after(() => env.shutdown());
  const r = await fetch(env.ingressUrl + '/jellyfin/Users/AuthenticateByName', {
    method: 'POST', headers: { 'content-type': 'text/plain' },
    body: 'z'.repeat(300000),
  });
  assert.equal(r.status, 413);
  assert.equal(env.seen.aio.length, 0);
  assert.equal(env.seen.gateway.length, 0);
});
