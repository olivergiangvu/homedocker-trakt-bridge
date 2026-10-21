import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import {
  attachWatchRefreshRelay, readImportMarker, refreshFrame,
} from '../src/watch-refresh-relay.mjs';

const USER = 'a'.repeat(32);
const PERSONA = '';
const SINK = 'bridge-sink';
const TOKEN = 'synthetic-session-token';
const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function createFixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'watch-refresh-test-'));
  const dbPath = join(dir, 'aio.db');
  const db = new DatabaseSync(dbPath);
  db.exec(`
    CREATE TABLE watch_sinks (
      id TEXT, uuid TEXT, persona TEXT,
      addon_name TEXT, addon_instance_id TEXT, status TEXT, retired_at INTEGER
    );
    CREATE TABLE watch_state (
      uuid TEXT, persona TEXT, sink_id TEXT, origin TEXT,
      updated_at INTEGER, played INTEGER
    );
  `);
  db.prepare(`INSERT INTO watch_sinks VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .run(SINK, USER, PERSONA, 'homedocker-trakt-bridge', 'e3fe3b0', 'connected', null);
  db.prepare(`INSERT INTO watch_state VALUES (?, ?, ?, ?, ?, ?)`)
    .run(USER, PERSONA, SINK, 'import', 100, 0);
  t.after(() => { db.close(); rmSync(dir, { recursive: true, force: true }); });
  return { db, dbPath };
}

function listen(server) {
  server.listen(0, '127.0.0.1');
  return once(server, 'listening').then(
    () => 'http://127.0.0.1:' + server.address().port);
}

function makeWsUpstream(t) {
  const active = new Set();
  let upgrades = 0;
  const server = http.createServer((req, res) => {
    if (req.url !== '/jellyfin/Users/Me') {
      res.writeHead(404).end();
      return;
    }
    const ok = req.headers['x-emby-token'] === TOKEN;
    res.writeHead(ok ? 200 : 401, { 'Content-Type': 'application/json' })
      .end(JSON.stringify(ok ? { Id: USER } : {}));
  });
  server.on('upgrade', (req, socket) => {
    upgrades++;
    if (!req.url?.startsWith('/jellyfin/socket?api_key=')
        || req.headers.upgrade !== 'websocket') {
      socket.destroy();
      return;
    }
    const key = String(req.headers['sec-websocket-key'] || '');
    const accept = createHash('sha1').update(key + GUID).digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\n'
      + 'Upgrade: websocket\r\nConnection: Upgrade\r\n'
      + 'Sec-WebSocket-Accept: ' + accept + '\r\n\r\n');
    active.add(socket);
    socket.on('close', () => active.delete(socket));
  });
  t.after(() => { for (const socket of active) socket.destroy(); server.close(); });
  return { server, upgrades: () => upgrades };
}

function waitForMessage(socket, maxMs = 3500) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      socket.removeEventListener('message', onMessage);
      reject(new Error('timed out waiting for refresh'));
    }, maxMs);
    const onMessage = event => {
      clearTimeout(timeout);
      socket.removeEventListener('message', onMessage);
      resolve(JSON.parse(event.data));
    };
    socket.addEventListener('message', onMessage);
  });
}

test('read-only marker identifies one exact mapped Bridge sink, never another user', t => {
  const { db, dbPath } = createFixture(t);
  assert.equal(readImportMarker(dbPath, USER, 'homedocker-trakt-bridge', 'e3fe3b0'), '100:1');
  assert.equal(readImportMarker(dbPath, 'b'.repeat(32), 'homedocker-trakt-bridge'), null);
  db.prepare('INSERT INTO watch_sinks VALUES (?, ?, ?, ?, ?, ?, ?)')
    .run('other', 'b'.repeat(32), '', 'homedocker-trakt-bridge', 'otherid', 'connected', null);
  assert.equal(readImportMarker(dbPath, USER, 'homedocker-trakt-bridge'), null);
  assert.equal(readImportMarker(dbPath, USER, 'homedocker-trakt-bridge', 'e3fe3b0'), '100:1');
});

test('notification is a server-side, unmasked WebSocket text frame with no history', () => {
  const bytes = refreshFrame(USER);
  assert.equal(bytes[0], 0x81);
  assert.equal(bytes[1] & 0x80, 0);
  assert.deepEqual(JSON.parse(bytes.subarray(2).toString()), {
    MessageType: 'UserDataChanged',
    Data: { UserId: USER, UserDataList: [] },
  });
});

test('relay disabled by default: no handler, no timer and no REST changes', t => {
  const gateway = http.createServer((_req, res) => res.writeHead(404).end());
  t.after(() => gateway.close());
  assert.equal(attachWatchRefreshRelay({
    gateway, config: { jellyfinGatewayWatchRefreshRelay: false },
    userId: USER, upstreamBase: 'http://127.0.0.1:9999/jellyfin',
  }), null);
  assert.equal(gateway.listenerCount('upgrade'), 0);
});

test('beta exact-socket proxy forwards AIO websocket, notifies only after Bridge import', async t => {
  const { db, dbPath } = createFixture(t);
  const upstream = makeWsUpstream(t);
  const base = await listen(upstream.server);
  const gateway = http.createServer((_req, res) => res.writeHead(404).end());
  t.after(() => gateway.close());
  const relay = attachWatchRefreshRelay({
    gateway,
    config: {
      jellyfinGatewayWatchRefreshRelay: true,
      jellyfinGatewaySourceKind: 'aiostreams',
      aioDbPath: dbPath,
      aioReconcileSinkName: 'homedocker-trakt-bridge',
      aioReconcileSinkInstanceId: 'e3fe3b0',
    },
    userId: USER,
    upstreamBase: base + '/jellyfin',
    pollMs: 1000,
  });
  const gatewayBase = await listen(gateway);
  const ws = new WebSocket(gatewayBase.replace('http:', 'ws:') + '/socket?api_key=' + TOKEN);
  t.after(() => ws.close());
  await once(ws, 'open');
  assert.equal(upstream.upgrades(), 1);
  assert.equal(relay.snapshot().clients, 1);
  // A normal AIO local event is not a Bridge import; it must not signal.
  db.prepare('INSERT INTO watch_state VALUES (?, ?, ?, ?, ?, ?)')
    .run(USER, '', null, 'local', 110, 1);
  const event = waitForMessage(ws);
  db.prepare('INSERT INTO watch_state VALUES (?, ?, ?, ?, ?, ?)')
    .run(USER, '', SINK, 'import', 200, 1);
  const received = await event;
  assert.equal(received.MessageType, 'UserDataChanged');
  assert.equal(received.Data.UserId, USER);
  assert.deepEqual(received.Data.UserDataList, []);
  assert.equal(relay.snapshot().notices, 1);
  relay.stop();
});

test('wrong user or token cannot connect through proxy', async t => {
  const { dbPath } = createFixture(t);
  const upstream = makeWsUpstream(t);
  const base = await listen(upstream.server);
  const gateway = http.createServer((_req, res) => res.writeHead(404).end());
  t.after(() => gateway.close());
  attachWatchRefreshRelay({
    gateway,
    config: {
      jellyfinGatewayWatchRefreshRelay: true,
      jellyfinGatewaySourceKind: 'aiostreams',
      aioDbPath: dbPath,
    },
    userId: 'b'.repeat(32),
    upstreamBase: base + '/jellyfin',
  });
  const gatewayBase = await listen(gateway);
  const ws = new WebSocket(gatewayBase.replace('http:', 'ws:') + '/socket?api_key=' + TOKEN);
  t.after(() => ws.close());
  await assert.rejects(once(ws, 'open'), /unexpected server response|HTTP|error|Unexpected/);
  assert.equal(upstream.upgrades(), 0);
});
