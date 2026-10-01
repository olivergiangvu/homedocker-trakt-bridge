import { APP_NAME, APP_VERSION } from './config.mjs';
import { SCHEMA_VERSION } from './migrations.mjs';
import { summarizeRecentEvents } from './diagnostics.mjs';

export const ACTIVE_ERROR_WINDOW_SECONDS = 30 * 60;

function parseJson(value) {
  if (!value) return null;
  try { return JSON.parse(value); }
  catch { return null; }
}

function latest(rows, predicate) {
  return (rows || []).find(predicate) || null;
}

function compactAction(action) {
  const labels = {
    'history:add': 'History added',
    'history:remove': 'History removed',
    'history:bulk-add': 'Bulk history added',
    'history:bulk-remove': 'Bulk history removed',
    'watchlist:add': 'Watchlist added',
    'watchlist:remove': 'Watchlist removed',
    'scrobble:start': 'Scrobble started',
    'scrobble:pause': 'Scrobble paused',
    'scrobble:stop': 'Scrobble stopped',
  };
  return labels[action] || action || 'Completed';
}

export function summarizeEventDetail(row) {
  const detail = parseJson(row?.detail);

  if (row?.event === 'auth' && detail?.state) {
    if (detail.state === 'connected') return 'Trakt connected';
    if (detail.state === 'reconnect_required') return 'Reconnect required';
    if (detail.state === 'disconnected') return 'Trakt disconnected';
    return `Auth · ${detail.state}`;
  }

  if (row?.event === 'pull' && detail) {
    const source = detail.source === 'cache'
      ? 'Cache'
      : detail.source === 'stale-cache'
        ? 'Stale cache'
        : detail.source === 'coalesced'
          ? 'Trakt · shared request'
          : 'Trakt';
    const parts = [`${source} · ${detail.items ?? 0} items`];
    if (detail.watchedMovies != null || detail.watchedEpisodes != null) {
      parts.push(`watched ${detail.watchedMovies ?? 0}/${detail.watchedEpisodes ?? 0}`);
    }
    if (detail.watchlistItems != null) parts.push(`watchlist ${detail.watchlistItems}`);
    if (detail.ageSeconds != null) parts.push(`age ${detail.ageSeconds}s`);
    if (detail.upstreamError) parts.push(detail.upstreamError);
    return parts.join(' · ');
  }

  if (detail?.ignored) {
    if (detail.ignored === 'progress_below_trakt_minimum') return 'Below 1% · ignored locally';
    if (detail.ignored === 'covered_by_recent_bulk') return 'Covered by recent bulk update';
    return `Ignored · ${detail.ignored}`;
  }

  if (detail?.action) return compactAction(detail.action);

  const raw = String(row?.detail || '');
  const match = raw.match(/^trakt_(\d+):.*?(?:endpoint=([^\s]+))?$/);
  if (match) return `Trakt ${match[1]}${match[2] ? ` · ${match[2]}` : ''}`;
  return raw || '—';
}

export function buildReadiness({ db }) {
  let dbOk = false;
  let schemaVersion = null;
  let connectedProfiles = 0;
  let error = null;

  try {
    dbOk = db.ping();
    schemaVersion = db.schemaVersion();
    connectedProfiles = db.countConnectedProfiles();
  } catch (err) {
    error = err?.message || String(err);
  }

  const schemaOk = schemaVersion === SCHEMA_VERSION;
  const ready = dbOk && schemaOk && connectedProfiles > 0;
  const status = ready
    ? 'ready'
    : (!dbOk || !schemaOk ? 'error' : 'setup_required');

  return {
    status,
    app: APP_NAME,
    version: APP_VERSION,
    ready,
    database: dbOk ? 'ok' : 'error',
    schemaVersion,
    schemaExpected: SCHEMA_VERSION,
    connectedProfiles,
    error,
  };
}

export function buildProfileOperationalStatus({ db, config, profileId }) {
  const profile = db.getProfile(profileId);
  if (!profile) return null;

  const rows = db.recentEvents(profileId, 250);
  const summarized = summarizeRecentEvents(rows, 250);
  const now = typeof db.now === 'function' ? db.now() : Math.floor(Date.now() / 1000);

  const lastPull = latest(rows, (row) => row.event === 'pull' && ['ok', 'cached', 'stale'].includes(row.status));
  const lastAuthoritativePull = latest(rows, (row) => {
    if (row.event !== 'pull' || row.status !== 'ok') return false;
    const detail = parseJson(row.detail);
    return Boolean(detail?.watchedChanged || detail?.watchlistChanged);
  });
  const lastError = latest(rows, (row) => row.status === 'error');
  const latestAuth = latest(rows, (row) => row.event === 'auth');
  const latestAuthDetail = parseJson(latestAuth?.detail);
  const connected = Boolean(profile.access_token_enc);
  const connectionState = connected
    ? 'connected'
    : (latestAuthDetail?.state === 'reconnect_required' ? 'reconnect_required' : 'disconnected');
  const activeErrors = summarized.filter((row) => (
    row.displayStatus === 'retrying'
    && Number(row.created_at || 0) >= now - ACTIVE_ERROR_WINDOW_SECONDS
  ));
  const recovered = summarized.filter((row) => row.displayStatus === 'recovered').length;
  const ignored = summarized.filter((row) => row.status === 'ignored').length;
  const historicalErrors = rows.filter((row) => row.status === 'error').length;

  const aliasStore = db.cacheGet(`identity-alias:v1:${profileId}`) || { revision: 0, shows: {} };
  const aliases = Object.entries(aliasStore.shows || {}).map(([traktShowId, value]) => ({
    traktShowId,
    preferredMetaId: value?.preferredMetaId || null,
    traktImdb: value?.traktImdb || null,
    effectivePullMetaId: config.pullIdentityMode === 'trakt'
      ? (value?.traktImdb || value?.preferredMetaId || null)
      : (value?.preferredMetaId || value?.traktImdb || null),
    updatedAt: value?.updatedAt || null,
  }));

  return {
    app: APP_NAME,
    version: APP_VERSION,
    schemaVersion: db.schemaVersion(),
    profile: {
      id: profile.id,
      name: profile.name,
      connected,
      connectionState,
      reconnectRequired: connectionState === 'reconnect_required',
      connectedAt: profile.connected_at || null,
    },
    authority: {
      history: 'trakt',
      surface: 'aiostreams',
      pullIdentityMode: config.pullIdentityMode,
      aiometadataReadModeRecommended: 'this_server_only',
      aiometadataRole: 'local-state + secondary-tracker write/fan-out',
    },
    sync: {
      lastPullAt: lastPull?.created_at || null,
      lastPullStatus: lastPull?.status || null,
      lastPull: parseJson(lastPull?.detail),
      lastAuthoritativePullAt: lastAuthoritativePull?.created_at || null,
      lastAuthoritativePull: parseJson(lastAuthoritativePull?.detail),
    },
    errors: {
      active: activeErrors.length,
      activeWindowSeconds: ACTIVE_ERROR_WINDOW_SECONDS,
      unresolved: activeErrors.length,
      recovered,
      ignored,
      historical: historicalErrors,
      lastErrorAt: lastError?.created_at || null,
      lastErrorEvent: lastError?.event || null,
      lastErrorDetail: lastError?.detail || null,
    },
    identity: {
      aliasRevision: Number(aliasStore.revision || 0),
      aliasCount: aliases.length,
      aliases,
    },
  };
}

export function filterOperationalEvents(rows, filter = 'all') {
  const allowed = new Set(['all', 'errors', 'pull', 'playback', 'ignored']);
  const selected = allowed.has(filter) ? filter : 'all';

  if (selected === 'all') return rows;
  if (selected === 'errors') return rows.filter((row) => row.hadError || ['retrying', 'recovered'].includes(row.displayStatus));
  if (selected === 'pull') return rows.filter((row) => row.event === 'pull');
  if (selected === 'ignored') return rows.filter((row) => row.status === 'ignored');
  return rows.filter((row) => ['start', 'pause', 'stop', 'played', 'unplayed'].includes(row.event));
}
