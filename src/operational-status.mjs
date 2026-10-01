import { APP_NAME, APP_VERSION } from './config.mjs';
import { SCHEMA_VERSION } from './migrations.mjs';
import { summarizeRecentEvents } from './diagnostics.mjs';

function parseJson(value) {
  if (!value) return null;
  try { return JSON.parse(value); }
  catch { return null; }
}

function latest(rows, predicate) {
  return (rows || []).find(predicate) || null;
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
  const lastPull = latest(rows, (row) => row.event === 'pull' && ['ok', 'cached', 'stale'].includes(row.status));
  const lastError = latest(rows, (row) => row.status === 'error');
  const unresolved = summarized.filter((row) => row.displayStatus === 'retrying').length;
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
      connected: Boolean(profile.access_token_enc),
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
    },
    errors: {
      unresolved,
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
