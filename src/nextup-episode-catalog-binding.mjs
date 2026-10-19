/**
 * RC3-P5 — source-independent, read-only episode catalog + logical-history
 * correlation. This is NOT a synthetic Jellyfin NextUp response.
 *
 * Input pages must come from ONE same-user SeriesId's authenticated
 * GET /Shows/{SeriesId}/Episodes?StartIndex=N&Limit=L endpoint, with a
 * separately authenticated GET /Items/{SeriesId}. Caller is responsible
 * for confirming same user across ALL calls and no unseen source mutation.
 *
 * No HTTP, Trakt, database, playback, Gateway imports, writable caches,
 * synthetic Jellyfin IDs, global NextUp total or client response.
 */
const IMDB = /^tt[0-9]{7,12}$/;
const MAX_PAGES = 6;
const MAX_LIMIT = 100;
const MAX_CATALOG = MAX_PAGES * MAX_LIMIT;
const positive = value => Number.isSafeInteger(value) && value >= 0;
const key = (season,episode) => season + ':' + episode;

function stop(reason, extra = {}) {
  return {qualified:false,reason,seriesIdBound:false,
    historyCoordinatesResolved:0,historyCoordinatesUnresolved:0,
    catalogItems:0,catalogComplete:false,playbackAvailabilityProven:false,
    clientResponseChanged:false,globalNextUpQualified:false, ...extra};
}

/** Only full, non-overlapping, coherent catalogs qualify. */
export function qualifyEpisodeCatalogPages({
  series, pages, sameUserVerified = false,
  backendContractVerified = false, expectedImdb,
} = {}) {
  if (!sameUserVerified) return stop('USER_IDENTITY_UNVERIFIED');
  if (!backendContractVerified) return stop('BACKEND_EPISODE_API_UNVERIFIED');
  if (!series || typeof series!=='object'
      || series.Type!=='Series'
      || typeof series.Id!=='string' || !series.Id || series.Id.length>512
      || typeof series.ProviderIds?.Imdb!=='string'
      || !IMDB.test(series.ProviderIds.Imdb.toLowerCase())
      || typeof expectedImdb!=='string'
      || series.ProviderIds.Imdb.toLowerCase()!==expectedImdb) {
    return stop('SERIES_IDENTITY_MISMATCH');
  }
  if(!Array.isArray(pages) || pages.length<1 || pages.length>MAX_PAGES)
    return stop('PAGE_WINDOW_UNQUALIFIED');

  const items=[], episodeIds=new Set(),coordinates=new Set();
  let total=null,complete=false,expectedOffset=0;
  let pageLimit=null;
  for (const page of pages) {
    if (!page || typeof page!=='object'
        || !positive(page.startIndex) || page.startIndex!==expectedOffset
        || !Number.isSafeInteger(page.requestedLimit)
        || page.requestedLimit<1 || page.requestedLimit>MAX_LIMIT
        || (pageLimit!==null && page.requestedLimit!==pageLimit)) {
      return stop('PAGE_REQUESTS_NOT_CONTIGUOUS');
    }
    pageLimit=page.requestedLimit;
    const response=page.response;
    if (!response || typeof response!=='object'
        || !Array.isArray(response.Items)
        || response.Items.length>pageLimit
        || response.StartIndex!==page.startIndex
        || !positive(response.TotalRecordCount)
        || response.TotalRecordCount<expectedOffset+response.Items.length
        || (response.Count!==undefined
          && response.Count!==response.Items.length)) {
      return stop('PAGE_RESPONSE_CONTRACT_UNVERIFIED');
    }
    if(total===null) total=response.TotalRecordCount;
    else if(total!==response.TotalRecordCount)
      return stop('GLOBAL_TOTAL_DRIFT');
    if(complete) return stop('UNEXPECTED_PAGE_AFTER_TERMINAL');
    for(const item of response.Items) {
      if(!item || typeof item!=='object' || Array.isArray(item)
          || item.Type!=='Episode'
          || typeof item.Id!=='string' || !item.Id || item.Id.length>512
          || item.SeriesId!==series.Id
          || !positive(item.ParentIndexNumber) || item.ParentIndexNumber>200
          || !Number.isSafeInteger(item.IndexNumber)
          || item.IndexNumber<1 || item.IndexNumber>10000) {
        return stop('CATALOG_ITEM_CONTRACT_UNVERIFIED');
      }
      const coord=key(item.ParentIndexNumber,item.IndexNumber);
      if(episodeIds.has(item.Id) || coordinates.has(coord))
        return stop('DUPLICATE_CATALOG_ID_OR_COORDINATE');
      episodeIds.add(item.Id);coordinates.add(coord);
      items.push(item);
    }
    expectedOffset+=response.Items.length;
    if(response.Items.length<pageLimit || expectedOffset===total) {
      if (expectedOffset!==total) return stop('SHORT_PAGE_BEFORE_TRUE_END');
      complete=true;
    }
  }
  if(!complete || items.length!==total || items.length>MAX_CATALOG)
    return stop('CATALOG_NOT_EXHAUSTIVELY_VERIFIED',{
      catalogItems:items.length,catalogComplete:false
    });

  return {qualified:true,reason:'EXACT_SERIES_CATALOG_ONLY',
    seriesId:series.Id,seriesImdb:expectedImdb,items,
    seriesIdBound:true,catalogComplete:true,
    catalogItems:items.length,historyCoordinatesResolved:0,
    historyCoordinatesUnresolved:0,
    playbackAvailabilityProven:false,
    clientResponseChanged:false,globalNextUpQualified:false};
}

/**
 * Match an independently verified canonical-progress read model to a real,
 * complete Episode catalog. Never rewrites the UserData on source items.
 * Exposes only exact local episode objects already returned by the backend,
 * so a later authorized adapter may verify /Items/{Id} or PlaybackInfo.
 */
export function bindCanonicalProgressToCatalog({
  progressView, catalog, expectedCanonicalImdb,
} = {}) {
  if(!progressView?.qualified || !Array.isArray(progressView.logicalEpisodes))
    return stop('PROGRESS_VIEW_UNQUALIFIED');
  if (!catalog?.qualified || !catalog.catalogComplete
      || !catalog.seriesIdBound
      || catalog.seriesImdb!==expectedCanonicalImdb
      || !Array.isArray(catalog.items))
    return stop('CATALOG_NOT_QUALIFIED');
  const coordinates=new Map();
  for(const item of catalog.items) {
    const coord=key(item.ParentIndexNumber,item.IndexNumber);
    if(coordinates.has(coord)) return stop('CATALOG_DUPLICATE_COORDINATE');
    coordinates.set(coord,item);
  }
  const bindings=[];
  for(const state of progressView.logicalEpisodes) {
    if(!state || !positive(state.season) || !Number.isSafeInteger(state.episode)
        || state.episode<1 || typeof state.played!=='boolean'
        || !positive(state.positionMs)) {
      return stop('LOGICAL_EPISODE_STATE_UNQUALIFIED');
    }
    const existing=coordinates.get(key(state.season,state.episode));
    if(!existing) return stop('HISTORY_EPISODE_NOT_IN_CATALOG',{
      historyCoordinatesResolved:bindings.length,
      historyCoordinatesUnresolved:progressView.logicalEpisodes.length-bindings.length,
      catalogItems:catalog.items.length
    });
    bindings.push({
      season:state.season,episode:state.episode,
      resolvedEpisodeId:existing.Id,
      resolvedSeriesId:existing.SeriesId,
      historicalPlayed:state.played,historicalPositionMs:state.positionMs,
      historicalSourceSpelling:state.sourceSpelling,
      catalogUserDataUnmodified:true,
    });
  }
  return {qualified:true,reason:'HISTORY_TO_CATALOG_COORDINATES_BOUND',
    seriesIdBound:true,catalogComplete:true,
    catalogItems:catalog.items.length,
    historyCoordinatesResolved:bindings.length,
    historyCoordinatesUnresolved:0,
    episodeBindings:bindings,
    playbackAvailabilityProven:false,
    clientResponseChanged:false,globalNextUpQualified:false};
}
