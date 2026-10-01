const CACHE_PREFIX = 'identity-alias:v1:';
const TEN_YEARS_SECONDS = 10 * 365 * 24 * 3600;

function normalizeImdb(value) {
  const text = String(value || '').trim().toLowerCase();
  return /^tt\d+$/.test(text) ? text : null;
}

function normalizeTraktId(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 0 ? n : null;
}

function cacheKey(profileId) {
  return `${CACHE_PREFIX}${profileId}`;
}

export function loadIdentityAliases(db, profileId) {
  const raw = db.cacheGet(cacheKey(profileId));
  const revision = Number.isSafeInteger(Number(raw?.revision)) ? Number(raw.revision) : 0;
  const shows = {};

  for (const [key, value] of Object.entries(raw?.shows || {})) {
    const traktShowId = normalizeTraktId(key);
    const preferredMetaId = normalizeImdb(value?.preferredMetaId);
    if (traktShowId == null || !preferredMetaId) continue;
    shows[String(traktShowId)] = {
      preferredMetaId,
      traktImdb: normalizeImdb(value?.traktImdb),
      updatedAt: Number.isSafeInteger(Number(value?.updatedAt)) ? Number(value.updatedAt) : 0,
    };
  }

  return { revision, shows };
}

export function identityAliasVersion(state) {
  const rows = Object.entries(state?.shows || {})
    .map(([traktShowId, value]) => [
      String(traktShowId),
      normalizeImdb(value?.preferredMetaId),
      normalizeImdb(value?.traktImdb),
    ])
    .filter(([, preferredMetaId]) => preferredMetaId)
    .sort((a, b) => Number(a[0]) - Number(b[0]));
  return JSON.stringify({ revision: Number(state?.revision || 0), shows: rows });
}

export function learnShowAlias(db, profileId, showIds, preferredMetaId) {
  const traktShowId = normalizeTraktId(showIds?.trakt);
  const preferred = normalizeImdb(preferredMetaId);
  if (traktShowId == null || !preferred) {
    return { changed: false, reason: 'unusable_identity' };
  }

  const state = loadIdentityAliases(db, profileId);
  const key = String(traktShowId);
  const previous = state.shows[key]?.preferredMetaId || null;
  const traktImdb = normalizeImdb(showIds?.imdb);

  if (previous === preferred && state.shows[key]?.traktImdb === traktImdb) {
    return {
      changed: false,
      traktShowId,
      preferredMetaId: preferred,
      traktImdb,
      revision: state.revision,
    };
  }

  const now = Math.floor(Date.now() / 1000);
  const next = {
    revision: state.revision + 1,
    shows: {
      ...state.shows,
      [key]: {
        preferredMetaId: preferred,
        traktImdb,
        updatedAt: now,
      },
    },
  };
  db.cacheSet(cacheKey(profileId), next, TEN_YEARS_SECONDS);

  return {
    changed: true,
    traktShowId,
    preferredMetaId: preferred,
    traktImdb,
    previousMetaId: previous,
    revision: next.revision,
  };
}

export function preferredShowMetaId(state, showIds) {
  const traktShowId = normalizeTraktId(showIds?.trakt);
  if (traktShowId == null) return null;
  return state?.shows?.[String(traktShowId)]?.preferredMetaId || null;
}

export function rewriteShowRow(row, state) {
  const show = row?.show;
  if (!show?.ids) return row;
  const preferred = preferredShowMetaId(state, show.ids);
  if (!preferred || show.ids.imdb === preferred) return row;
  return {
    ...row,
    show: {
      ...show,
      ids: {
        ...show.ids,
        imdb: preferred,
      },
    },
  };
}

export function rewriteShowRows(rows = [], state) {
  return rows.map((row) => rewriteShowRow(row, state));
}

export function rowsForPullIdentity(rows = [], state, mode = 'trakt') {
  if (mode === 'aiostreams') return rewriteShowRows(rows, state);
  return rows;
}
