/**
 * Verified cross-IMDb identity evidence learned from successful Trakt scrobbles.
 *
 * This is a *registry*, not a migration or AIOStreams NextUp patch.
 * Never merge on display titles, episode numbers, TMDB coincidence alone,
 * stale resolver caches, or an unconfirmed Watch State push.
 *
 * All state is scoped to the Trakt profile via existing Bridge media_cache.
 * No AIO database access, HTTP calls, or Trakt writes happen here.
 */
const PREFIX = 'identity-graph:v1:';
const TEN_YEARS_SECONDS = 10 * 365 * 24 * 3600;
const MAX_ALIASES_PER_SHOW = 12;
const MAX_SHOWS = 10000;
const MAX_CONFLICTS = 100;

function imdb(value) {
  const id = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return /^tt\d{7,12}$/.test(id) ? id : null;
}

function positiveId(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

function key(profileId) {
  if (typeof profileId !== 'string' || !profileId || profileId.length > 256) {
    throw new TypeError('connected profile id required');
  }
  return PREFIX + profileId;
}

function blank() {
  return { revision: 0, shows: {}, conflicts: [] };
}

/** Treat persisted cache as untrusted: only import bounded, coherent rows. */
export function loadVerifiedIdentityGraph(db, profileId) {
  const raw = db.cacheGet(key(profileId));
  const out = blank();
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
  const shows = raw.shows;
  if (!shows || typeof shows !== 'object' || Array.isArray(shows)) return out;
  const entries = Object.entries(shows);
  if (entries.length > MAX_SHOWS) return out;
  for (const [traktText, value] of entries) {
    const trakt = positiveId(traktText);
    const canonicalImdb = imdb(value?.canonicalImdb);
    if (!trakt || !canonicalImdb || !Array.isArray(value.aliases)
        || value.aliases.length > MAX_ALIASES_PER_SHOW) continue;
    const aliases = [];
    for (const alias of value.aliases) {
      const id = imdb(alias);
      if (!id || id === canonicalImdb || aliases.includes(id)) continue;
      aliases.push(id);
    }
    out.shows[String(trakt)] = {
      canonicalImdb,
      tmdb: positiveId(value.tmdb),
      tvdb: positiveId(value.tvdb),
      aliases,
    };
  }
  out.revision = Number.isSafeInteger(raw.revision) && raw.revision >= 0
    ? raw.revision : 0;
  if (Array.isArray(raw.conflicts)) {
    out.conflicts = raw.conflicts.slice(-MAX_CONFLICTS)
      .filter((x) => x && typeof x === 'object'
        && imdb(x.id) && typeof x.reason === 'string')
      .map((x) => ({
        id: imdb(x.id),
        reason: String(x.reason).slice(0, 64),
        observedShow: positiveId(x.observedShow),
      }));
  }
  return out;
}

/** Only positive completed Trakt identity evidence, not heuristics. */
export function observeVerifiedShowIdentity(db, profileId, showIds, rawMetaId) {
  const trakt = positiveId(showIds?.trakt);
  const canonicalImdb = imdb(showIds?.imdb);
  const observedId = imdb(rawMetaId);
  if (!trakt || !canonicalImdb || !observedId) {
    return { changed: false, status: 'NO_VERIFIED_CROSS_IMDB_EVIDENCE' };
  }

  const current = loadVerifiedIdentityGraph(db, profileId);
  const sid = String(trakt);
  const previous = current.shows[sid];
  const tmdb = positiveId(showIds?.tmdb);
  const tvdb = positiveId(showIds?.tvdb);

  let conflict = null;
  if (previous?.canonicalImdb && previous.canonicalImdb !== canonicalImdb) {
    conflict = 'canonical_imdb_changed';
  }
  if (previous?.tmdb && tmdb && previous.tmdb !== tmdb) {
    conflict = 'tmdb_disagreement';
  }
  if (previous?.tvdb && tvdb && previous.tvdb !== tvdb) {
    conflict = 'tvdb_disagreement';
  }

  // Across all *other* Trakt shows, neither the canonical IMDb nor the raw
  // local IMDb may already be claimed. No transitive/fuzzy title merge.
  for (const [other, row] of Object.entries(current.shows)) {
    if (other === sid) continue;
    const ids = [row.canonicalImdb, ...row.aliases];
    if (ids.includes(observedId) || ids.includes(canonicalImdb)) {
      conflict = 'imdb_claimed_by_different_trakt_show';
      break;
    }
  }
  // Previously conflicted IDs must stay quarantined. Repeating the
  // same request must be idempotent and cannot create conflict spam.
  if (current.conflicts.some((x) => x.id === observedId || x.id === canonicalImdb)) {
    return { changed: false, status: 'QUARANTINED', reason: 'previously_quarantined_id' };
  }

  if (conflict) {
    const recorded = current.conflicts.some((x) =>
      x.id === observedId && x.reason === conflict && x.observedShow === trakt);
    if (!recorded) {
      current.conflicts = [
        ...current.conflicts,
        { id: observedId, reason: conflict, observedShow: trakt },
      ].slice(-MAX_CONFLICTS);
      current.revision++;
      db.cacheSet(key(profileId), current, TEN_YEARS_SECONDS);
    }
    return { changed: !recorded, status: 'QUARANTINED', reason: conflict };
  }

  if (!previous && Object.keys(current.shows).length >= MAX_SHOWS) {
    return { changed: false, status: 'CAPACITY_REVIEW_REQUIRED' };
  }
  const aliases = previous ? [...previous.aliases] : [];
  if (observedId !== canonicalImdb && !aliases.includes(observedId)) {
    if (aliases.length >= MAX_ALIASES_PER_SHOW) {
      return { changed: false, status: 'CAPACITY_REVIEW_REQUIRED' };
    }
    aliases.push(observedId);
  }

  const row = {
    canonicalImdb,
    tmdb: previous?.tmdb ?? tmdb,
    tvdb: previous?.tvdb ?? tvdb,
    aliases: aliases.sort(),
  };
  if (previous && JSON.stringify(previous) === JSON.stringify(row)) {
    return {
      changed: false,
      status: 'VERIFIED_UNCHANGED',
      traktShowId: trakt,
      canonicalImdb,
      aliasCount: aliases.length,
    };
  }
  current.shows[sid] = row;
  current.revision++;
  db.cacheSet(key(profileId), current, TEN_YEARS_SECONDS);
  return {
    changed: true,
    status: 'VERIFIED_STORED',
    traktShowId: trakt,
    canonicalImdb,
    aliasCount: aliases.length,
    revision: current.revision,
  };
}

/** For tests and future AIO interop: never expose a conflicted equivalence. */
export function verifiedCanonicalForImdb(graph, rawImdb) {
  const id = imdb(rawImdb);
  if (!id || graph?.conflicts?.some((x) => x.id === id)) return null;
  const matches = Object.entries(graph?.shows ?? {})
    .filter(([, row]) => row.canonicalImdb === id || row.aliases.includes(id));
  if (matches.length !== 1) return null;
  return {
    traktShowId: Number(matches[0][0]),
    canonicalImdb: matches[0][1].canonicalImdb,
    verified: true,
  };
}

/**
 * A bounded, versioned, evidence-backed *proposal* for an optional future AIO
 * identity equivalence contract. AIO currently DOES NOT consume this payload.
 * Do not automatically send it as watch-state dropped/alias metadata.
 */
export function identityEquivalenceProposal(graph) {
  const quarantined = new Set((graph?.conflicts ?? []).map((x) => x.id));
  const quarantinedShows = new Set((graph?.conflicts ?? [])
    .map((x) => String(x.observedShow)));
  const claims = new Map();
  for (const [trakt, row] of Object.entries(graph?.shows ?? {})) {
    for (const id of [row.canonicalImdb, ...row.aliases]) {
      if (!claims.has(id)) claims.set(id, new Set());
      claims.get(id).add(trakt);
    }
  }
  const identities = Object.entries(graph?.shows ?? {})
    .filter(([showId, row]) => !quarantinedShows.has(showId)
      && ![row.canonicalImdb, ...row.aliases]
      .some((id) => quarantined.has(id) || claims.get(id)?.size !== 1))
    .map(([trakt, row]) => ({
      type: 'series',
      traktShowId: Number(trakt),
      canonicalImdb: row.canonicalImdb,
      imdbAliases: [...row.aliases],
      ...(row.tmdb ? { tmdb: row.tmdb } : {}),
      ...(row.tvdb ? { tvdb: row.tvdb } : {}),
      proof: 'successful_trakt_scrobble_response',
    }))
    .filter((x) => x.imdbAliases.length)
    .sort((a, b) => a.traktShowId - b.traktShowId);
  return {
    contract: 'homedocker-identity-equivalence-proposal-v1',
    status: 'NOT_SUPPORTED_BY_AIO_UPSTREAM_YET',
    revision: graph?.revision ?? 0,
    identities,
    conflictCount: graph?.conflicts?.length ?? 0,
  };
}
