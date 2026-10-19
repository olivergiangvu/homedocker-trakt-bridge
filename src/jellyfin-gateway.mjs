/**
 * Opt-in, same-user Jellyfin NextUp gateway and exact-route Resume passthrough.
 * AIOStreams is NOT modified, patched, forked, pinned or written to.
 *
 * NextUp is an opt-in read-only view projection. Resume may ONLY be
 * passed through byte-for-byte when separately enabled, never normalized.
 * Streaming/WebSocket/playback and all other AIO paths stay directly on AIO.
 *
 * The upstream Jellyfin API authenticates all requests. We additionally ask
 * upstream /Users/Me and enforce an explicit user->Bridge profile mapping.
 * Never use another user's graph. Requests lacking credentials pass through.
 */
import http from 'node:http';
import { Buffer } from 'node:buffer';
import { loadVerifiedIdentityGraph } from './identity-continuity.mjs';
import { filterNextUpForVerifiedAliases } from './jellyfin-nextup-filter.mjs';
import { gatewayFilteringEnabled } from './gateway-control.mjs';

const MAX_NEXTUP_BYTES = 2 * 1024 * 1024;
const MAX_METADATA_SERIES = 50;
const MAX_METADATA_CONCURRENCY = 6;
// Global active bound across concurrent NextUp requests; per-request batching
// alone could otherwise multiply metadata traffic without an upper bound.
const MAX_METADATA_WAITERS = 96;

export function createMetadataLimiter(maxActive = MAX_METADATA_CONCURRENCY, maxWaiters = MAX_METADATA_WAITERS) {
  if (!Number.isSafeInteger(maxActive) || maxActive < 1
      || !Number.isSafeInteger(maxWaiters) || maxWaiters < 0) {
    throw new TypeError('invalid metadata concurrency limit');
  }
  let active = 0;
  let peakActive = 0;
  const waiters = [];
  const snapshot = () => ({ active, queued: waiters.length, peakActive });
  return {
    snapshot,
    async run(task) {
      if (typeof task !== 'function') throw new TypeError('metadata task required');
      if (active < maxActive) {
        active++;
        peakActive = Math.max(peakActive, active);
      } else {
        if (waiters.length >= maxWaiters) {
          const error = new Error('metadata_concurrency_capacity_exceeded');
          error.code = 'METADATA_QUEUE_FULL';
          throw error;
        }
        // A waiter receives the vacated active slot; release does not first
        // decrement the count (avoids a race with new arriving requests).
        await new Promise(resolve => waiters.push(resolve));
      }
      try { return await task(); }
      finally {
        const next = waiters.shift();
        if (next) next();
        else active--;
      }
    },
  };
}
const NETWORK_TIMEOUT_MS = 8000;
const NEXTUP_TIMEOUT_MAX_MS = 20000;

export function validatedNextUpTimeoutMs(value = NETWORK_TIMEOUT_MS) {
  if (!Number.isSafeInteger(value) || value < NETWORK_TIMEOUT_MS
      || value > NEXTUP_TIMEOUT_MAX_MS) {
    throw new Error('NextUp timeout must be an integer between 8000 and 20000 ms');
  }
  return value;
}

// The opt-in extension applies ONLY to the initial /Shows/NextUp GET.
// Authenticated /Users/Me, per-series metadata and Resume stay at 8000ms.
export function upstreamTimeoutForRoute(options, isNextUp) {
  return isNextUp ? options.nextUpTimeoutMs : NETWORK_TIMEOUT_MS;
}
const SERIES_METADATA_TTL_MS = 5 * 60 * 1000;
const MAX_SERIES_CACHE = 128;
const LEGACY_RESUME = /^\/Users\/[A-Za-z0-9_-]{8,128}\/Items\/Resume$/;
const HOP_HEADERS = new Set([
  'host', 'connection', 'keep-alive', 'proxy-connection',
  'transfer-encoding', 'upgrade', 'te', 'trailer',
  'proxy-authenticate', 'proxy-authorization',
  'content-length', 'content-encoding', 'accept-encoding',
]);
const AUTH_HEADERS = [
  'authorization', 'x-emby-authorization',
  'x-emby-token', 'x-mediabrowser-token',
];

export function validateGatewayConfig(config) {
  if (config.jellyfinGatewayEnabled !== true) return null;
  const raw = config.jellyfinGatewayUpstreamUrl;
  let url;
  try { url = new URL(raw); } catch { throw new Error('Invalid Jellyfin gateway upstream URL'); }
  if (!['http:', 'https:'].includes(url.protocol)
      || url.username || url.password || url.search || url.hash
      || !url.hostname) {
    throw new Error('Gateway upstream must be an absolute HTTP(S) URL without credentials or query');
  }
  if (typeof config.jellyfinGatewayUserId !== 'string'
      || !/^[A-Za-z0-9_-]{8,128}$/.test(config.jellyfinGatewayUserId)) {
    throw new Error('Explicit Jellyfin gateway UserId required');
  }
  if (!/^[a-f0-9]{24}$/.test(config.jellyfinGatewayProfileId || '')) {
    throw new Error('Explicit connected Bridge profile id required');
  }
  const prefix = url.pathname.replace(/\/+$/, '');
  return {
    base: url.origin + prefix,
    userId: config.jellyfinGatewayUserId,
    profileId: config.jellyfinGatewayProfileId,
    resumePassThrough: config.jellyfinGatewayResumePassThrough === true,
    firstPageMode: config.jellyfinGatewayFirstPageMode ?? 'short_first_page_only',
    nextUpTimeoutMs: validatedNextUpTimeoutMs(
      config.jellyfinGatewayNextUpTimeoutMs === undefined
        ? NETWORK_TIMEOUT_MS : config.jellyfinGatewayNextUpTimeoutMs
    ),
  };
}

function outboundHeaders(req) {
  const result = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (HOP_HEADERS.has(key)) continue;
    // Only called for a bounded GET route; do not trust inbound Host header.
    if (Array.isArray(value)) result.set(key, value.join(', '));
    else if (value != null) result.set(key, value);
  }
  result.set('accept-encoding', 'identity');
  return result;
}

function hasToken(req) {
  return AUTH_HEADERS.some((key) => typeof req.headers[key] === 'string'
    && req.headers[key].length > 0);
}

function upstreamPath(base, pathname, search = '') {
  return base + pathname + search;
}

async function readBounded(response, res) {
  const reader = response.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  const parts = [];
  let sum = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) return Buffer.concat(parts, sum);
    const buf = Buffer.from(value);
    sum += buf.length;
    parts.push(buf);
    if (sum > MAX_NEXTUP_BYTES) {
      // Too large to safely parse. Stream the original JSON through unchanged
      // (using decompressed body; content-encoding/length are always removed).
      responseHeaders(res, response, false);
      for (const p of parts) res.write(p);
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        res.write(Buffer.from(next.value));
      }
      res.end();
      return null;
    }
  }
}

function responseHeaders(res, response, mutated) {
  res.statusCode = response.status;
  for (const [name, value] of response.headers) {
    if (HOP_HEADERS.has(name) || name === 'set-cookie') continue;
    // set-cookie is copied separately to avoid merging multiple cookies.
    res.setHeader(name, value);
  }
  const cookies = response.headers.getSetCookie?.() ?? [];
  if (cookies.length) res.setHeader('set-cookie', cookies);
  if (mutated) {
    res.removeHeader('etag');
    res.removeHeader('content-md5');
    res.setHeader('cache-control', 'private, no-store');
    const inheritedVary = response.headers.get('vary');
    res.setHeader('vary', [
      inheritedVary,
      'Authorization, X-Emby-Authorization, X-Emby-Token, X-MediaBrowser-Token',
    ].filter(Boolean).join(', '));
  }
}

async function upstreamJson(url, headers, fetchImpl, maxBytes = 128 * 1024) {
  const response = await fetchImpl(url, {
    method: 'GET',
    headers,
    redirect: 'manual',
    signal: AbortSignal.timeout(NETWORK_TIMEOUT_MS),
  });
  if (response.status !== 200) throw new Error('AIO authenticated Jellyfin detail was unavailable');
  const declared = Number(response.headers.get('content-length') || 0);
  if (declared > maxBytes) throw new Error('Oversized Jellyfin metadata response');
  // Bound the parsed body even when Content-Length is missing or misleading.
  const stream = response.body?.getReader();
  if (!stream) throw new Error('Empty Jellyfin metadata response');
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await stream.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) throw new Error('Oversized Jellyfin metadata response');
      chunks.push(Buffer.from(value));
    }
  } finally {
    try { await stream.cancel(); } catch {}
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function hasVerifiedAliases(graph) {
  return Object.values(graph?.shows ?? {}).some((row) =>
    Array.isArray(row.aliases) && row.aliases.length > 0);
}

async function normalizeForUser(body, req, graph, options, fetchImpl, seriesCache, metadataInFlight, limiter, now) {
  if (!hasToken(req) || !hasVerifiedAliases(graph)
      || !Array.isArray(body?.Items) || body.Items.length < 2
      || body.Items.length > MAX_METADATA_SERIES) return body;
  const incoming = outboundHeaders(req);
  const user = await upstreamJson(upstreamPath(options.base, '/Users/Me'),
    incoming, fetchImpl);
  if (user?.Id !== options.userId) return body;

  const ids = [...new Set(body.Items.map((row) => row?.SeriesId))];
  if (ids.some((id) => typeof id !== 'string' || !id
      || id.length > 512)) return body;

  // All lookups use the SAME upstream token as /Users/Me and NextUp; metadata
  // lookups never use Bridge's Trakt OAuth token or another Jellyfin account.
  const found = new Map();
  const freshTime = now();
  const needLookup = [];
  for (const id of ids) {
    const entry = seriesCache.get(id);
    if (entry && entry.expiresAt > freshTime) {
      // Cache is owned by this one exact verified Jellyfin account/gateway,
      // never keyed across accounts, profiles, or token-sharing contexts.
      found.set(id, entry.imdb);
    } else {
      seriesCache.delete(id);
      needLookup.push(id);
    }
  }
  const completed = new Map();
  for (let index = 0; index < needLookup.length; index += MAX_METADATA_CONCURRENCY) {
    await Promise.all(needLookup.slice(index, index + MAX_METADATA_CONCURRENCY)
      .map(async (id) => {
        // Concurrent NextUp refreshes for the SAME already authenticated AIO
        // user share pending SeriesId metadata reads. Never share credentials,
        // token buffers or responses with a different user. /Users/Me is still
        // verified for every request before consulting this per-listener map.
        let pending = metadataInFlight.get(id);
        if (!pending) {
          pending = limiter.run(async () => {
            const item = await upstreamJson(upstreamPath(options.base,
              '/Items/' + encodeURIComponent(id)), incoming, fetchImpl);
            if (item?.Type !== 'Series' || item.Id !== id) {
              throw new Error('Jellyfin returned mismatched series identity');
            }
            const imdb = item.ProviderIds?.Imdb;
            return typeof imdb === 'string' ? imdb.toLowerCase() : null;
          }).finally(() => metadataInFlight.delete(id));
          metadataInFlight.set(id, pending);
        }
        completed.set(id, await pending);
      }));
  }
  // Don't cache a partial metadata batch if another lookup fails. On success,
  // bound cache size and remember only nonsecret seriesId -> show IMDb.
  for (const [id, spelling] of completed) {
    found.set(id, spelling);
    seriesCache.delete(id);
    seriesCache.set(id, {
      imdb: spelling,
      expiresAt: now() + SERIES_METADATA_TTL_MS,
    });
  }
  while (seriesCache.size > MAX_SERIES_CACHE) {
    seriesCache.delete(seriesCache.keys().next().value);
  }
  return filterNextUpForVerifiedAliases(body, {
    verifiedGraph: graph,
    resolveSeriesImdb: async (id) => found.get(id) ?? null,
  });
}

/**
 * Odin commonly sends StartIndex=0. Only allow in-memory normalization when
 * a bounded, demonstrably short first page was delivered, with exact counts.
 * A full page can have hidden later results even if AIO reports prefix totals:
 * NEVER mutate those pages or StartIndex>0. The policy fails open to AIO.
 */
export function eligibleCompleteFirstPage(searchParams, body, mode = 'short_first_page_only') {
  if (mode !== 'short_first_page_only'
      || searchParams.getAll('StartIndex').length !== 1
      || searchParams.get('StartIndex') !== '0'
      || searchParams.getAll('Limit').length !== 1) return false;
  const limitValue = searchParams.get('Limit');
  if (!/^[1-9][0-9]?$/.test(limitValue ?? '')) return false;
  const limit = Number(limitValue);
  if (limit > MAX_METADATA_SERIES || limit < 2) return false;
  const allowed = new Set(['StartIndex', 'Limit']);
  if ([...searchParams.keys()].some(key => !allowed.has(key))) return false;
  const items = body?.Items;
  if (!Array.isArray(items) || items.length < 2 || items.length >= limit) return false;
  if (body?.StartIndex !== undefined && body.StartIndex !== 0) return false;
  if (body?.TotalRecordCount !== items.length) return false;
  if (Object.hasOwn(body, 'Count') && body.Count !== items.length) return false;
  return true;
}

function sendJson(res, status, payload) {
  const bytes = Buffer.from(JSON.stringify(payload));
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.setHeader('content-length', bytes.length);
  res.end(bytes);
}

export function createJellyfinGateway({ config, db, fetchImpl = fetch, now = Date.now }) {
  const options = validateGatewayConfig(config);
  if (!options) throw new Error('Gateway must be explicitly enabled');
  // This gateway is configured for ONE exact AIO Jellyfin user ID. The cache
  // only sees /Items/{SeriesId} after /Users/Me matches that user, and never
  // contains tokens or source watch-state rows.
  const seriesCache = new Map();
  const metadataInFlight = new Map();
  const limiter = createMetadataLimiter();
  // Transient listener-level counters only. No request paths, usernames,
  // auth headers, tokens, item IDs, titles or watch positions are retained.
  const metrics = {
    nextUpRequests: 0,
    resumePassthroughRequests: 0,
    nextUpNormalizedResponses: 0,
    nextUpUnchangedResponses: 0,
    nextUpRemovedItems: 0,
    nextUpPagedPassThrough: 0,
    failOpenNormalizationErrors: 0,
    upstreamTransportFailures: 0,
    upstreamNon200Responses: 0,
    responseFailures: 0,
    oversizedPassThroughResponses: 0,
    invalidJsonPassThroughResponses: 0,
    // Anonymous aggregate latency only: no paths, user IDs or watch history.
    // These measure local Gateway wall time, not client/network end-to-end p95.
    nextUpCompletedResponses: 0,
    nextUpResponseTotalMs: 0,
    nextUpResponseMaxMs: 0,
    nextUpUpstreamSamples: 0,
    nextUpUpstreamTotalMs: 0,
    nextUpUpstreamMaxMs: 0,
  };

  function observeUpstreamLatency(started) {
    const elapsed = Math.max(0, Math.round(performance.now() - started));
    metrics.nextUpUpstreamSamples++;
    metrics.nextUpUpstreamTotalMs += elapsed;
    metrics.nextUpUpstreamMaxMs = Math.max(metrics.nextUpUpstreamMaxMs, elapsed);
  }


  const server = http.createServer(async (req, res) => {
    // Health is for local nginx/docker probes. Do not leak config/credentials.
    if (req.method === 'GET' && req.url === '/health') {
      return sendJson(res, 200, { status: 'ok', adapter: 'nextup-only' });
    }
    let requested;
    try { requested = new URL(req.url || '/', 'http://gateway.local'); }
    catch { return sendJson(res, 400, { error: 'invalid_path' }); }
    const nextUp = requested.pathname === '/Shows/NextUp';
    const resumeRoute = requested.pathname === '/UserItems/Resume'
      || LEGACY_RESUME.test(requested.pathname);
    const resumePassthrough = options.resumePassThrough && resumeRoute;
    if (!nextUp && !resumePassthrough) {
      return sendJson(res, 404, { error: 'gateway_route_not_enabled' });
    }
    if (req.method !== 'GET') {
      res.setHeader('allow', 'GET');
      return sendJson(res, 405, { error: 'method_not_allowed' });
    }

    if (nextUp) {
      metrics.nextUpRequests++;
      const started = performance.now();
      res.once('finish', () => {
        const elapsed = Math.max(0, Math.round(performance.now() - started));
        metrics.nextUpCompletedResponses++;
        metrics.nextUpResponseTotalMs += elapsed;
        metrics.nextUpResponseMaxMs = Math.max(metrics.nextUpResponseMaxMs, elapsed);
      });
    } else metrics.resumePassthroughRequests++;

    const headers = outboundHeaders(req);
    const destination = upstreamPath(options.base, requested.pathname, requested.search);
    let upstream;
    const upstreamStarted = nextUp ? performance.now() : null;
    try {
      upstream = await fetchImpl(destination, {
        method: 'GET', headers, redirect: 'manual',
        signal: AbortSignal.timeout(upstreamTimeoutForRoute(options, nextUp)),
      });
    } catch {
      if (nextUp) observeUpstreamLatency(upstreamStarted);
      metrics.upstreamTransportFailures++;
      return sendJson(res, 502, { error: 'upstream_unavailable' });
    }
    if (nextUp) observeUpstreamLatency(upstreamStarted);
    try {
      const bytes = await readBounded(upstream, res);
      if (!bytes) {
        metrics.oversizedPassThroughResponses++;
        return; // large response streamed as-is
      }
      if (upstream.status !== 200 || resumePassthrough) {
        if (upstream.status !== 200) metrics.upstreamNon200Responses++;
        // Resume is ALWAYS a byte-for-byte representation passthrough:
        // preserve Items, pagination, TotalRecordCount and every movie/resume.
        // AIO's reported TotalRecordCount may be a fetched-prefix count.
        responseHeaders(res, upstream, false);
        return res.end(bytes);
      }
      // OFF is a genuine fast bypass: upstream JSON bytes are passed through
      // without parsing, metadata lookups, graph reads or response projection.
      if (!gatewayFilteringEnabled({ db, config, profileId: options.profileId })) {
        metrics.nextUpUnchangedResponses++;
        responseHeaders(res, upstream, false);
        return res.end(bytes);
      }
      let json;
      try { json = JSON.parse(bytes.toString('utf8')); }
      catch {
        metrics.invalidJsonPassThroughResponses++;
        responseHeaders(res, upstream, false);
        return res.end(bytes);
      }
      let filtered = json;
      // Series-specific NextUp must remain upstream-defined. Unknown paging or
      // alternative filters also remain untouched until separately qualified.
      const hasPaging = requested.searchParams.has('StartIndex');
      const firstPage = eligibleCompleteFirstPage(
        requested.searchParams, json, options.firstPageMode
      );
      if (hasPaging && !firstPage) metrics.nextUpPagedPassThrough++;
      // Never project arbitrary upstream selectors (especially UserId) even
      // without a paging key. The /Users/Me probe proves the token's owner,
      // not that an explicit query did not select somebody else's library.
      const simpleUnpaged = !hasPaging
        && [...requested.searchParams.keys()].every(key => key === 'Limit')
        && requested.searchParams.getAll('Limit').length <= 1;
      if (simpleUnpaged || firstPage) {
        try {
          const graph = loadVerifiedIdentityGraph(db, options.profileId);
          filtered = await normalizeForUser(json, req, graph, options, fetchImpl, seriesCache, metadataInFlight, limiter, now);
        } catch {
          metrics.failOpenNormalizationErrors++;
          filtered = json; // Fail open; never hide an ambiguous episode.
        }
      }
      if (filtered !== json) {
        metrics.nextUpNormalizedResponses++;
        metrics.nextUpRemovedItems += Math.max(
          0, (json.Items?.length ?? 0) - (filtered.Items?.length ?? 0)
        );
      } else {
        metrics.nextUpUnchangedResponses++;
      }
      responseHeaders(res, upstream, filtered !== json);
      const output = filtered === json ? bytes : Buffer.from(JSON.stringify(filtered));
      return res.end(output);
    } catch {
      metrics.responseFailures++;
      if (res.headersSent) return res.destroy();
      return sendJson(res, 502, { error: 'upstream_response_failure' });
    }
  });

  // Admin/profile dashboard accesses this method from the owning Bridge
  // process only; it is NOT a public HTTP endpoint on the gateway.
  server.gatewayMetricsSnapshot = () => ({
    ...metrics,
    metadataActive: limiter.snapshot().active,
    metadataQueued: limiter.snapshot().queued,
    metadataPeakActive: limiter.snapshot().peakActive,
    metadataCacheEntries: seriesCache.size,
  });
  return server;
}
