/**
 * RC3/P3 — PURE, BACKEND-AGNOSTIC CANONICAL PROGRESS READ MODEL.
 *
 * No production imports, no database, filesystem, network, HTTP routes or
 * Trakt writers. A source adapter must supply exact user-scoped rows from
 * its own supported read API, and the Bridge verified identity graph.
 *
 * This module is NOT a Jellyfin NextUp item/catalog generator. An episode
 * coordinate/progress value is not proof of an available playable item.
 * Never use returned logicalEpisodes to synthesize upstream Items, IDs,
 * TotalRecordCount, pagination or watch-history mutations.
 */
const IMDB = /^tt[0-9]{7,12}$/;
const MAX_ROWS = 5000;
const MAX_SPELLINGS = 13;
const MAX_SHOWS = 10000;

function validRow(row) {
  return row && !Array.isArray(row) && typeof row === 'object'
    && typeof row.imdb === 'string' && IMDB.test(row.imdb)
    && Number.isSafeInteger(row.season) && row.season >= 0 && row.season <= 200
    && Number.isSafeInteger(row.episode) && row.episode >= 1 && row.episode <= 10000
    && typeof row.played === 'boolean'
    && Number.isSafeInteger(row.positionMs) && row.positionMs >= 0;
}
function empty(reason) {
  return {
    qualified: false, reason, logicalEpisodes: [],
    metrics: { inputRows: 0, canonicalEpisodes: 0, aliasEpisodes: 0,
      overlapEpisodes: 0, canonicalOnlyEpisodes: 0, aliasOnlyEpisodes: 0,
      conflicts: 0, activeResumes: 0, aliasesPreserved: 0 },
    sourceDataChanged: false, clientResponseChanged: false,
  };
}
const positiveKey = key => /^[1-9][0-9]{0,15}$/.test(key);

/**
 * Resolve ONE verified Trakt show and spelling set, without inference from
 * titles, similar IMDb IDs or unverified TVDB/TMDB collisions.
 */
export function verifiedSpellingSet(graph, traktShowId) {
  const id = String(traktShowId ?? '');
  if (!positiveKey(id) || !graph || typeof graph !== 'object'
      || Array.isArray(graph) || !graph.shows || typeof graph.shows !== 'object'
      || Array.isArray(graph.shows) || Object.keys(graph.shows).length > MAX_SHOWS
      || !Array.isArray(graph.conflicts ?? [])) return null;
  const show = graph.shows[id];
  if (!show || typeof show !== 'object' || Array.isArray(show)
      || !IMDB.test(show.canonicalImdb)
      || !Array.isArray(show.aliases)
      || show.aliases.length < 1 || show.aliases.length >= MAX_SPELLINGS) return null;
  const spellings = [show.canonicalImdb, ...show.aliases];
  if (spellings.some(x => typeof x !== 'string' || !IMDB.test(x))
      || new Set(spellings).size !== spellings.length) return null;
  const conflicts = new Set(
    graph.conflicts.filter(x => x && typeof x.id === 'string').map(x => x.id)
  );
  if (spellings.some(x => conflicts.has(x))) return null;
  for (const [otherId, other] of Object.entries(graph.shows)) {
    if (otherId === id) continue;
    if (!other || typeof other !== 'object'
        || !Array.isArray(other.aliases)) return null;
    const otherSpelling = [other.canonicalImdb, ...other.aliases];
    if (otherSpelling.some(x => spellings.includes(x))) return null;
  }
  return spellings;
}

function candidate(row) {
  return {
    played: row.played,
    positionMs: row.positionMs,
    activeResume: !row.played && row.positionMs > 0,
  };
}

/**
 * Produce only an internal logical view, with no generated playback ID.
 * Input rows are validated, bounded, user-scoped by the caller, and
 * *already* mapped to one exact verified Trakt show.
 *
 * Ambiguous episode-coordinates, played disagreements and two concurrent
 * active resumes reject the entire model. No automatic winner/merge is
 * forced on conflict.
 */
export function buildCanonicalProgressView({graph, traktShowId, rows} = {}) {
  const spellings = verifiedSpellingSet(graph, traktShowId);
  if (!spellings) return empty('GRAPH_NOT_VERIFIED');
  if (!Array.isArray(rows) || rows.length > MAX_ROWS
      || rows.some(r => !validRow(r) || !spellings.includes(r.imdb))) {
    return empty('ROWS_NOT_VERIFIED');
  }
  const coords = new Map();
  const duplicateExact = new Set();
  for (const row of rows) {
    const key = row.season + ':' + row.episode;
    const bySpelling = coords.get(key) ?? new Map();
    if (bySpelling.has(row.imdb)) duplicateExact.add(key);
    bySpelling.set(row.imdb, row);
    coords.set(key, bySpelling);
  }
  if (duplicateExact.size) return empty('DUPLICATE_EPISODE_COORDINATE');

  const logicalEpisodes = [];
  const metrics = {
    inputRows: rows.length, canonicalEpisodes: 0, aliasEpisodes: 0,
    overlapEpisodes: 0, canonicalOnlyEpisodes: 0, aliasOnlyEpisodes: 0,
    conflicts: 0, activeResumes: 0, aliasesPreserved: 0,
  };
  const ordered = [...coords.entries()].sort((a,b) => {
    const [as,ae] = a[0].split(':').map(Number);
    const [bs,be] = b[0].split(':').map(Number);
    return as-bs || ae-be;
  });
  for (const [coord, bySpelling] of ordered) {
    const [season, episode] = coord.split(':').map(Number);
    const canonical = bySpelling.get(spellings[0]);
    const aliases = spellings.slice(1).flatMap(x => bySpelling.has(x) ? [bySpelling.get(x)] : []);
    const variants = [canonical, ...aliases].filter(Boolean);
    if (canonical) metrics.canonicalEpisodes++;
    if (aliases.length) metrics.aliasEpisodes++;
    if (canonical && aliases.length) metrics.overlapEpisodes++;
    else if (canonical) metrics.canonicalOnlyEpisodes++;
    else metrics.aliasOnlyEpisodes++;
    if (variants.length > 2) {
      metrics.conflicts++;
      return empty('AMBIGUOUS_THREE_WAY_EPISODE');
    }
    const snapshots = variants.map(candidate);
    const statuses = new Set(snapshots.map(x => x.played));
    if (statuses.size !== 1 || snapshots.filter(x => x.activeResume).length > 1
        || snapshots.some(x => x.played && x.positionMs > 0)) {
      metrics.conflicts++;
      return empty('PLAYBACK_STATE_CONFLICT');
    }
    const active = variants.find(x => !x.played && x.positionMs > 0);
    const selected = active ?? canonical ?? variants[0];
    if (active) metrics.activeResumes++;
    if (aliases.length) metrics.aliasesPreserved += aliases.length;
    logicalEpisodes.push({
      season, episode, played: selected.played,
      positionMs: selected.played ? 0 : selected.positionMs,
      activeResume: !!active,
      sourceSpelling: selected.imdb === spellings[0] ? 'canonical' : 'alias',
      hasCanonicalEvidence: Boolean(canonical),
      hasAliasEvidence: aliases.length > 0,
      // No SeriesId, EpisodeId, URL, or global NextUp count is produced.
    });
  }
  return {
    qualified: true,
    reason: 'VERIFIED_LOCAL_LOGICAL_VIEW_ONLY',
    logicalEpisodes,
    metrics,
    sourceDataChanged: false,
    clientResponseChanged: false,
    playableItemQualified: false,
    globalPaginationQualified: false,
  };
}
