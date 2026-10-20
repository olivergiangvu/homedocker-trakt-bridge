/**
 * RC3-L: single verified show's canonical history -> actual source Item IDs.
 *
 * Pure offline / shadow-only composition of the three existing read models.
 * The caller, not this module, must independently attest SAME mapped user,
 * exact SQLite watch_sink (UUID + persona), verified graph, stable history,
 * stable NextUp/card selection and TWO identical fully checked catalog reads.
 *
 * Never used by production Gateway. Episode IDs are already returned by the
 * authenticated backend catalog; they are not synthesized from IMDb/Trakt.
 * Does not modify UserData, watched progress, playback offsets, routes or
 * global NextUp shelf. An ID mapping is not proof that a stream will play.
 */
import {adaptAioEpisodeRows,projectSourceHistory}
  from './nextup-progress-source-adapters.mjs';
import {qualifyEpisodeCatalogPages,bindCanonicalProgressToCatalog}
  from './nextup-episode-catalog-binding.mjs';
import {verifiedSpellingSet}
  from './nextup-canonical-progress-view.mjs';

const fail = reason => ({
  qualified:false,reason,
  episodeBindings:[],logicalEpisodeCount:0,
  catalogItemCount:0,catalogOnlyCount:0,
  sourceDataChanged:false,clientResponseChanged:false,
  nextUpShelfQualified:false,globalNextUpQualified:false,
  mediaPlaybackProven:false,
});

/**
 * All evidence gates must refer to one stable mapped AIO user/profile.
 * Flags are preconditions for a trusted offline caller, NOT validation of
 * authenticated HTTP/SQLite by this pure function. No network is available.
 */
export function buildScopedAioHistoryCatalogBinding({
  graph,traktShowId,watchRows,series,pages,expectedCanonicalImdb,
  evidence,
}={}) {
  if (!evidence || typeof evidence !== 'object'
      || evidence.sameMappedUser !== true
      || evidence.exactSinkScopeVerified !== true
      || evidence.graphVerified !== true
      || evidence.graphSnapshotStable !== true
      || evidence.watchStateDoubleReadStable !== true
      || evidence.nextUpDoubleReadStable !== true
      || evidence.canonicalNextUpCardVerified !== true
      || evidence.catalogDoubleReadStable !== true
      || evidence.catalogBackendContractVerified !== true)
    return fail('SOURCE_EVIDENCE_INCOMPLETE');

  const spellings=verifiedSpellingSet(graph,traktShowId);
  if (!spellings || spellings[0]!==expectedCanonicalImdb)
    return fail('CANONICAL_GRAPH_IDENTITY_UNVERIFIED');

  const adapted=adaptAioEpisodeRows({
    rows:watchRows,
    exactSinkScopeVerified:evidence.exactSinkScopeVerified,
  });
  if (!adapted.qualified) return fail('SOURCE_HISTORY_'+adapted.reason);
  const view=projectSourceHistory({adapted,graph,traktShowId});
  if (!view.qualified) return fail('CANONICAL_VIEW_'+view.reason);

  const catalog=qualifyEpisodeCatalogPages({
    series,pages,
    sameUserVerified:evidence.sameMappedUser,
    backendContractVerified:evidence.catalogBackendContractVerified,
    expectedImdb:expectedCanonicalImdb,
  });
  if (!catalog.qualified) return fail('CATALOG_'+catalog.reason);

  const binding=bindCanonicalProgressToCatalog({
    progressView:view,catalog,expectedCanonicalImdb,
  });
  if (!binding.qualified) return fail('COORDINATE_JOIN_'+binding.reason);

  // The catalog can legitimately contain episodes lacking AIO history.
  // They are not classified as unwatched, seen elsewhere or unplayable.
  const count=binding.episodeBindings.length;
  if (count>catalog.items.length) return fail('BINDING_COUNT_INCONSISTENT');
  if (view.metrics.inputRows!==adapted.rows.length
      || count!==view.logicalEpisodes.length
      || binding.historyCoordinatesUnresolved!==0)
    return fail('HISTORY_EVIDENCE_INCONSISTENT');

  return {
    qualified:true,reason:'SCOPED_EPISODE_IDS_BOUND_READ_ONLY',
    sourceKind:'aiostreams',
    logicalEpisodeCount:count,catalogItemCount:catalog.items.length,
    catalogOnlyCount:catalog.items.length-count,
    canonicalAliasOverlap:view.metrics.overlapEpisodes,
    aliasOnlyCoordinates:view.metrics.aliasOnlyEpisodes,
    activeResumes:view.metrics.activeResumes,
    // For internal shadow comparisons only; NEVER print or serve without
    // a separate user-scoped/client-compatible playback validation.
    episodeBindings:binding.episodeBindings,
    sourceDataChanged:false,clientResponseChanged:false,
    nextUpShelfQualified:false,globalNextUpQualified:false,
    mediaPlaybackProven:false,
  };
}
