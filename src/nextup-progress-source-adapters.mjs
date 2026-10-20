/**
 * RC3-P4 — STRICT, PURE, SHADOW-ONLY ADAPTERS for source episode histories.
 *
 * Not imported in the production Gateway, does not create playable Items,
 * does not map unrelated identities based on titles, and never writes
 * AIO/Trakt/Jellyfin. A NextUp shelf is NOT an episode history.
 */
import { buildCanonicalProgressView } from './nextup-canonical-progress-view.mjs';

const MAX_EPISODES = 5000;
const IMDB = /^tt[0-9]{7,12}$/;
const SERIES = /^s\|(tt[0-9]{7,12})$/;
const MS_PER_TICK = 10_000; // Jellyfin: one tick = 100 nanoseconds

function reject(reason) {
  return { qualified: false, reason, rows: [],
    sourceDataChanged: false, clientResponseChanged: false,
    nextUpShelfQualified: false, playableItemQualified: false };
}

const isInt = x => Number.isSafeInteger(x);
const nonnegative = x => isInt(x) && x >= 0;
function coordinate(s,e) {
  return nonnegative(s) && s <= 200
    && isInt(e) && e >= 1 && e <= 10000;
}
function validRows(rows) {
  return Array.isArray(rows) && rows.length <= MAX_EPISODES;
}
function success(rows, sourceKind) {
  return {qualified:true,reason:'READ_MODEL_ONLY',
    sourceKind,rows,sourceDataChanged:false,clientResponseChanged:false,
    nextUpShelfQualified:false,playableItemQualified:false};
}

/**
 * Caller MUST have selected ONE exact AIO watch_sink uuid+persona using the
 * connected Bridge profile and fixed source DB, with SQLite mode=ro and
 * PRAGMA query_only=ON. Every source row must already be from that scope,
 * and must include kind, series_key, season, episode, played, position_ms.
 */
export function adaptAioEpisodeRows({
  rows, exactSinkScopeVerified = false,
} = {}) {
  if (exactSinkScopeVerified !== true) return reject('SCOPE_NOT_VERIFIED');
  if (!validRows(rows)) return reject('ROW_BUDGET');
  const normalized=[];
  for (const row of rows) {
    if (!row || Array.isArray(row) || typeof row !== 'object'
        || row.kind !== 'episode' || typeof row.series_key !== 'string'
        || !SERIES.test(row.series_key)
        || !coordinate(row.season,row.episode)
        || (row.played !== 0 && row.played !== 1)
        || !nonnegative(row.position_ms)) {
      return reject('AIO_ROW_UNQUALIFIED');
    }
    const imdb = row.series_key.slice(2);
    normalized.push({imdb,season:row.season,episode:row.episode,
      played:row.played===1,positionMs:row.position_ms});
  }
  return success(normalized,'aiostreams');
}

/**
 * For a native Jellyfin/Remux source, the caller must explicitly verify:
 * (1) same authenticated user, (2) a COMPLETE episode HISTORY source, NOT a
 * paginated /Shows/NextUp response; (3) Series metadata via /Items/{SeriesId}
 * with the SAME token and a matching object ID, (4) the Remux implementation
 * separately passed the Jellyfin read-contract probe if sourceKind=remux.
 *
 * Exact tick-to-ms conversion: refuse sub-millisecond positions rather than
 * silently lowering playback offsets or claiming reliable progress.
 */
export function adaptJellyfinHistoryItems({
  items, seriesMetadataById,
  sameUserVerified = false, completeHistoryVerified = false,
  sourceKind = 'jellyfin', remuxContractVerified = false,
} = {}) {
  if (!['jellyfin','remux'].includes(sourceKind))
    return reject('BACKEND_UNKNOWN');
  if (sameUserVerified !== true) return reject('USER_UNVERIFIED');
  if (completeHistoryVerified !== true) return reject('HISTORY_NOT_COMPLETE');
  if (sourceKind==='remux' && remuxContractVerified !== true)
    return reject('REMUX_CONTRACT_UNVERIFIED');
  if (!validRows(items)) return reject('ROW_BUDGET');
  if (!seriesMetadataById || typeof seriesMetadataById!=='object'
      || Array.isArray(seriesMetadataById)) return reject('SERIES_METADATA_UNVERIFIED');
  const normalized=[];
  const seenItemIds = new Set();
  for(const item of items) {
    if (!item || Array.isArray(item) || typeof item!=='object'
        || item.Type!=='Episode' || typeof item.Id!=='string'
        || !item.Id || item.Id.length>512
        || typeof item.SeriesId!=='string'
        || !item.SeriesId || item.SeriesId.length>512
        || !coordinate(item.ParentIndexNumber,item.IndexNumber)
        || !item.UserData || typeof item.UserData!=='object'
        || typeof item.UserData.Played !== 'boolean'
        || !nonnegative(item.UserData.PlaybackPositionTicks)) {
      return reject('HISTORY_ITEM_UNQUALIFIED');
    }
    if(seenItemIds.has(item.Id)) return reject('REPEATED_SOURCE_ITEM_ID');
    seenItemIds.add(item.Id);
    const t=item.UserData.PlaybackPositionTicks;
    if(t % MS_PER_TICK !== 0) return reject('SUB_MILLISECOND_TICKS_UNPROVEN');
    const series=seriesMetadataById[item.SeriesId];
    if(!series || Array.isArray(series) || typeof series!=='object'
        || series.Type!=='Series' || series.Id!==item.SeriesId
        || typeof series.ProviderIds?.Imdb !== 'string'
        || !IMDB.test(series.ProviderIds.Imdb.toLowerCase())) {
      return reject('SERIES_METADATA_UNVERIFIED');
    }
    normalized.push({imdb:series.ProviderIds.Imdb.toLowerCase(),
      season:item.ParentIndexNumber,episode:item.IndexNumber,
      played:item.UserData.Played,positionMs:t/MS_PER_TICK});
  }
  return success(normalized,sourceKind);
}

/**
 * Scoped, conflict-safe projection only; a source adapter does NOT authorize
 * Client NextUp rewriting. A verified canonical graph + valid rows are still
 * not proof of an episode being playable or a complete Jellyfin NextUp shelf.
 */
export function projectSourceHistory({
  adapted, graph, traktShowId,
}={}) {
  if (!adapted?.qualified || !['aiostreams','jellyfin','remux'].includes(
    adapted.sourceKind)) {
    return {qualified:false,reason:'ADAPTER_UNQUALIFIED',
      logicalEpisodes:[],sourceDataChanged:false,clientResponseChanged:false,
      nextUpShelfQualified:false};
  }
  const view=buildCanonicalProgressView({
    graph,traktShowId,rows:adapted.rows,
  });
  return {...view,nextUpShelfQualified:false};
}
