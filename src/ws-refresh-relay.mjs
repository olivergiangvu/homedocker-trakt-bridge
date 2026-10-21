/**
 * Narrow AIOStreams Jellyfin WebSocket passthrough + local imported-state
 * notification. Keeps upstream AIO untouched and leaves v1.4 NextUp intact.
 * Gate: JELLYFIN_GATEWAY_WS_NOTIFY_ENABLED=true AND existing exact UserId mapping.
 */
import { WebSocket, WebSocketServer } from 'ws';
import { createImportWitness } from './watch-state-import-witness.mjs';

const SOCKET_PATH = /^\/jellyfin(?:\/[^/?]+\/[^/?]+)?(?:\/(?:emby|mediabrowser))?\/(?:socket|websocket)$/i;
const MAX_QUEUED = 2 * 1024 * 1024;
const OBSERVE_EVERY_MS = 2500;
const UPGRADE_TIMEOUT_MS = 8000;
const MAX_SOCKETS = 8;

export function matchesAioSocketPath(path) {
  return typeof path === 'string' && SOCKET_PATH.test(path);
}

function reject(socket, code = 404) {
  if (!socket.destroyed) {
    socket.end(`HTTP/1.1 ${code} Error\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
  }
}

function apiKeyOf(req, query) {
  return query.get('api_key') || query.get('ApiKey') || query.get('apikey') || query.get('token') ||
    req.headers['x-emby-token'] || req.headers['x-mediabrowser-token'] || null;
}

async function verifiedUser(req, parsed, options, fetchImpl) {
  const token = apiKeyOf(req, parsed.searchParams);
  if (typeof token !== 'string' || !token || token.length > 2048) return false;
  try {
    const response = await fetchImpl(options.base + '/Users/Me', {
      method: 'GET', headers: { 'X-Emby-Token': token },
      redirect: 'manual', signal: AbortSignal.timeout(UPGRADE_TIMEOUT_MS),
    });
    if (response.status !== 200) return false;
    const bytes = await response.text();
    if (bytes.length > 64 * 1024) return false;
    return JSON.parse(bytes).Id === options.userId;
  } catch { return false; }
}

/** Returns a synchronous cleanup function, invoked on Bridge shutdown. */
export function installWatchRefreshRelay(server, {
  enabled, options, dbPath, fetchImpl = fetch, witnessFactory = createImportWitness,
}) {
  if (!enabled) return () => {};
  if (!options || !options.userId || !options.base || !dbPath)
    throw new Error('watch refresh relay requires exact configured AIO identity and DB path');
  const upstreamBase = new URL(options.base);
  if (!['http:', 'https:'].includes(upstreamBase.protocol))
    throw new Error('invalid refresh relay upstream');
  const prefix = upstreamBase.pathname.replace(/\/+$/, '');
  if (!prefix.toLowerCase().startsWith('/jellyfin')) throw new Error('refresh relay requires Jellyfin mount');
  const wss = new WebSocketServer({ noServer: true, perMessageDeflate: false, maxPayload: MAX_QUEUED });
  const clients = new Set();
  let interval = null;
  let witness = null;

  function emitImportChange() {
    if (!clients.size || !witness) return;
    let sample;
    try { sample = witness.sample(); }
    catch {
      // Failed reads never emit a success-shaped refresh. Re-baseline on recovery.
      witness = null;
      return;
    }
    if (sample.status !== 'IMPORT_OBSERVED') return;
    const frame = JSON.stringify({
      MessageType: 'UserDataChanged',
      Data: { UserId: options.userId, UserDataList: [] },
    });
    for (const socket of clients) {
      if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < MAX_QUEUED)
        socket.send(frame);
    }
  }

  function startObserver() {
    if (interval || !clients.size) return;
    witness = null;
    try {
      witness = witnessFactory({ dbPath });
      witness.sample(); // baseline; do not replay old imports on every reconnect
    } catch { witness = null; }
    interval = setInterval(() => {
      if (!witness) {
        try {
          witness = witnessFactory({ dbPath });
          witness.sample();
        } catch { witness = null; }
        return;
      }
      emitImportChange();
    }, OBSERVE_EVERY_MS);
    interval.unref?.();
  }

  function stopObserver() {
    if (clients.size) return;
    if (interval) clearInterval(interval);
    interval = null;
    witness = null;
  }

  const onUpgrade = async (req, socket, head) => {
    let requested;
    try { requested = new URL(req.url || '/', 'http://gateway.local'); }
    catch { return reject(socket, 400); }
    if (!matchesAioSocketPath(requested.pathname)
        || !requested.pathname.startsWith(prefix + '/')) return reject(socket, 404);
    if (clients.size >= MAX_SOCKETS) return reject(socket, 503);
    if (!(await verifiedUser(req, requested, options, fetchImpl))) return reject(socket, 401);
    if (socket.destroyed) return;

    const destination = new URL(upstreamBase.origin);
    destination.protocol = destination.protocol === 'https:' ? 'wss:' : 'ws:';
    destination.pathname = requested.pathname;
    destination.search = requested.search;
    const hdr = {};
    if (typeof req.headers.origin === 'string') hdr.Origin = req.headers.origin;
    // AIO validates the SAME client-supplied api_key on upstream handshake.
    const upstream = new WebSocket(destination.toString(), {
      headers: hdr, handshakeTimeout: UPGRADE_TIMEOUT_MS,
      maxPayload: MAX_QUEUED, perMessageDeflate: false,
    });
    const fail = () => { if (!socket.destroyed) reject(socket, 502); };
    upstream.once('error', fail);
    upstream.once('unexpected-response', (_req, res) => {
      res.resume();
      fail();
    });
    upstream.once('open', () => {
      upstream.off('error', fail);
      if (socket.destroyed) { upstream.close(); return; }
      wss.handleUpgrade(req, socket, head, client => {
        clients.add(client);
        startObserver();
        const forward = (target, message, binary) => {
          if (target.readyState !== WebSocket.OPEN) return;
          if (target.bufferedAmount >= MAX_QUEUED) { target.close(1011); return; }
          target.send(message, { binary });
        };
        client.on('message', (data, binary) => forward(upstream, data, binary));
        upstream.on('message', (data, binary) => forward(client, data, binary));
        client.on('close', () => {
          clients.delete(client); stopObserver(); if (upstream.readyState === WebSocket.OPEN) upstream.close();
        });
        upstream.on('close', () => { if (client.readyState === WebSocket.OPEN) client.close(); });
        upstream.on('error', () => { if (client.readyState === WebSocket.OPEN) client.close(1011); });
        client.on('error', () => { if (upstream.readyState === WebSocket.OPEN) upstream.close(1011); });
      });
    });
  };
  server.on('upgrade', onUpgrade);
  return () => {
    server.off('upgrade', onUpgrade);
    for (const client of clients) client.close(1001);
    clients.clear();
    stopObserver();
    wss.close();
  };
}
