import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {
  adaptAioEpisodeRows, adaptJellyfinHistoryItems, projectSourceHistory
} from '../src/nextup-progress-source-adapters.mjs';

const c='tt44051354', a='tt44094505';
const graph={shows:{'263102':{canonicalImdb:c,aliases:[a]}},conflicts:[]};
const aioRow=(imdb,ep,played=1,position=0)=>({
  kind:'episode',series_key:'s|'+imdb,season:1,episode:ep,
  played,position_ms:position,
});
const jellyItem=(id,seriesId,ep,played=true,ticks=0)=>({
  Type:'Episode',Id:id,SeriesId:seriesId,
  ParentIndexNumber:1,IndexNumber:ep,
  UserData:{Played:played,PlaybackPositionTicks:ticks},
});
const metadata={
  x:{Type:'Series',Id:'x',ProviderIds:{Imdb:c}},
  y:{Type:'Series',Id:'y',ProviderIds:{Imdb:a}},
};
const aio19=[
  ...Array.from({length:11},(_,i)=>aioRow(c,i+1)),
  ...Array.from({length:8},(_,i)=>aioRow(a,i+1)),
];
const jelly19=[
  ...Array.from({length:11},(_,i)=>jellyItem('cx'+i,'x',i+1)),
  ...Array.from({length:8},(_,i)=>jellyItem('ay'+i,'y',i+1)),
];
function projection(adapted) {
  return projectSourceHistory({adapted,graph,traktShowId:'263102'});
}

test('AIO read-only mapped watch_state adapter maps actual 11+8 to 11 logical episodes',()=>{
  const before=structuredClone(aio19);
  const adapted=adaptAioEpisodeRows({
    rows:aio19,exactSinkScopeVerified:true,
  });
  assert.equal(adapted.qualified,true);
  assert.equal(adapted.rows.length,19);
  assert.equal(adapted.nextUpShelfQualified,false);
  const view=projection(adapted);
  assert.equal(view.qualified,true);
  assert.equal(view.logicalEpisodes.length,11);
  assert.equal(view.metrics.overlapEpisodes,8);
  assert.equal(view.metrics.canonicalOnlyEpisodes,3);
  assert.equal(view.sourceDataChanged,false);
  assert.equal(view.clientResponseChanged,false);
  assert.deepEqual(aio19,before);
});

test('native Jellyfin complete history + same-user series lookup yields same canonical view',()=>{
  const before=structuredClone(jelly19);
  const adapted=adaptJellyfinHistoryItems({
    items:jelly19,seriesMetadataById:metadata,
    sameUserVerified:true,completeHistoryVerified:true,sourceKind:'jellyfin',
  });
  assert.equal(adapted.qualified,true);
  assert.equal(adapted.rows.length,19);
  const view=projection(adapted);
  assert.equal(view.qualified,true);
  assert.equal(view.logicalEpisodes.length,11);
  assert.equal(view.metrics.overlapEpisodes,8);
  assert.deepEqual(jelly19,before);
  assert.equal(adapted.playableItemQualified,false);
});

test('Remux needs explicit contract proof even when shape is Jellyfin compatible',()=>{
  const base={
    items:jelly19,seriesMetadataById:metadata,sameUserVerified:true,
    completeHistoryVerified:true,sourceKind:'remux',
  };
  let adapted=adaptJellyfinHistoryItems(base);
  assert.equal(adapted.qualified,false);
  assert.equal(adapted.reason,'REMUX_CONTRACT_UNVERIFIED');
  adapted=adaptJellyfinHistoryItems({...base,remuxContractVerified:true});
  assert.equal(adapted.qualified,true);
  assert.equal(projection(adapted).logicalEpisodes.length,11);
});

test('Jellyfin NextUp page cannot masquerade as full user history',()=>{
  const partial=jelly19.slice(0,20);
  const result=adaptJellyfinHistoryItems({
    items:partial,seriesMetadataById:metadata,
    sameUserVerified:true,completeHistoryVerified:false,
  });
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'HISTORY_NOT_COMPLETE');
  assert.equal(projection(result).qualified,false);
});

test('never allow unauthenticated/cross-profile episode states',()=>{
  assert.equal(adaptAioEpisodeRows({rows:aio19}).reason,'SCOPE_NOT_VERIFIED');
  assert.equal(adaptJellyfinHistoryItems({
    items:jelly19,seriesMetadataById:metadata,
    completeHistoryVerified:true,
  }).reason,'USER_UNVERIFIED');
});

test('reject missing series metadata and mismatched SeriesId',()=>{
  const mismatch={...metadata,x:{...metadata.x,Id:'wrong'}};
  const result=adaptJellyfinHistoryItems({
    items:jelly19,seriesMetadataById:mismatch,
    sameUserVerified:true,completeHistoryVerified:true,
  });
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'SERIES_METADATA_UNVERIFIED');
});

test('preserve exact playback position and fail closed on sub-millisecond tick quantization',()=>{
  let source=[jellyItem('one','x',4,false,45_000)];
  let result=adaptJellyfinHistoryItems({
    items:source,seriesMetadataById:metadata,
    sameUserVerified:true,completeHistoryVerified:true,
  });
  assert.equal(result.reason,'SUB_MILLISECOND_TICKS_UNPROVEN');
  source=[jellyItem('one','x',4,false,40_000)];
  result=adaptJellyfinHistoryItems({
    items:source,seriesMetadataById:metadata,
    sameUserVerified:true,completeHistoryVerified:true,
  });
  assert.equal(result.rows[0].positionMs,4);
  const view=projection(result);
  assert.equal(view.logicalEpisodes[0].positionMs,4);
});

test('reject duplicate user item ID, malformed episode numbers and invalid AIO states',()=>{
  assert.equal(adaptJellyfinHistoryItems({
    items:[jellyItem('same','x',1),jellyItem('same','y',1)],
    seriesMetadataById:metadata,sameUserVerified:true,completeHistoryVerified:true,
  }).reason,'REPEATED_SOURCE_ITEM_ID');
  assert.equal(adaptAioEpisodeRows({rows:[
    {...aioRow(c,1),played:2}],exactSinkScopeVerified:true,
  }).reason,'AIO_ROW_UNQUALIFIED');
  assert.equal(adaptAioEpisodeRows({rows:[
    {...aioRow(c,1),season:999}],exactSinkScopeVerified:true,
  }).reason,'AIO_ROW_UNQUALIFIED');
  assert.equal(adaptAioEpisodeRows({rows:[
    {...aioRow(c,1),series_key:'s|unverified'}],exactSinkScopeVerified:true,
  }).reason,'AIO_ROW_UNQUALIFIED');
});

test('future conflicting played state and two active resumes still reject canonical view',()=>{
  const source=[
    aioRow(c,3,0,1200),
    aioRow(a,3,0,800),
  ];
  const adapted=adaptAioEpisodeRows({rows:source,exactSinkScopeVerified:true});
  const view=projection(adapted);
  assert.equal(view.qualified,false);
  assert.equal(view.reason,'PLAYBACK_STATE_CONFLICT');
  assert.equal(view.sourceDataChanged,false);
});

test('prototype is isolated from production and cannot fabricate NextUp/Trakt writes',()=>{
  const source=readFileSync(
    new URL('../src/nextup-progress-source-adapters.mjs',import.meta.url),'utf8');
  for(const phrase of ['fetch(','UPDATE watch_state','DELETE FROM','INSERT INTO',
    'TraktClient','createServer(','cacheSet(']){
    assert.equal(source.includes(phrase),false);
  }
  for(const path of ['../src/index.mjs','../src/jellyfin-gateway.mjs']){
    const code=readFileSync(new URL(path,import.meta.url),'utf8');
    assert.doesNotMatch(code,/nextup-progress-source-adapters/);
  }
});
