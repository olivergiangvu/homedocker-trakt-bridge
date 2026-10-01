import http from 'node:http';
import { randomBytes } from 'node:crypto';
import { APP_NAME, APP_VERSION } from './config.mjs';
import { deriveProfileKey, randomToken, safeEqual } from './crypto.mjs';
import { BridgeError } from './errors.mjs';
import { page, escapeHtml } from './html.mjs';
import { buildManifest, planEvent, validatePushEvent } from './watch-state.mjs';
import { formatEventTime, summarizeRecentEvents } from './diagnostics.mjs';
import { cachedPullPayload, makePullCacheEntry, stalePullPayload } from './pull-cache.mjs';
import { coveredByRecentBulk, rememberBulkCoverage } from './bulk-dedupe.mjs';

const inflight = new Map();
const pullInflight = new Map();
const pullCache = new Map();
const PULL_CACHE_PREFIX = 'pull-state:v5:';

export function createServer({ config, db, trakt }) {
  return http.createServer(async (req, res) => {
    const started = Date.now();
    try {
      await route(req, res, { config, db, trakt });
    } catch (err) {
      const e = err instanceof BridgeError ? err : new BridgeError(err?.message || 'Internal error', { status: 500, cause: err });
      if (!res.headersSent) {
        if (e.retryAfter) res.setHeader('Retry-After', String(e.retryAfter));
        sendJson(res, e.status, { error: e.code, message: e.status >= 500 ? 'Upstream/internal error' : e.message });
      } else {
        res.end();
      }
      console.error(JSON.stringify({ level: 'error', status: e.status, code: e.code, message: e.message, retryAfter: e.retryAfter || null, upstreamPath: e.upstreamPath || null, ms: Date.now() - started }));
    }
  });
}

async function route(req, res, ctx) {
  const { config, db, trakt } = ctx;
  const url = new URL(req.url, config.publicBaseUrl);
  const path = url.pathname;

  securityHeaders(res);

  if (req.method === 'GET' && path === '/health') {
    return sendJson(res, 200, { status: 'ok', app: APP_NAME, version: APP_VERSION });
  }

  if (req.method === 'GET' && path === '/') {
    return sendHtml(res, 200, page(APP_NAME, `<div class="card"><h1>${APP_NAME}</h1><p>Self-hosted AIOStreams <code>watch_state v2</code> ↔ Trakt bridge.</p><p class="muted">v${APP_VERSION}</p></div>`));
  }

  if (path === '/setup') {
    requireAdmin(url, config);
    if (req.method === 'GET') return renderAdmin(res, ctx, url.searchParams.get('key'));
    if (req.method === 'POST') {
      const form = await readForm(req);
      const id = randomBytes(12).toString('hex');
      db.createProfile(id, String(form.get('name') || 'Trakt').slice(0, 80));
      const setupKey = deriveProfileKey(config.bridgeSecret, 'setup', id);
      redirect(res, `/u/${id}/setup?key=${encodeURIComponent(setupKey)}`);
      return;
    }
  }

  if (req.method === 'GET' && path === '/oauth/callback') {
    const code = url.searchParams.get('code');
    const state = url.searchParams.get('state');
    if (!code || !state) throw new BridgeError('Missing OAuth code/state', { status: 400, code: 'oauth_callback_invalid' });
    const profileId = db.consumeOauthState(state);
    if (!profileId) throw new BridgeError('OAuth state expired or invalid', { status: 400, code: 'oauth_state_invalid' });
    const tokenResponse = await trakt.exchangeCode(code);
    db.setTokens(profileId, tokenResponse);
    const setupKey = deriveProfileKey(config.bridgeSecret, 'setup', profileId);
    redirect(res, `/u/${profileId}/setup?key=${encodeURIComponent(setupKey)}&connected=1`);
    return;
  }

  const setupMatch = path.match(/^\/u\/([a-f0-9]{24})\/setup$/);
  if (setupMatch) {
    const profileId = setupMatch[1];
    requireSetupKey(url, config, db, profileId);
    if (req.method !== 'GET') return methodNotAllowed(res);
    return renderProfile(res, ctx, profileId, url.searchParams.get('key'));
  }

  const oauthStartMatch = path.match(/^\/u\/([a-f0-9]{24})\/oauth\/start$/);
  if (oauthStartMatch) {
    const profileId = oauthStartMatch[1];
    requireSetupKey(url, config, db, profileId);
    if (req.method !== 'GET') return methodNotAllowed(res);
    const state = randomToken(32);
    db.putOauthState(state, profileId);
    const target = new URL('https://auth.trakt.tv/oauth/authorize');
    target.searchParams.set('response_type', 'code');
    target.searchParams.set('client_id', config.traktClientId);
    target.searchParams.set('redirect_uri', config.redirectUri);
    target.searchParams.set('state', state);
    redirect(res, target.toString());
    return;
  }

  const disconnectMatch = path.match(/^\/u\/([a-f0-9]{24})\/disconnect$/);
  if (disconnectMatch) {
    const profileId = disconnectMatch[1];
    requireSetupKey(url, config, db, profileId);
    if (req.method !== 'POST') return methodNotAllowed(res);
    db.clearTokens(profileId);
    clearPullCache(db, profileId);
    redirect(res, `/u/${profileId}/setup?key=${encodeURIComponent(url.searchParams.get('key'))}`);
    return;
  }

  const manifestMatch = path.match(/^\/u\/([a-f0-9]{24})\/([A-Za-z0-9_-]{30,})\/manifest\.json$/);
  if (manifestMatch) {
    const [, profileId, addonKey] = manifestMatch;
    requireAddonKey(config, db, profileId, addonKey);
    if (req.method !== 'GET') return methodNotAllowed(res);
    return sendJson(res, 200, buildManifest(profileId, config.pullTtlSeconds));
  }

  const pullMatch = path.match(/^\/u\/([a-f0-9]{24})\/([A-Za-z0-9_-]{30,})\/watch_state\/pull\.json$/);
  if (pullMatch) {
    const [, profileId, addonKey] = pullMatch;
    requireAddonKey(config, db, profileId, addonKey);
    if (req.method !== 'GET') return methodNotAllowed(res);
    const profile = db.getProfile(profileId);
    if (!profile?.access_token_enc) throw new BridgeError('Trakt account is not connected', { status: 401, code: 'not_connected' });
    const since = url.searchParams.get('since') || null;
    return processPull(res, { profileId, since, db, trakt, config });
  }

  const pushMatch = path.match(/^\/u\/([a-f0-9]{24})\/([A-Za-z0-9_-]{30,})\/watch_state\/push\/(movie|series)\/(.+)\.json$/);
  if (pushMatch) {
    const [, profileId, addonKey] = pushMatch;
    requireAddonKey(config, db, profileId, addonKey);
    if (req.method !== 'POST') return methodNotAllowed(res);
    const profile = db.getProfile(profileId);
    if (!profile?.access_token_enc) throw new BridgeError('Trakt account is not connected', { status: 401, code: 'not_connected' });
    const body = validatePushEvent(await readJson(req, 128 * 1024));
    return processPush(res, { profileId, body, db, trakt, config });
  }

  sendJson(res, 404, { error: 'not_found' });
}

function pullCacheKey(profileId) {
  return `${PULL_CACHE_PREFIX}${profileId}`;
}

function getPullCache(db, profileId) {
  const memory = pullCache.get(profileId);
  if (memory) return { entry: memory, layer: 'memory' };

  const persisted = db.cacheGet(pullCacheKey(profileId));
  if (!persisted) return { entry: null, layer: null };
  pullCache.set(profileId, persisted);
  return { entry: persisted, layer: 'sqlite' };
}

function storePullCache(db, profileId, entry, staleSeconds) {
  pullCache.set(profileId, entry);
  db.cacheSet(pullCacheKey(profileId), entry, staleSeconds);
}

function clearPullCache(db, profileId) {
  pullCache.delete(profileId);
  db.cacheDelete(pullCacheKey(profileId));
}

async function processPull(res, { profileId, since, db, trakt, config }) {
  const eventId = `pull|${since || 'initial'}`;
  const now = Date.now();
  const cacheState = getPullCache(db, profileId);
  const cached = cachedPullPayload(cacheState.entry, since, now, config.pullTtlSeconds);

  if (cached) {
    const detail = pullDetail(cached, {
      source: 'cache',
      cacheLayer: cacheState.layer,
      ageSeconds: Math.max(0, Math.floor((now - cacheState.entry.fetchedAt) / 1000)),
    });
    db.logEvent({ profileId, eventId, event: 'pull', status: 'cached', detail: JSON.stringify(detail) });
    return sendJson(res, 200, cached);
  }

  const inflightKey = `${profileId}|${since || 'initial'}`;
  let task = pullInflight.get(inflightKey);
  const coalesced = Boolean(task);
  if (!task) {
    task = trakt.pullState(profileId, since).finally(() => pullInflight.delete(inflightKey));
    pullInflight.set(inflightKey, task);
  }

  try {
    const payload = await task;
    const cacheEntry = makePullCacheEntry(payload, Date.now());
    if (cacheEntry) storePullCache(db, profileId, cacheEntry, config.pullStaleIfErrorSeconds);
    const detail = pullDetail(payload, { source: coalesced ? 'coalesced' : 'trakt' });
    db.logEvent({ profileId, eventId, event: 'pull', status: 'ok', detail: JSON.stringify(detail) });
    return sendJson(res, 200, payload);
  } catch (err) {
    const mayUseStale = err?.status === 429 || Number(err?.status) >= 500;
    const staleState = getPullCache(db, profileId);
    const stale = mayUseStale
      ? stalePullPayload(staleState.entry, since, Date.now(), config.pullStaleIfErrorSeconds)
      : null;

    if (stale) {
      const detail = pullDetail(stale, {
        source: 'stale-cache',
        cacheLayer: staleState.layer,
        ageSeconds: Math.max(0, Math.floor((Date.now() - staleState.entry.fetchedAt) / 1000)),
        upstreamError: err.code || 'error',
        upstreamPath: err.upstreamPath || null,
        retryAfter: err.retryAfter || null,
      });
      db.logEvent({ profileId, eventId, event: 'pull', status: 'stale', detail: JSON.stringify(detail) });
      return sendJson(res, 200, stale);
    }

    const parts = [`${err.code || 'error'}:${err.message}`];
    if (err.upstreamPath) parts.push(`endpoint=${err.upstreamPath}`);
    if (err.retryAfter) parts.push(`retry_after=${err.retryAfter}`);
    db.logEvent({ profileId, eventId, event: 'pull', status: 'error', detail: parts.join(' ') });
    throw err;
  }
}

function pullDetail(payload, extra = {}) {
  return {
    version: payload.version,
    items: payload.items?.length || 0,
    watchedMovies: payload.watched?.movies?.length ?? null,
    watchedEpisodes: payload.watched?.episodes?.length ?? null,
    watchedNextUp: payload.watched?.nextUp?.length ?? null,
    watchedChanged: Boolean(payload.watched),
    watchlistItems: payload.watchlist?.length ?? null,
    watchlistChanged: Array.isArray(payload.watchlist),
    ...extra,
  };
}

async function processPush(res, { profileId, body, db, trakt, config }) {
  if (db.isProcessed(profileId, body.id)) return noContent(res);

  const coverage = coveredByRecentBulk(db, profileId, body, config.bulkSingleDedupeSeconds);
  if (coverage) {
    const result = {
      ignored: 'covered_by_recent_bulk',
      bulkEventId: coverage.bulkEventId,
      videoId: coverage.videoId,
      deltaSeconds: coverage.deltaSeconds,
    };
    db.markProcessed(profileId, body.id, JSON.stringify(result));
    db.logEvent({ profileId, eventId: body.id, event: body.event, status: 'ignored', detail: JSON.stringify(result) });
    return noContent(res);
  }

  const inflightKey = `${profileId}:${body.id}`;
  if (inflight.has(inflightKey)) {
    await inflight.get(inflightKey);
    return noContent(res);
  }

  const task = (async () => {
    try {
      const plan = planEvent(body);
      const result = plan.kind === 'ignore' ? { ignored: plan.reason } : await trakt.applyEvent(profileId, body, plan);
      if (result?.action === 'history:bulk-add' || result?.action === 'history:bulk-remove') {
        rememberBulkCoverage(db, profileId, body, config.bulkSingleDedupeSeconds);
      }
      if (invalidatesPullCache(result)) clearPullCache(db, profileId);
      db.markProcessed(profileId, body.id, JSON.stringify(result));
      db.logEvent({ profileId, eventId: body.id, event: body.event, status: result.ignored ? 'ignored' : 'ok', detail: JSON.stringify(result) });
    } catch (err) {
      const parts = [`${err.code || 'error'}:${err.message}`];
      if (err.upstreamPath) parts.push(`endpoint=${err.upstreamPath}`);
      if (err.retryAfter) parts.push(`retry_after=${err.retryAfter}`);
      db.logEvent({ profileId, eventId: body.id, event: body.event, status: 'error', detail: parts.join(' ') });
      throw err;
    }
  })().finally(() => inflight.delete(inflightKey));
  inflight.set(inflightKey, task);
  await task;
  return noContent(res);
}

export function invalidatesPullCache(result) {
  if (result?.identityAlias) return true;
  return ['history:add', 'history:remove', 'history:bulk-add', 'history:bulk-remove', 'watchlist:add', 'watchlist:remove', 'scrobble:stop'].includes(result?.action);
}

function requireAdmin(url, config) {
  const key = url.searchParams.get('key');
  if (!key || !safeEqual(key, config.adminKey)) throw new BridgeError('Not found', { status: 404, code: 'not_found' });
}

function requireSetupKey(url, config, db, profileId) {
  const profile = db.getProfile(profileId);
  if (!profile) throw new BridgeError('Not found', { status: 404, code: 'not_found' });
  const expected = deriveProfileKey(config.bridgeSecret, 'setup', profileId);
  if (!safeEqual(url.searchParams.get('key') || '', expected)) throw new BridgeError('Not found', { status: 404, code: 'not_found' });
}

function requireAddonKey(config, db, profileId, candidate) {
  if (!db.getProfile(profileId)) throw new BridgeError('Not found', { status: 404, code: 'not_found' });
  const expected = deriveProfileKey(config.bridgeSecret, 'addon', profileId);
  if (!safeEqual(candidate, expected)) throw new BridgeError('Not found', { status: 404, code: 'not_found' });
}

function renderAdmin(res, { config, db }, adminKey) {
  const profiles = db.listProfiles();
  const rows = profiles.map((p) => {
    const setupKey = deriveProfileKey(config.bridgeSecret, 'setup', p.id);
    return `<tr><td>${escapeHtml(p.name)}</td><td>${p.connected_at ? '<span class="ok">Connected</span>' : '<span class="bad">Not connected</span>'}</td><td><a class="btn secondary" href="/u/${p.id}/setup?key=${encodeURIComponent(setupKey)}">Open</a></td></tr>`;
  }).join('') || '<tr><td colspan="3" class="muted">No profiles yet.</td></tr>';
  return sendHtml(res, 200, page('Bridge setup', `
    <div class="card"><h1>${APP_NAME}</h1><p class="muted">v${APP_VERSION}</p></div>
    <div class="card"><h2>Profiles</h2><table><thead><tr><th>Name</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="card"><h2>Create profile</h2><form method="post" action="/setup?key=${encodeURIComponent(adminKey)}"><label>Name</label><input name="name" value="Trakt" maxlength="80"><button class="btn" type="submit">Create profile</button></form></div>
  `));
}

function renderProfile(res, { config, db }, profileId, setupKey) {
  const p = db.getProfile(profileId);
  const addonKey = deriveProfileKey(config.bridgeSecret, 'addon', profileId);
  const manifestUrl = `${config.publicBaseUrl}/u/${profileId}/${addonKey}/manifest.json`;
  const recent = summarizeRecentEvents(db.recentEvents(profileId, 60), 16);
  const events = recent.map((e) => {
    const statusClass = e.displayStatus === 'retrying' ? 'bad' : 'ok';
    const retryText = e.attempts > 1 ? ` · ${e.attempts} attempts` : '';
    return `<tr><td>${escapeHtml(formatEventTime(e.created_at, config.displayTimeZone))}</td><td>${escapeHtml(e.event || '')}<br><small class="muted">${escapeHtml(e.shortEventId || '')}</small></td><td><span class="${statusClass}">${escapeHtml(e.displayStatus)}</span><small class="muted">${escapeHtml(retryText)}</small></td><td><small>${escapeHtml(e.detail || '')}</small></td></tr>`;
  }).join('') || '<tr><td colspan="4" class="muted">No events yet.</td></tr>';
  const connected = Boolean(p.access_token_enc);
  return sendHtml(res, 200, page(`${p.name} - Trakt Bridge`, `
    <div class="card"><h1>${escapeHtml(p.name)}</h1><p>Status: <span class="status ${connected ? 'ok' : 'bad'}">${connected ? 'Trakt connected' : 'Not connected'}</span></p><p class="muted">AIOStreams Watch State v2 · bidirectional push + pull · Display time: ${escapeHtml(config.displayTimeZone)}</p>
      <div class="row"><a class="btn" href="/u/${profileId}/oauth/start?key=${encodeURIComponent(setupKey)}">${connected ? 'Reconnect Trakt' : 'Connect Trakt'}</a>${connected ? `<form method="post" action="/u/${profileId}/disconnect?key=${encodeURIComponent(setupKey)}"><button class="btn bad" type="submit">Disconnect</button></form>` : ''}</div>
    </div>
    <div class="card"><h2>AIOStreams manifest</h2><p class="url"><code>${escapeHtml(manifestUrl)}</code></p><p class="muted">The manifest URL is a credential. Pull identity mode: <code>${escapeHtml(config.pullIdentityMode)}</code>. In <code>trakt</code> mode the bridge preserves Trakt's IMDb spelling on pull, allowing native Trakt clients and AIOStreams to converge on the same identity.</p></div>
    <div class="card"><h2>v${APP_VERSION} capabilities</h2><p><strong>Push:</strong> <code>start</code> · <code>pause</code> · <code>stop</code> · <code>played</code> · <code>unplayed</code> · <code>watchlisted</code> · <code>unwatchlisted</code> · <code>bulk=true</code></p><p><strong>Pull:</strong> <code>items</code> · <code>watched</code> · <code>watchlist</code> · configurable pull identity (<code>${escapeHtml(config.pullIdentityMode)}</code>) · alias-aware counts · safe next-up hints when supplied upstream · cache TTL ${escapeHtml(config.pullTtlSeconds)}s</p><p class="muted">IMDb/TMDb/TVDb resolution remains deterministic. A successful AIOStreams episode <code>stop</code> may persist a profile-scoped Trakt-show → preferred AIOStreams IMDb alias; if that changes identity state, the pull cache is invalidated immediately. Bulk single-echo suppression remains ${escapeHtml(config.bulkSingleDedupeSeconds)}s.</p></div>
    <div class="card"><h2>Recent events</h2><table><thead><tr><th>Time</th><th>Event / ID</th><th>Status</th><th>Detail</th></tr></thead><tbody>${events}</tbody></table></div>
  `));
}

function securityHeaders(res) {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Cache-Control', 'no-store');
}

function sendJson(res, status, value) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(value));
}
function sendHtml(res, status, value) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(value);
}
function noContent(res) { res.statusCode = 204; res.end(); }
function redirect(res, location) { res.statusCode = 302; res.setHeader('Location', location); res.end(); }
function methodNotAllowed(res) { sendJson(res, 405, { error: 'method_not_allowed' }); }

async function readJson(req, maxBytes) {
  const buf = await readBody(req, maxBytes);
  try { return JSON.parse(buf.toString('utf8')); }
  catch { throw new BridgeError('Invalid JSON', { status: 400, code: 'invalid_json' }); }
}
async function readForm(req) {
  const buf = await readBody(req, 32 * 1024);
  return new URLSearchParams(buf.toString('utf8'));
}
async function readBody(req, maxBytes) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maxBytes) throw new BridgeError('Request body too large', { status: 413, code: 'body_too_large' });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
