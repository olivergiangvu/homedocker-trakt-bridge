/**
 * G2-1B: isolated Odin/macOS NextUp SHADOW ingress.
 *
 * Exported factory only: importing this module NEVER starts a listener,
 * sends traffic, opens a database or reads environment variables.
 *
 * AIO is always the ONLY provider of client responses. After distinct
 * approval, an exact NextUp GET may be copied to an already-configured local
 * Gateway for evidence only. Nothing from that Gateway reaches Odin.
 *
 * Do not deploy onto RC6/production. G2 release HOLD remains.
 */
import http from 'node:http';
import { planOdinShadowRoute } from './odin-g2-shadow-policy.mjs';

const MAX_TARGET = 4096;
const MAX_LOGIN_BYTES = 256 * 1024;
const MAX_SHADOW_BYTES = 2 * 1024 * 1024;
const MAX_SHADOW_REQUESTS = 3;
const HOP_HEADERS = new Set([
  'connection', 'keep-alive', 'proxy-connection', 'upgrade', 'te',
  'trailer', 'transfer-encoding', 'proxy-authenticate',
  'proxy-authorization',
]);
const VALID_PATH = /^\/[\x21-\x7e]*$/;

function requireLoopbackUrl(value, prefix) {
  let url;
  try { url = new URL(value); }
  catch { throw new Error('SHADOW_STOP_INVALID_UPSTREAM_CONFIG'); }
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1'
    || !/^\d+$/.test(url.port) || Number(url.port) < 1024
    || url.username || url.password || url.search || url.hash
    || url.pathname.replace(/\/+$/, '') !== prefix) {
    throw new Error('SHADOW_STOP_NON_LOOPBACK_UPSTREAM');
  }
  return url;
}

function copyHeaders(input) {
  const result = {};
  for (const [key, val] of Object.entries(input)) {
    if (!HOP_HEADERS.has(key.toLowerCase()) && val !== undefined) {
      result[key] = val;
    }
  }
  return result;
}

function checkRequestTarget(method, target) {
  if (!['GET', 'HEAD', 'OPTIONS', 'POST'].includes(method)
    && !['PUT', 'PATCH', 'DELETE'].includes(method)) return false;
  if (typeof target !== 'string' || target.length > MAX_TARGET
    || !VALID_PATH.test(target) || target.startsWith('//')
    || target.includes('\\') || target.includes('#')
    || !/^\/jellyfin(?:\/|\?|$)/.test(target)) return false;
  // Prevent encoded path tricks from slipping around our route allowlist.
  const path = target.split('?', 1)[0];
  if (/%2f|%5c|%00/i.test(path)) return false;
  return true;
}

function shadowTargetSafe(target) {
  // Avoid copied tokens in URL query. Auth still travels to local Gateway
  // only through the exact in-memory original Jellyfin headers.
  const query = target.split('?')[1] ?? '';
  return query.length <= 512
    && !/(?:^|&)(?:token|api_key|apikey|authorization|access_token)=/i.test(query);
}

function classifyStatus(status) {
  return status >= 200 && status < 300 ? '2xx'
    : status >= 300 && status < 400 ? '3xx'
      : status >= 400 && status < 500 ? '4xx' : '5xx';
}

export function createOdinShadowIngress({
  aioBase,
  gatewayBase,
  shadowApproved = false,
  shadowTimeoutMs = 3000,
} = {}) {
  const aio = requireLoopbackUrl(aioBase, '/jellyfin');
  const gateway = requireLoopbackUrl(gatewayBase, '');
  if (aio.port === gateway.port || !Number.isSafeInteger(shadowTimeoutMs)
    || shadowTimeoutMs < 500 || shadowTimeoutMs > 8000) {
    throw new Error('SHADOW_STOP_INVALID_RUNTIME_CONFIG');
  }

  // In-memory scalar counts only; NEVER store paths, identities, URL query,
  // auth headers, cookies, item IDs, watch state or response payloads.
  const metrics = {
    client_requests: 0, client_nextup_requests: 0,
    client_resume_requests: 0, client_aio_response_2xx: 0,
    client_aio_response_3xx: 0, client_aio_response_4xx: 0,
    client_aio_response_5xx: 0, client_upstream_transport_failures: 0,
    locally_blocked_writes: 0, locally_blocked_other: 0,
    shadow_attempted: 0, shadow_completed: 0,
    shadow_response_2xx: 0, shadow_response_3xx: 0,
    shadow_response_4xx: 0, shadow_response_5xx: 0,
    shadow_timeouts: 0, shadow_errors: 0, shadow_skipped_capacity: 0,
    gateway_client_responses: 0, playback_writes_forwarded: 0,
    production_database_writes: 0,
  };
  let activeShadow = 0;

  function maybeShadow(target, headers) {
    if (!shadowApproved || !shadowTargetSafe(target)) return;
    if (metrics.shadow_attempted >= MAX_SHADOW_REQUESTS || activeShadow >= 1) {
      metrics.shadow_skipped_capacity++;
      return;
    }
    // The Gateway is an already-running loopback-only NextUp listener.
    // Strip /jellyfin but retain original query bytes.
    const suffix = target.slice('/jellyfin'.length);
    metrics.shadow_attempted++;
    activeShadow++;
    let settled = false;
    let bytes = 0;
    const finish = (errorType, status) => {
      if (settled) return;
      settled = true;
      activeShadow--;
      if (errorType === 'timeout') metrics.shadow_timeouts++;
      else if (errorType) metrics.shadow_errors++;
      else {
        metrics.shadow_completed++;
        metrics['shadow_response_' + classifyStatus(status)]++;
      }
    };
    const request = http.request({
      hostname: '127.0.0.1', port: Number(gateway.port), method: 'GET',
      path: suffix, agent: false,
      headers: copyHeaders(headers),
    }, response => {
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > MAX_SHADOW_BYTES) {
          finish('over_limit');
          request.destroy();
        }
      });
      response.once('end', () => finish(null, response.statusCode));
      response.once('error', () => finish('read_error'));
    });
    request.setTimeout(shadowTimeoutMs, () => {
      finish('timeout');
      request.destroy();
    });
    request.once('error', () => finish('transport'));
    request.end();
  }

  const server = http.createServer((req, res) => {
    metrics.client_requests++;
    const target = req.url;
    if (!checkRequestTarget(req.method, target) || req.headers.upgrade) {
      metrics.locally_blocked_other++;
      res.writeHead(400, { 'cache-control': 'no-store' });
      return res.end();
    }
    const plan = planOdinShadowRoute(req.method, target, {
      approved: shadowApproved,
    });
    if (plan.clientDestination === 'local_405') {
      metrics.locally_blocked_writes++;
      res.writeHead(405, { 'allow': 'GET, HEAD, OPTIONS' });
      return res.end();
    }
    // Explicitly never proxy any GET with a body; the read-only pilot only
    // expects read-style requests plus one allowlisted login POST.
    if (req.method !== 'POST'
      && ((req.headers['content-length'] && req.headers['content-length'] !== '0')
        || req.headers['transfer-encoding'])) {
      metrics.locally_blocked_other++;
      res.writeHead(400);
      return res.end();
    }

    if (plan.category === 'nextup') metrics.client_nextup_requests++;
    if (plan.category === 'resume') metrics.client_resume_requests++;

    const relay = (loginBytes = null) => {
      const upstream = http.request({
        hostname: '127.0.0.1', port: Number(aio.port), method: req.method,
        path: target, headers: copyHeaders(req.headers), agent: false,
      }, upstreamRes => {
        const status = upstreamRes.statusCode ?? 502;
        metrics['client_aio_response_' + classifyStatus(status)]++;
        if (!res.headersSent) {
          res.writeHead(status, upstreamRes.statusMessage,
            copyHeaders(upstreamRes.headers));
        }
        // Native Node streaming preserves source payload bytes including
        // compression/ETag/content-length, without decoding or parsing.
        upstreamRes.on('error', () => {
          if (!res.writableEnded) res.destroy();
        });
        upstreamRes.pipe(res);
      });
      upstream.setTimeout(15000, () => upstream.destroy(new Error('timeout')));
      upstream.on('error', () => {
        metrics.client_upstream_transport_failures++;
        if (!res.headersSent) {
          res.writeHead(502, { 'cache-control': 'no-store' });
          res.end();
        } else if (!res.writableEnded) res.destroy();
      });
      req.once('aborted', () => upstream.destroy());
      res.once('close', () => {
        if (!res.writableEnded) upstream.destroy();
      });
      if (loginBytes !== null) upstream.end(loginBytes);
      else upstream.end();

      // Launch shadow independently and NEVER await it before serving Odin.
      // Copy only exact NextUp GET; no other request may go to Gateway.
      if (plan.shadowGatewayGet) maybeShadow(target, req.headers);
    };

    if (req.method !== 'POST') return relay();

    // Exact AuthenticationByName POST only, with bounded in-memory body.
    // Do not log/store the secret or submit a partial login upstream.
    const chunks = [];
    let length = 0;
    let done = false;
    const reject = () => {
      if (done) return;
      done = true;
      metrics.locally_blocked_other++;
      chunks.length = 0;
      res.writeHead(413, { 'cache-control': 'no-store' });
      res.end();
    };
    if (Number(req.headers['content-length']) > MAX_LOGIN_BYTES) return reject();
    req.on('data', chunk => {
      if (done) return;
      length += chunk.length;
      if (length > MAX_LOGIN_BYTES) return reject();
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (done) return;
      done = true;
      relay(Buffer.concat(chunks));
      chunks.length = 0;
    });
    req.once('error', reject);
  });

  // Only aggregate, non-identifying counters. No HTTP admin route.
  server.shadowMetricsSnapshot = () => ({ ...metrics, shadow_active: activeShadow,
    shadow_enabled: shadowApproved === true,
    gateway_client_qualified: false, stable_release_hold: true });
  return server;
}
