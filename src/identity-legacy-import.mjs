/**
 * Explicit opt-in, one-time RC6 -> v1.3.1 registry migration.
 *
 * Identity evidence comes exclusively from this Bridge's *own* legacy
 * identity-alias:v1:<exact-profile> cache, originally learned on a successful
 * authenticated Trakt scrobble. This migration does NOT read/write AIO,
 * perform any Trakt HTTP calls, or delete/edit the old cache.
 *
 * dryRun=true is the default. A complete, conflict-free snapshot is persisted
 * as ONE new media_cache value only after all checks succeed.
 */
const LEGACY_PREFIX = 'identity-alias:v1:';
const GRAPH_PREFIX = 'identity-graph:v1:';
const MAX_SHOWS = 10000;
const TTL = 10 * 365 * 24 * 3600;
const imdb = (x) => typeof x === 'string' && /^tt\d{7,12}$/.test(x.toLowerCase())
  ? x.toLowerCase() : null;
const traktId = (x) => {
  if (!/^\d+$/.test(String(x ?? ''))) return null;
  const id = Number(x);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
};
const validProfile = (x) => typeof x === 'string'
  && /^[a-f0-9]{24}$/.test(x);

function result(status, opts = {}) {
  return { status, ...opts };
}

/**
 * All-or-nothing migration. Existing populated graph is never overwritten;
 * profile must exist + be connected. This is called from Bridge startup, not
 * the request path, and only with both gateway/import flags opted in.
 */
export function importLegacyIdentityAliases(db, profileId, {
  dryRun = true,
} = {}) {
  if (!validProfile(profileId)) {
    return result('INVALID_PROFILE_ID', { changed: false });
  }
  if (typeof db?.getProfile !== 'function') {
    return result('NO_PROFILE_VERIFIER', { changed: false });
  }
  const profile = db.getProfile(profileId);
  if (!profile || !profile.access_token_enc) {
    return result('PROFILE_NOT_CONNECTED', { changed: false });
  }
  if (typeof db?.cachePeek !== 'function' || typeof db?.cacheInsertIfAbsent !== 'function') {
    return result('CACHE_UNAVAILABLE', { changed: false });
  }

  const existing = db.cachePeek(GRAPH_PREFIX + profileId);
  if (existing !== null && existing !== undefined) {
    return result('GRAPH_ALREADY_EXISTS', { changed: false });
  }

  const legacy = db.cachePeek(LEGACY_PREFIX + profileId);
  if (!legacy || typeof legacy !== 'object' || Array.isArray(legacy)
      || !legacy.shows || typeof legacy.shows !== 'object'
      || Array.isArray(legacy.shows)) {
    return result('NO_VALID_LEGACY_DATA', { changed: false });
  }
  const entries = Object.entries(legacy.shows);
  if (!entries.length || entries.length > MAX_SHOWS) {
    return result('EMPTY_OR_UNBOUNDED_LEGACY', { changed: false });
  }

  // Fail the whole import if a single row is invalid, incomplete or conflicts.
  // Never silently drop a suspicious legacy alias to make the import "pass".
  const proposed = {};
  const claims = new Map();
  let aliasedShows = 0;
  for (const [rawTrakt, row] of entries) {
    const id = traktId(rawTrakt);
    const canonical = imdb(row?.traktImdb);
    const oldSpelling = imdb(row?.preferredMetaId);
    const updatedAt = Number(row?.updatedAt);
    if (!id || !canonical || !oldSpelling
        || !Number.isSafeInteger(updatedAt) || updatedAt <= 0) {
      return result('INVALID_OR_UNVERIFIED_LEGACY_ROW', { changed: false });
    }
    // This is a show-level scrobble-derived mapping; no TMDB/TVDB proof is
    // present in RC6 legacy and therefore neither field is invented.
    const aliases = canonical !== oldSpelling ? [oldSpelling] : [];
    for (const name of [canonical, ...aliases]) {
      const owner = claims.get(name);
      if (owner !== undefined && owner !== id) {
        return result('CROSS_SHOW_IDENTITY_CONFLICT', { changed: false });
      }
      claims.set(name, id);
    }
    proposed[String(id)] = {
      canonicalImdb: canonical,
      tmdb: null,
      tvdb: null,
      aliases,
    };
    if (aliases.length) aliasedShows++;
  }
  if (!aliasedShows) {
    return result('NO_DIFFERING_ALIASES', {
      changed: false, showCount: entries.length,
    });
  }

  const preview = {
    changed: false,
    showCount: entries.length,
    aliasedShows,
    source: 'rc6_successful_trakt_scrobble_identity_alias_v1',
    targetRevision: 1,
  };
  if (dryRun) return result('DRY_RUN_READY', preview);

  // The single DB insertion is atomic and must not overwrite a graph
  // from a simultaneous process. The RC6 legacy key remains untouched.
  const inserted = db.cacheInsertIfAbsent(GRAPH_PREFIX + profileId, {
    revision: 1,
    shows: proposed,
    conflicts: [],
  }, TTL);
  return inserted
    ? result('LEGACY_IMPORTED', { ...preview, changed: true })
    : result('GRAPH_CREATED_CONCURRENTLY', { changed: false });
}
