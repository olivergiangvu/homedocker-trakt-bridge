import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildScopedSeriesDiscovery as discover}
  from '../src/nextup-scoped-series-discovery.mjs';

const c='tt44051354',a='tt44094505';
const good={
  sameMappedUser:true,uniqueSinkVerified:true,
  connectedBridgeProfileVerified:true,graphVerified:true,
  snapshotDoubleReadStable:true,queryBudgetExhaustionChecked:true,
  recentSeriesSqlContractVerified:true,
};
const item=(i)=>'s|tt'+String(7000000+i);
function input(count=861) {
  // 861 distinct raw candidate keys, with a verified alias pair at top.
  const candidates=[c,a,...Array.from({length:count-2},(_,i)=>'tt'+String(7000000+i))];
  return {
    evidence:{...good},
    verifiedAliasGroups:[[c,a]],
    historySeries:candidates.map((imdb,i)=>({
      seriesKey:'s|'+imdb,episodeCoordinateRows:i===0?11:i===1?8:1,
    })),
    recentCandidates:candidates.map((imdb,i)=>({
      seriesKey:'s|'+imdb,lastSortAt:count-i,
    })),
  };
}

test('all 861 ranked source keys retained, including positions beyond 200',()=>{
  const arg=input(),before=structuredClone(arg);
  const result=discover(arg);
  assert.equal(result.qualified,true);
  assert.equal(result.historySeriesKeyCount,861);
  assert.equal(result.recentCandidateKeyCount,861);
  assert.equal(result.historyOnlyKeyCount,0);
  assert.equal(result.candidate1To50,50);
  assert.equal(result.candidate51To200,150);
  assert.equal(result.candidateBeyond200,661);
  assert.equal(result.graphCollapsedKeyReduction,1);
  assert.equal(result.inventory.length,860);
  assert.equal(result.inventory[0].classification,
    'VERIFIED_GRAPH_GROUP_HINT_ONLY');
  assert.equal(result.inventory[0].rawHistoryRows,19);
  assert.equal(result.inventory[0].candidateSpellingCount,2);
  assert.equal(result.globalNextUpQualified,false);
  assert.equal(result.playableCatalogQualified,false);
  assert.equal(result.nextUpShelfQualified,false);
  assert.deepEqual(arg,before);
});

test('history-only keys remain in inventory but not NextUp candidate stream',()=>{
  const arg=input(4);
  arg.historySeries.push({seriesKey:'s|tt91919191',episodeCoordinateRows:1});
  const result=discover(arg);
  assert.equal(result.qualified,true);
  assert.equal(result.historySeriesKeyCount,5);
  assert.equal(result.recentCandidateKeyCount,4);
  assert.equal(result.historyOnlyKeyCount,1);
  assert.equal(result.candidateBeyond200,0);
  assert.equal(result.inventory.at(-1).firstCandidateRank,null);
});

test('opaque series keys never become verified IMDb or a playable Item',()=>{
  const arg=input(3);
  arg.historySeries.push({seriesKey:'opaque-key',episodeCoordinateRows:1});
  const result=discover(arg);
  assert.equal(result.qualified,true);
  const opaque=result.inventory.find(x=>x.rawSeriesKeys[0]==='opaque-key');
  assert.equal(opaque.classification,'OPAQUE_OR_UNVERIFIED_SOURCE_KEY');
  assert.equal(opaque.playableItemQualified,false);
});

test('graph group aliases collapse only known graph group, never unrelated keys',()=>{
  const arg=input(4);
  arg.verifiedAliasGroups=[];
  let r=discover(arg);
  assert.equal(r.graphCollapsedKeyReduction,0);
  assert.equal(r.inventory.length,4);
  arg.verifiedAliasGroups=[[c,a]];
  r=discover(arg);
  assert.equal(r.graphCollapsedKeyReduction,1);
  assert.equal(r.inventory.length,3);
  assert.equal(r.inventory[0].rawSeriesKeys.length,2);
});

test('collision or duplicate group spellings reject entire projection',()=>{
  const arg=input(4);
  arg.verifiedAliasGroups=[[c,a],[c,'tt22113344']];
  assert.equal(discover(arg).reason,'VERIFIED_GRAPH_COLLISION');
  arg.verifiedAliasGroups=[[c,c]];
  assert.equal(discover(arg).reason,'VERIFIED_GROUP_SHAPE_INVALID');
});

test('all evidence must be verified for source snapshot, user and graph',()=>{
  for(const key of Object.keys(good)){
    const arg=input(4);
    arg.evidence[key]=false;
    const result=discover(arg);
    assert.equal(result.qualified,false,key);
    assert.equal(result.reason,'SOURCE_EVIDENCE_INCOMPLETE');
    assert.deepEqual(result.inventory,[]);
  }
});

test('a 50-record NextUp prefix cannot masquerade as full source scan',()=>{
  const arg=input();
  const first50=arg.recentCandidates.slice(0,50);
  // Caller cannot attest queryBudgetExhaustionChecked for a truncated
  // NextUp page. This is an independent provenance gate.
  arg.recentCandidates=first50;
  arg.evidence.queryBudgetExhaustionChecked=false;
  assert.equal(discover(arg).reason,'SOURCE_EVIDENCE_INCOMPLETE');
});

test('duplicate keys, unsorted time and missing history are rejected',()=>{
  let arg=input(5);
  arg.recentCandidates[1]=arg.recentCandidates[0];
  assert.equal(discover(arg).reason,'RECENT_CANDIDATE_ORDER_INVALID');
  arg=input(5);arg.recentCandidates[1].lastSortAt=99999;
  assert.equal(discover(arg).reason,'RECENT_CANDIDATE_ORDER_INVALID');
  arg=input(5);arg.historySeries.pop();
  assert.equal(discover(arg).reason,'RECENT_NOT_A_UNIQUE_HISTORY_KEY');
  arg=input(5);arg.historySeries[1]=arg.historySeries[0];
  assert.equal(discover(arg).reason,'HISTORY_SERIES_ROW_INVALID');
});

test('tie-breaking follows source SQLite series_key ordering',()=>{
  const arg=input(4);
  arg.recentCandidates[1].lastSortAt=arg.recentCandidates[0].lastSortAt;
  // c lexically precedes a? No: "tt44094505" > "tt44051354".
  assert.equal(discover(arg).qualified,true);
  const bad=input(4);
  bad.recentCandidates[0].lastSortAt=bad.recentCandidates[1].lastSortAt;
  // Use a key that exists in the scoped source index, but sorts after alias.
  bad.recentCandidates[0].seriesKey=bad.recentCandidates[2].seriesKey;
  assert.equal(discover(bad).reason,'RECENT_CANDIDATE_ORDER_INVALID');
});

test('no production wiring, global synthetic shelf or watch-state writes',()=>{
  const s=readFileSync(
    new URL('../src/nextup-scoped-series-discovery.mjs',import.meta.url),'utf8');
  for(const phrase of ['fetch(','INSERT INTO','UPDATE watch_state',
    'DELETE FROM','TraktClient','createServer(','cacheSet('])
    assert.equal(s.includes(phrase),false,phrase);
  for(const path of ['../src/index.mjs','../src/jellyfin-gateway.mjs']){
    const source=readFileSync(new URL(path,import.meta.url),'utf8');
    assert.doesNotMatch(source,/nextup-scoped-series-discovery/);
  }
});
