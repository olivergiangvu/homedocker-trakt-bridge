/**
 * Proof-of-concept *read projection only* for a future AIOStreams upstream PR.
 *
 * This module is deliberately OUTSIDE src/ and is never bundled in the
 * published Bridge image. It does not alter AIO or Bridge databases.
 * AIOStreams upstream has not adopted the identity equivalence protocol.
 *
 * A single logical series groups verified IMDb aliases for ONE user/persona.
 * Each original watch-state row remains intact; only an in-memory shelf anchor
 * is chosen. Metadata and "NextUp" episode calculation remain AIO-owned.
 */
const IMDB_SERIES_KEY = /^s\|(tt\d{7,12})$/;
const EPS = 2_000; // Only same-time mutually inconsistent claims are ambiguous.

const isSafeMillis = (v) => Number.isSafeInteger(v) && v >= 0;
const isNatural = (v) => Number.isSafeInteger(v) && v >= 0;

function displayAnchor(row) {
  return {
    sourceSeriesKey: row.seriesKey,
    season: row.season,
    episode: row.episode,
    played: row.played,
    positionMs: row.positionMs,
    sortAt: row.sortAt,
  };
}

function better(a, b, canonicalId) {
  if (a.sortAt !== b.sortAt) return a.sortAt > b.sortAt ? a : b;
  if (a.seriesKey === 's|' + canonicalId) return a;
  if (b.seriesKey === 's|' + canonicalId) return b;
  if (a.season !== b.season) return a.season > b.season ? a : b;
  if (a.episode !== b.episode) return a.episode > b.episode ? a : b;
  return a.seriesKey < b.seriesKey ? a : b;
}

export function projectIdentityNextUpRows({
  scope,
  rows,
  equivalences,
}) {
  if (typeof scope?.uuid !== 'string' || !scope.uuid
      || typeof scope?.persona !== 'string') {
    throw new TypeError('exact AIO user/persona scope required');
  }
  if (!Array.isArray(rows) || rows.length > 10_000 || !Array.isArray(equivalences)) {
    throw new TypeError('bounded watch-state rows and equivalences required');
  }
  if (equivalences.length > 2_000) throw new RangeError('equivalence cap exceeded');

  const byAlias = new Map();
  const quarantined = new Set();
  for (const e of equivalences) {
    if (e?.type !== 'series'
        || !Number.isSafeInteger(e.traktShowId) || e.traktShowId <= 0
        || !/^tt\d{7,12}$/.test(e.canonicalImdb || '')
        || !Array.isArray(e.imdbAliases) || !e.imdbAliases.length
        || e.imdbAliases.length > 12
        || e.proof !== 'successful_trakt_scrobble_response') {
      continue;
    }
    const ids = [e.canonicalImdb, ...e.imdbAliases];
    if (ids.some((id) => !/^tt\d{7,12}$/.test(id) || id === '')) continue;
    for (const id of ids) {
      const previous = byAlias.get(id);
      if (previous && previous.traktShowId !== e.traktShowId) {
        quarantined.add(id);
      }
      byAlias.set(id, e);
    }
  }

  // If even one member of a claimed equivalence is ambiguous, never merge
  // any of that component. This must be decided BEFORE selecting a shelf.
  const blockedShows = new Set();
  for (const id of quarantined) {
    for (const e of equivalences) {
      if ([e.canonicalImdb, ...(e.imdbAliases || [])].includes(id)) {
        blockedShows.add(e.traktShowId);
      }
    }
  }

  const grouped = new Map();
  for (const row of rows) {
    if (row.uuid !== scope.uuid || row.persona !== scope.persona) continue;
    const id = IMDB_SERIES_KEY.exec(row.seriesKey || '')?.[1];
    if (!id || row.kind !== 'episode' || !isNatural(row.season)
        || !Number.isSafeInteger(row.episode) || row.episode < 1
        || typeof row.played !== 'boolean'
        || !isSafeMillis(row.positionMs) || !isSafeMillis(row.sortAt)) continue;
    const e = byAlias.get(id);
    const safe = e && !blockedShows.has(e.traktShowId)
      && !quarantined.has(id);
    const canonicalImdb = safe ? e.canonicalImdb : id;
    const groupKey = safe ? 'trakt|' + e.traktShowId : 'raw|' + id;
    let group = grouped.get(groupKey);
    if (!group) {
      group = {
        groupKey, canonicalImdb, status: safe ? 'VERIFIED_EQUIVALENCE' : 'RAW_SEPARATE',
        sourceSeriesKeys: new Set(),
        rowCount: 0,
        watchedCount: 0,
        resumeCount: 0,
        latest: null,
        activeResumes: [],
      };
      grouped.set(groupKey, group);
    }
    group.rowCount++;
    group.sourceSeriesKeys.add(row.seriesKey);
    if (row.played) group.watchedCount++;
    if (!row.played && row.positionMs > 0) {
      group.resumeCount++;
      group.activeResumes.push(row);
    }
    group.latest = group.latest ? better(group.latest, row, canonicalImdb) : row;
  }

  const shelves = [...grouped.values()].map((g) => {
    // Different simultaneously resumed episodes under one verified show are
    // not safe to silently discard/overwrite just to guarantee one card.
    let conflict = false;
    const active = g.activeResumes;
    for (let i = 0; i < active.length && !conflict; i++) {
      for (let j = i + 1; j < active.length; j++) {
        if (Math.abs(active[i].sortAt - active[j].sortAt) > EPS) continue;
        if (active[i].season !== active[j].season
            || active[i].episode !== active[j].episode
            || Math.abs(active[i].positionMs - active[j].positionMs) > 30_000) {
          conflict = true;
          break;
        }
      }
    }
    return {
      logicalSeriesKey: g.groupKey,
      canonicalImdb: g.canonicalImdb,
      status: conflict ? 'AMBIGUOUS_RESUMES_REVIEW' : g.status,
      sourceSeriesKeys: [...g.sourceSeriesKeys].sort(),
      originalEpisodeRows: g.rowCount,
      originalWatchedRows: g.watchedCount,
      originalResumeRows: g.resumeCount,
      // Display anchor only: AIO's own metadata/NextUp engine must decide
      // actual upcoming episode; no row is edited by this projection.
      anchor: displayAnchor(g.latest),
    };
  });
  shelves.sort((a, b) =>
    b.anchor.sortAt - a.anchor.sortAt
    || a.logicalSeriesKey.localeCompare(b.logicalSeriesKey));
  return {
    contract: 'experimental-aio-nextup-view-projection-v1',
    readOnly: true,
    shelves,
    sourceRowsInspected: rows.length,
    discardedRows: 0,
  };
}
