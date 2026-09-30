import http from 'node:http';
import { randomBytes } from 'node:crypto';
import { APP_NAME, APP_VERSION } from './config.mjs';
import { deriveProfileKey, randomToken, safeEqual } from './crypto.mjs';
import { BridgeError } from './errors.mjs';
import { page, escapeHtml } from './html.mjs';
import { buildManifest, planEvent, validatePushEvent } from './watch-state.mjs';

const inflight = new Map();

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
      console.error(JSON.stringify({ level: 'error', status: e.status, code: e.code, message: e.message, ms: Date.now() - started }));
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
    return sendHtml(res, 200, page(APP_NAME, `<div class="card"><h1>${APP_NAME}</h1><p>Self-hosted AIOStreams <code>watch_state v2</code> → Trakt bridge.</p><p class="muted">v${APP_VERSION}</p></div>`));
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
    redirect(res, `/u/${profileId}/setup?key=${encodeURIComponent(url.searchParams.get('key'))}`);
    return;
  }

  const manifestMatch = path.match(/^\/u\/([a-f0-9]{24})\/([A-Za-z0-9_-]{30,})\/manifest\.json$/);
  if (manifestMatch) {
    const [, profileId, addonKey] = manifestMatch;
    requireAddonKey(config, db, profileId, addonKey);
    if (req.method !== 'GET') return methodNotAllowed(res);
    return sendJson(res, 200, buildManifest(profileId));
  }

  const pushMatch = path.match(/^\/u\/([a-f0-9]{24})\/([A-Za-z0-9_-]{30,})\/watch_state\/push\/(movie|series)\/(.+)\.json$/);
  if (pushMatch) {
    const [, profileId, addonKey] = pushMatch;
    requireAddonKey(config, db, profileId, addonKey);
    if (req.method !== 'POST') return methodNotAllowed(res);
    const profile = db.getProfile(profileId);
    if (!profile?.access_token_enc) throw new BridgeError('Trakt account is not connected', { status: 401, code: 'not_connected' });
    const body = validatePushEvent(await readJson(req, 128 * 1024));
    return processPush(res, { profileId, body, db, trakt });
  }

  sendJson(res, 404, { error: 'not_found' });
}

async function processPush(res, { profileId, body, db, trakt }) {
  if (db.isProcessed(profileId, body.id)) return noContent(res);
  const inflightKey = `${profileId}:${body.id}`;
  if (inflight.has(inflightKey)) {
    await inflight.get(inflightKey);
    return noContent(res);
  }

  const task = (async () => {
    try {
      const plan = planEvent(body);
      const result = plan.kind === 'ignore' ? { ignored: plan.reason } : await trakt.applyEvent(profileId, body, plan);
      db.markProcessed(profileId, body.id, JSON.stringify(result));
      db.logEvent({ profileId, eventId: body.id, event: body.event, status: result.ignored ? 'ignored' : 'ok', detail: JSON.stringify(result) });
    } catch (err) {
      db.logEvent({ profileId, eventId: body.id, event: body.event, status: 'error', detail: `${err.code || 'error'}:${err.message}` });
      throw err;
    }
  })().finally(() => inflight.delete(inflightKey));
  inflight.set(inflightKey, task);
  await task;
  return noContent(res);
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
  const events = db.recentEvents(profileId, 12).map((e) => `<tr><td>${new Date(Number(e.created_at)*1000).toLocaleString()}</td><td>${escapeHtml(e.event || '')}</td><td>${escapeHtml(e.status)}</td><td><small>${escapeHtml(e.detail || '')}</small></td></tr>`).join('') || '<tr><td colspan="4" class="muted">No events yet.</td></tr>';
  const connected = Boolean(p.access_token_enc);
  return sendHtml(res, 200, page(`${p.name} - Trakt Bridge`, `
    <div class="card"><h1>${escapeHtml(p.name)}</h1><p>Status: <span class="status ${connected ? 'ok' : 'bad'}">${connected ? 'Trakt connected' : 'Not connected'}</span></p><p class="muted">AIOStreams Watch State v2 · push-only MVP</p>
      <div class="row"><a class="btn" href="/u/${profileId}/oauth/start?key=${encodeURIComponent(setupKey)}">${connected ? 'Reconnect Trakt' : 'Connect Trakt'}</a>${connected ? `<form method="post" action="/u/${profileId}/disconnect?key=${encodeURIComponent(setupKey)}"><button class="btn bad" type="submit">Disconnect</button></form>` : ''}</div>
    </div>
    <div class="card"><h2>AIOStreams manifest</h2><p class="url"><code>${escapeHtml(manifestUrl)}</code></p><p class="muted">Add this URL as a custom addon in AIOStreams. The URL is a credential; do not publish it.</p></div>
    <div class="card"><h2>v0.1 capabilities</h2><p><code>start</code> · <code>pause</code> · <code>stop</code> · <code>played</code> · <code>unplayed</code></p><p class="muted">Pull/history import and watchlist sync are intentionally not advertised yet.</p></div>
    <div class="card"><h2>Recent events</h2><table><thead><tr><th>Time</th><th>Event</th><th>Status</th><th>Detail</th></tr></thead><tbody>${events}</tbody></table></div>
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
