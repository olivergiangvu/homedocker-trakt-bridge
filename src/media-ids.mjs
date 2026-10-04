function int(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 0 ? n : null;
}

function imdb(value) {
  const s = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return /^tt\d+$/.test(s) ? s : null;
}

export function idsFromMetaId(metaId) {
  const value = typeof metaId === 'string' ? metaId.trim() : '';
  const imdbId = imdb(value);
  if (imdbId) return { imdb: imdbId };

  let match = value.match(/^tmdb:(\d+)$/i);
  if (match) return { tmdb: Number(match[1]) };
  match = value.match(/^tvdb:(\d+)$/i);
  if (match) return { tvdb: Number(match[1]) };
  return {};
}

export function normalizeProviderIds(ids = {}) {
  const out = {};
  const imdbId = imdb(ids?.imdb);
  const tmdbId = int(ids?.tmdb);
  const tvdbId = int(ids?.tvdb);
  const traktId = int(ids?.trakt);
  if (imdbId) out.imdb = imdbId;
  if (tmdbId != null) out.tmdb = tmdbId;
  if (tvdbId != null) out.tvdb = tvdbId;
  if (traktId != null && traktId > 0) out.trakt = traktId;
  return out;
}

/** Explicit shared-vocabulary ids win; a representable metaId fills omissions. */
export function providerIdsForEvent(event = {}) {
  return {
    ...idsFromMetaId(event.metaId),
    ...normalizeProviderIds(event.ids || {}),
  };
}

/** Stable AIOStreams spellings in preference order. */
export function representableMetaIds(ids = {}) {
  const normalized = normalizeProviderIds(ids);
  const out = [];
  if (normalized.imdb) out.push(normalized.imdb);
  if (normalized.tmdb != null) out.push(`tmdb:${normalized.tmdb}`);
  if (normalized.tvdb != null) out.push(`tvdb:${normalized.tvdb}`);
  return out;
}

export function preferredMetaId(ids = {}) {
  return representableMetaIds(ids)[0] || null;
}
