/**
 * Opt-in AIOStreams Web/Desktop watch-state refresh relay.
 *
 * Exact /socket WebSocket passthrough; does not proxy REST, touch AIO SQLite,
 * write Trakt, or patch upstream AIOStreams.  It sends only an empty
 * UserDataChanged notification after a successful external Bridge import.
 *
 * AIOStreams' own client invalidates its watch-state queries on this message.
 * AIO's pull scheduler is configured separately; this relay never pulls Trakt.
 */
import { createHash } from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import { DatabaseSync } from 'node:sqlite';

const MAX_SOCKETS = 16;
const SOCKET_PATH = '/socket';
const DEFAULT_POLL_MS = 5000;
const MAX_FRAME_BYTES = 8192;

function sameId(a, b) {
  return String(a ?? '').replaceAll('-', '').toLowerCase()
    === String(b ?? '').replaceAll('-', '').toLowerCase();
}

function userIdOf(uuid, persona) {
  if (!persona) return uuid.replaceAll('-', '').toLowerCase();
  return createHash('sha256')
    .update(`jellyfin-persona:${uuid}:${persona}`).digest('hex').slice(0, 32);
}

/** A single read-only aggregation for the mapped user's connected Bridge sink. */
export function readImportMarker(dbPath, userId, sinkName, sinkInstanceId = '') {
  const db = new DatabaseSync(dbPath, { readOnly: true, timeout: 1000 });
  try {
    db.exec('PRAGMA query_only=ON');
    const sql = `SELECT id, uuid, persona FROM watch_sinks
      WHERE addon_name = ? AND retired_at IS NULL AND status = 'connected'`
      + (sinkInstanceId ? ' AND addon_instance_id = ?' : '');
    const matches = db.prepare(sql).all(...(sinkInstanceId
      ? [sinkName, sinkInstanceId] : [sinkName]));
    if (matches.length !== 1) return null; // fail closed on missing/ambiguous sink
    const sink = matches[0];
    if (!sameId(userIdOf(sink.uuid, sink.persona), userId)) return null;
    const marker = db.prepare(`
      SELECT MAX(updated_at) AS newest, COUNT(*) AS entries
      FROM watch_state
      WHERE uuid = ? AND persona = ? AND sink_id = ? AND origin = 'import'
    `).get(sink.uuid, sink.persona, sink.id);
    // An empty imported history is not proof of a new incoming state.
    if (!marker?.entries) return null;
    return `${marker.newest ?? 0}:${marker.entries}`;
  } finally {
    db.close();
  }
}

export function refreshFrame(userId) {
  const payload = Buffer.from(JSON.stringify({
    MessageType: 'UserDataChanged',
    Data: { UserId: userId, UserDataList: [] },
  }));
  if (payload.length > MAX_FRAME_BYTES) throw new Error('notification_too_large');
  const head = payload.length < 126
    ? Buffer.from([0x81, payload.length])
    : Buffer.from([0x81, 126, (payload.length >>> 8) & 255, payload.length & 255]);
  return Buffer.concat([head, payload]);
}

function closeHandshake(socket, status = 403) {
  if (socket.destroyed) return;
  socket.end(`HTTP/1.1 ${status} Rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
}

function tokenFromRequest(req) {
  try {
    const u = new URL(req.url || '/', 'http://local.invalid');
    if (u.pathname !== SOCKET_PATH) return null;
    if (u.searchParams.getAll('api_key').length !== 1) return null;
    return u.searchParams.get('api_key') || null;
  } catch {
    return null;
  }
}

/**
 * Only a valid AIO session for the configured Gateway user may reach a socket.
 * No token, path, profile, or item IDs are logged or persisted.
 */
async function verifyAioSession(base, token, userId, fetchImpl) {
  const answer = await fetchImpl(base + '/Users/Me', {
    method: 'GET',
    headers: { 'X-Emby-Token': token, Accept: 'application/json' },
    redirect: 'manual',
    signal: AbortSignal.timeout(5000),
  });
  if (answer.status !== 200) return false;
  const body = await answer.json();
  return sameId(body?.Id, userId);
}

function proxySocket(req, client, head, base, onReady, onClose) {
  const url = new URL(base + SOCKET_PATH + (req.url?.slice(SOCKET_PATH.length) || ''));
  const transport = url.protocol === 'https:' ? https : http;
  // Keep WebSocket handshake headers, not the inbound Host or forwarded headers.
  const headers = Object.fromEntries(Object.entries(req.headers)
    .filter(([name]) => name !== 'host' && !name.startsWith('x-forwarded-')));
  headers.host = url.host;
  headers.connection = 'Upgrade';
  headers.upgrade = 'websocket';
  let ready = false;
  const upstreamReq = transport.request(url, {
    method: 'GET', headers, timeout: 8000, agent: false,
  });
  upstreamReq.on('upgrade', (response, upstream, upstreamHead) => {
    if (response.statusCode !== 101 || !response.headers['sec-websocket-accept']) {
      upstream.destroy();
      return closeHandshake(client, 502);
    }
    const allowed = ['upgrade', 'connection', 'sec-websocket-accept',
      'sec-websocket-protocol', 'sec-websocket-extensions'];
    const lines = ['HTTP/1.1 101 Switching Protocols'];
    for (const name of allowed) {
      const value = response.headers[name];
      if (typeof value === 'string') lines.push(`${name}: ${value}`);
    }
    client.write(lines.join('\r\n') + '\r\n\r\n');
    // Bytes buffered by node on either side belong to the raw WS stream.
    if (head?.length) upstream.write(head);
    if (upstreamHead?.length) client.write(upstreamHead);
    ready = true;
    onReady(client);
    client.pipe(upstream);
    upstream.pipe(client);
    const done = () => {
      upstream.destroy();
      client.destroy();
      onClose(client);
    };
    client.once('close', done);
    upstream.once('close', done);
    client.once('error', done);
    upstream.once('error', done);
  });
  upstreamReq.on('response', (response) => {
    response.resume();
    closeHandshake(client, 502);
  });
  upstreamReq.on('timeout', () => upstreamReq.destroy());
  upstreamReq.on('error', () => {
    if (!ready) closeHandshake(client, 502);
    else client.destroy();
  });
  client.once('close', () => upstreamReq.destroy());
  upstreamReq.end();
}

/**
 * Attach to the already existing v1.4 Gateway's HTTP server.
 * Disabled unless explicitly enabled, so the NextUp path is unchanged.
 * The timer does not run without connected Web/Desktop sockets.
 */
export function attachWatchRefreshRelay({
  gateway, config, userId, upstreamBase,
  fetchImpl = fetch, pollMs = DEFAULT_POLL_MS,
}) {
  if (config.jellyfinGatewayWatchRefreshRelay !== true) return null;
  if (config.jellyfinGatewaySourceKind !== 'aiostreams') {
    throw new Error('Watch refresh relay requires AIOStreams source');
  }
  if (!Number.isSafeInteger(pollMs) || pollMs < 1000) {
    throw new Error('Invalid watch refresh poll interval');
  }
  const clients = new Set();
  const stats = { notices: 0, readings: 0, readErrors: 0, rejected: 0 };
  let timer = null;
  let baseline = null;

  const sample = () => {
    try {
      const marker = readImportMarker(
        config.aioDbPath, userId,
        config.aioReconcileSinkName || 'homedocker-trakt-bridge',
        config.aioReconcileSinkInstanceId || '',
      );
      stats.readings++;
      if (marker == null) { baseline = null; return; }
      if (baseline !== null && marker !== baseline) {
        const frame = refreshFrame(userId);
        for (const socket of clients) {
          if (!socket.destroyed && socket.writable) {
            socket.write(frame);
            stats.notices++;
          }
        }
      }
      baseline = marker;
    } catch {
      // AIO schema/volume may be unavailable during upgrades. Never emit a
      // guessed notification or interrupt upstream WS; retry read-only.
      stats.readErrors++;
      baseline = null;
    }
  };

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
    baseline = null;
  }

  function onReady(socket) {
    clients.add(socket);
    if (!timer) {
      sample(); // establish baseline; NEVER announce pre-existing history
      timer = setInterval(sample, pollMs);
      timer.unref?.();
    }
  }
  function onClose(socket) {
    clients.delete(socket);
    if (clients.size === 0) stopTimer();
  }

  const upgrade = (req, socket, head) => {
    if (req.method !== 'GET' || !tokenFromRequest(req)
        || clients.size >= MAX_SOCKETS
        || String(req.headers.upgrade || '').toLowerCase() !== 'websocket') {
      stats.rejected++;
      return closeHandshake(socket, 403);
    }
    const token = tokenFromRequest(req);
    void verifyAioSession(upstreamBase, token, userId, fetchImpl)
      .then(ok => {
        if (!ok) {
          stats.rejected++;
          return closeHandshake(socket, 403);
        }
        if (!socket.destroyed) proxySocket(req, socket, head, upstreamBase, onReady, onClose);
      })
      .catch(() => {
        stats.rejected++;
        closeHandshake(socket, 403);
      });
  };
  gateway.on('upgrade', upgrade);
  const stop = () => {
    gateway.off('upgrade', upgrade);
    stopTimer();
    for (const client of clients) client.destroy();
    clients.clear();
  };
  gateway.once('close', stop);
  return { snapshot: () => ({ ...stats, clients: clients.size }), stop, sample };
}
