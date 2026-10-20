/**
 * RC3-M: COMPLETE, SINK-SCOPED AIO watch-state series-key inventory.
 *
 * Separates:
 * 1. Any source series_key with episode-coordinate rows (HISTORY).
 * 2. recentSeries raw watched/resume candidate keys (CANDIDATES).
 * 3. Actual NextUp cards (NEVER inferred or created here).
 *
 * Inputs come from ONE independently verified mapped user, exact AIO sink,
 * bounded consistent read-only SQLite snapshots and trusted Bridge graph.
 * This function is pure. It cannot authenticate users or certify snapshots.
 * Verified alias groups are only logical inventory *hints*; they do NOT
 * prove source match_key drops, metadata, caughtUp, playable item IDs or
 * NextUp placement. Never change upstream sort, history or playback states.
 *
 * No production imports; no network, database, Trakt, Jellyfin, Remux,
 * route configuration or storage writes. Keep returned IDs strictly in
 * local shadow memory, never print them in a HomeDocker audit.
 */
const MAX_KEYS=10_000;
const MAX_GROUPS=200;
const MAX_PER_GROUP=13;
const KEY=/^s\|(tt[0-9]{7,12})$/;
const anyKey=x=>typeof x==='string'&&x.length>0&&x.length<=512;
const positiveInt=x=>Number.isSafeInteger(x)&&x>=1;
const nonnegativeFinite=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0;

const refused=reason=>({
  qualified:false,reason,
  historySeriesKeyCount:0,recentCandidateKeyCount:0,
  historyOnlyKeyCount:0,validatedAliasGroups:0,
  graphCollapsedKeyReduction:0,
  candidate1To50:0,candidate51To200:0,candidateBeyond200:0,
  unseenOutsideSourceWindow:0,
  inventory:[],
  sourceDataChanged:false,clientResponseChanged:false,
  playableCatalogQualified:false,nextUpShelfQualified:false,
  globalNextUpQualified:false,
});

/**
 * Caller must independently verify all flags, including that the two SQL
 * GROUP BY statements were executed on the SAME complete SQLite snapshot.
 * A limited 50/200 NextUp response is NEVER valid for recentCandidates.
 */
export function buildScopedSeriesDiscovery({
  historySeries,recentCandidates,verifiedAliasGroups=[],
  evidence,
}={}) {
  if (!evidence || evidence.sameMappedUser!==true
      || evidence.uniqueSinkVerified!==true
      || evidence.connectedBridgeProfileVerified!==true
      || evidence.graphVerified!==true
      || evidence.snapshotDoubleReadStable!==true
      || evidence.queryBudgetExhaustionChecked!==true
      || evidence.recentSeriesSqlContractVerified!==true)
    return refused('SOURCE_EVIDENCE_INCOMPLETE');
  if (!Array.isArray(historySeries) || historySeries.length>MAX_KEYS
      || !Array.isArray(recentCandidates)
      || recentCandidates.length>MAX_KEYS
      || !Array.isArray(verifiedAliasGroups)
      || verifiedAliasGroups.length>MAX_GROUPS)
    return refused('SOURCE_BUDGET_UNQUALIFIED');

  const all=new Map();
  for(const row of historySeries){
    if(!row||Array.isArray(row)||typeof row!=='object'
        || !anyKey(row.seriesKey)
        || !positiveInt(row.episodeCoordinateRows)
        || all.has(row.seriesKey))
      return refused('HISTORY_SERIES_ROW_INVALID');
    all.set(row.seriesKey,{
      seriesKey:row.seriesKey,
      historyEpisodeRows:row.episodeCoordinateRows,
      rawCandidateRank:null,
    });
  }
  let priorTime=Infinity, priorKey='',rank=0;
  for(const row of recentCandidates){
    if(!row||Array.isArray(row)||typeof row!=='object'
        || !anyKey(row.seriesKey)
        || !nonnegativeFinite(row.lastSortAt))
      return refused('RECENT_CANDIDATE_ROW_INVALID');
    // The upstream GROUP BY is ranked by MAX(sort_at) DESC, series_key ASC.
    if(row.lastSortAt>priorTime
        || (row.lastSortAt===priorTime && row.seriesKey<=priorKey))
      return refused('RECENT_CANDIDATE_ORDER_INVALID');
    const source=all.get(row.seriesKey);
    if(!source || source.rawCandidateRank!==null)
      return refused('RECENT_NOT_A_UNIQUE_HISTORY_KEY');
    source.rawCandidateRank=++rank;
    priorTime=row.lastSortAt;
    priorKey=row.seriesKey;
  }

  const ownedKeys=new Set(),groups=[];
  for(const group of verifiedAliasGroups){
    if(!Array.isArray(group)||group.length<2||group.length>MAX_PER_GROUP)
      return refused('VERIFIED_GROUP_SHAPE_INVALID');
    const spellings=group.map(x=>KEY.test('s|'+x)?'s|'+x:null);
    if(spellings.some(x=>!x)||new Set(spellings).size!==spellings.length)
      return refused('VERIFIED_GROUP_SHAPE_INVALID');
    if(spellings.some(x=>ownedKeys.has(x)))
      return refused('VERIFIED_GRAPH_COLLISION');
    spellings.forEach(x=>ownedKeys.add(x));
    groups.push(spellings);
  }

  const inventory=[];
  const represented=new Set();
  let graphCollapsedKeyReduction=0;
  for(const group of groups){
    const keys=group.filter(x=>all.has(x));
    if(!keys.length)continue;
    keys.forEach(x=>represented.add(x));
    graphCollapsedKeyReduction+=keys.length-1;
    const ranks=keys.map(x=>all.get(x).rawCandidateRank).filter(x=>x!==null);
    inventory.push({
      classification:'VERIFIED_GRAPH_GROUP_HINT_ONLY',
      rawSeriesKeys:keys,
      rawHistoryRows:keys.reduce((sum,x)=>sum+all.get(x).historyEpisodeRows,0),
      firstCandidateRank:ranks.length?Math.min(...ranks):null,
      candidateSpellingCount:ranks.length,
      playableItemQualified:false,
    });
  }
  for(const row of all.values()){
    if(represented.has(row.seriesKey))continue;
    inventory.push({
      classification:KEY.test(row.seriesKey)
        ? 'SINGLE_SOURCE_IMDB_KEY_UNGROUPED'
        : 'OPAQUE_OR_UNVERIFIED_SOURCE_KEY',
      rawSeriesKeys:[row.seriesKey],
      rawHistoryRows:row.historyEpisodeRows,
      firstCandidateRank:row.rawCandidateRank,
      candidateSpellingCount:row.rawCandidateRank===null?0:1,
      playableItemQualified:false,
    });
  }
  const count=recentCandidates.length;
  return {
    qualified:true,reason:'COMPLETE_RAW_WATCH_STATE_SERIES_INDEX_ONLY',
    historySeriesKeyCount:all.size,
    recentCandidateKeyCount:count,
    historyOnlyKeyCount:all.size-count,
    candidate1To50:Math.min(count,50),
    candidate51To200:Math.max(0,Math.min(count,200)-50),
    candidateBeyond200:Math.max(0,count-200),
    validatedAliasGroups:groups.length,
    graphCollapsedKeyReduction,
    // The complete source series inventory is NOT a NextUp replacement.
    unseenOutsideSourceWindow:Math.max(0,count-200),
    inventory,
    sourceDataChanged:false,clientResponseChanged:false,
    playableCatalogQualified:false,nextUpShelfQualified:false,
    globalNextUpQualified:false,
  };
}
