import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildScopedAioHistoryCatalogBinding as bind}
  from '../src/nextup-scoped-aio-catalog-read-model.mjs';

const canonical='tt44051354',alias='tt44094505';
const graph={shows:{'263102':{canonicalImdb:canonical,aliases:[alias]}},
  conflicts:[]};
const series={Type:'Series',Id:'backend-real-series-id',
  ProviderIds:{Imdb:canonical}};
const ep=n=>({
  Type:'Episode',Id:'backend-real-item-'+n,SeriesId:series.Id,
  ParentIndexNumber:1,IndexNumber:n,
  UserData:{Played:false,PlaybackPositionTicks:0},
});
const evidence={
  sameMappedUser:true,exactSinkScopeVerified:true,graphVerified:true,
  graphSnapshotStable:true,watchStateDoubleReadStable:true,
  nextUpDoubleReadStable:true,canonicalNextUpCardVerified:true,
  catalogDoubleReadStable:true,catalogBackendContractVerified:true,
};
const aio=(imdb,n,played=1,position_ms=0)=>({
  kind:'episode',series_key:'s|'+imdb,season:1,episode:n,
  played,position_ms,
});
const watchRows=[
  ...Array.from({length:11},(_,i)=>aio(canonical,i+1)),
  ...Array.from({length:8},(_,i)=>aio(alias,i+1)),
];
function args() {
  const all=Array.from({length:12},(_,i)=>ep(i+1));
  const page={startIndex:0,requestedLimit:100,
    response:{Items:all,StartIndex:0,TotalRecordCount:12,Count:12}};
  return {graph:structuredClone(graph),traktShowId:'263102',
    expectedCanonicalImdb:canonical,watchRows:structuredClone(watchRows),
    series:structuredClone(series),pages:[structuredClone(page)],
    evidence:{...evidence}};
}

test('RC3-K real aggregate 19->11 of twelve catalogs composes 11 source-provided IDs',()=>{
  const input=args(),before=structuredClone(input);
  const model=bind(input);
  assert.equal(model.qualified,true);
  assert.equal(model.reason,'SCOPED_EPISODE_IDS_BOUND_READ_ONLY');
  assert.equal(model.logicalEpisodeCount,11);
  assert.equal(model.catalogItemCount,12);
  assert.equal(model.catalogOnlyCount,1);
  assert.equal(model.canonicalAliasOverlap,8);
  assert.equal(model.aliasOnlyCoordinates,0);
  assert.equal(model.activeResumes,0);
  assert.equal(model.episodeBindings.length,11);
  assert.deepEqual(model.episodeBindings.map(x=>x.resolvedEpisodeId),
    Array.from({length:11},(_,i)=>'backend-real-item-'+(i+1)));
  assert.equal(model.episodeBindings.every(x=>x.catalogUserDataUnmodified===true),true);
  assert.equal(model.episodeBindings.every(x=>x.historicalPlayed===true),true);
  assert.equal(model.episodeBindings.every(x=>x.historicalPositionMs===0),true);
  assert.deepEqual(input,before,'caller input must remain byte-for-byte intact');
  assert.equal(model.mediaPlaybackProven,false);
  assert.equal(model.nextUpShelfQualified,false);
  assert.equal(model.globalNextUpQualified,false);
  assert.equal(model.clientResponseChanged,false);
  assert.equal(model.sourceDataChanged,false);
});

test('missing any independently supplied same-user/snapshot gate rejects all IDs',()=>{
  for (const key of Object.keys(evidence)){
    const arg=args();arg.evidence[key]=false;
    const m=bind(arg);
    assert.equal(m.qualified,false,key);
    assert.equal(m.reason,'SOURCE_EVIDENCE_INCOMPLETE');
    assert.deepEqual(m.episodeBindings,[]);
  }
  const m=bind({...args(),evidence:undefined});
  assert.equal(m.qualified,false);
});

test('noncanonical series and invalid graph cannot bind source IDs',()=>{
  let data=args();data.series.ProviderIds.Imdb=alias;
  assert.equal(bind(data).reason,'CATALOG_SERIES_IDENTITY_MISMATCH');
  data=args();data.graph.conflicts=[{id:canonical}];
  assert.equal(bind(data).reason,'CANONICAL_GRAPH_IDENTITY_UNVERIFIED');
  data=args();data.expectedCanonicalImdb=alias;
  assert.equal(bind(data).reason,'CANONICAL_GRAPH_IDENTITY_UNVERIFIED');
});

test('missing history coordinate never synthesizes a backend item ID',()=>{
  const data=args();
  data.pages[0].response.Items[10].IndexNumber=13;
  const m=bind(data);
  assert.equal(m.qualified,false);
  assert.equal(m.reason,'COORDINATE_JOIN_HISTORY_EPISODE_NOT_IN_CATALOG');
  assert.deepEqual(m.episodeBindings,[]);
});

test('catalog-only 12th cannot be called unwatched',()=>{
  const m=bind(args());
  assert.equal(m.qualified,true);
  assert.equal(m.catalogOnlyCount,1);
  assert.equal(JSON.stringify(m).includes('unwatched'),false);
});

test('conflicting played aliases fail closed without progress promotion',()=>{
  const data=args();
  data.watchRows[11].played=0;
  const m=bind(data);
  assert.equal(m.qualified,false);
  assert.equal(m.reason,'CANONICAL_VIEW_PLAYBACK_STATE_CONFLICT');
  assert.deepEqual(m.episodeBindings,[]);
});

test('one genuine alias active resume stays preserved without rewriting Jellyfin UserData',()=>{
  const data=args();
  data.watchRows=data.watchRows.map(x=>({...x,played:1,position_ms:0}));
  data.watchRows[3].played=0;
  data.watchRows[11+3].played=0;
  data.watchRows[11+3].position_ms=321000;
  const m=bind(data);
  assert.equal(m.qualified,true);
  assert.equal(m.activeResumes,1);
  const binding=m.episodeBindings.find(x=>x.episode===4);
  assert.equal(binding.historicalPlayed,false);
  assert.equal(binding.historicalPositionMs,321000);
  assert.equal(binding.historicalSourceSpelling,'alias');
  assert.equal(data.pages[0].response.Items[3].UserData.Played,false);
  assert.equal(m.clientResponseChanged,false);
});

test('double active resume fails before ID correlation',()=>{
  const data=args();
  data.watchRows[3].played=0;
  data.watchRows[3].position_ms=120;
  data.watchRows[11+3].played=0;
  data.watchRows[11+3].position_ms=321;
  const m=bind(data);
  assert.equal(m.qualified,false);
  assert.equal(m.reason,'CANONICAL_VIEW_PLAYBACK_STATE_CONFLICT');
});

test('immutable offline module not linked to Gateway/NextUp/Trakt transports',()=>{
  const path=new URL('../src/nextup-scoped-aio-catalog-read-model.mjs',import.meta.url);
  const code=readFileSync(path,'utf8');
  for(const s of ['fetch(','/PlaybackInfo','UPDATE watch_state','DELETE FROM',
                  'INSERT INTO','createServer(','TraktClient','cacheSet('])
    assert.equal(code.includes(s),false,s);
  for(const path of ['../src/index.mjs','../src/jellyfin-gateway.mjs']) {
    const body=readFileSync(new URL(path,import.meta.url),'utf8');
    assert.equal(body.includes('nextup-scoped-aio-catalog-read-model'),false);
  }
});
