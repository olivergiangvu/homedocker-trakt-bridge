import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {qualifyEpisodeCatalogPages,bindCanonicalProgressToCatalog}
  from '../src/nextup-episode-catalog-binding.mjs';
import {buildCanonicalProgressView}
  from '../src/nextup-canonical-progress-view.mjs';

const CANON='tt44051354', ALIAS='tt44094505';
const GRAPH={shows:{'263102':{canonicalImdb:CANON,aliases:[ALIAS]}},conflicts:[]};
const SERIES={Type:'Series',Id:'series-c',ProviderIds:{Imdb:CANON}};
function ep(n,seriesId=SERIES.Id) {
  return {Id:'actual-episode-'+n,SeriesId:seriesId,
    Type:'Episode',ParentIndexNumber:1,IndexNumber:n};
}
function page(start,limit,items,total) {
  return {startIndex:start,requestedLimit:limit,
    response:{Items:structuredClone(items),StartIndex:start,
      Count:items.length,TotalRecordCount:total}};
}
function complete(size=11,limit=10) {
  const all=Array.from({length:size},(_,i)=>ep(i+1));
  const pages=[];
  for (let start=0;start<size;start+=limit) {
    const items=all.slice(start,start+limit);
    pages.push(page(start,limit,items,size));
  }
  return {series:SERIES,pages,sameUserVerified:true,
    backendContractVerified:true,expectedImdb:CANON};
}
function progress() {
  const rows=[
    ...Array.from({length:11},(_,i)=>({
      imdb:CANON,season:1,episode:i+1,played:true,positionMs:0,
    })),
    ...Array.from({length:8},(_,i)=>({
      imdb:ALIAS,season:1,episode:i+1,played:true,positionMs:0,
    }))
  ];
  return buildCanonicalProgressView({graph:GRAPH,traktShowId:'263102',rows});
}

test('AIO-style /Shows/:seriesId/Episodes honors 0,10 while NextUp ignores StartIndex',()=>{
  const view=progress();
  assert.equal(view.qualified,true);
  const contract=qualifyEpisodeCatalogPages(complete());
  assert.equal(contract.qualified,true);
  assert.equal(contract.catalogItems,11);
  assert.equal(contract.catalogComplete,true);
  assert.equal(contract.globalNextUpQualified,false);
  const joined=bindCanonicalProgressToCatalog({
    progressView:view,catalog:contract,expectedCanonicalImdb:CANON,
  });
  assert.equal(joined.qualified,true);
  assert.equal(joined.historyCoordinatesResolved,11);
  assert.equal(joined.historyCoordinatesUnresolved,0);
  assert.equal(joined.episodeBindings.length,11);
  assert.equal(joined.episodeBindings.every(x=>x.resolvedEpisodeId.startsWith(
    'actual-episode-')),true);
  assert.equal(joined.episodeBindings.every(x=>x.catalogUserDataUnmodified),true);
  assert.equal(joined.playbackAvailabilityProven,false);
  assert.equal(joined.clientResponseChanged,false);
});

test('native Jellyfin and Remux pagination can use same contract after explicit qualification',()=>{
  const first=qualifyEpisodeCatalogPages({...complete(),
    backendContractVerified:false});
  assert.equal(first.reason,'BACKEND_EPISODE_API_UNVERIFIED');
  const good=qualifyEpisodeCatalogPages(complete());
  assert.equal(good.qualified,true);
});

test('only exact same-user and IMDb-matched Series metadata can bind',()=>{
  assert.equal(qualifyEpisodeCatalogPages({
    ...complete(),sameUserVerified:false,
  }).reason,'USER_IDENTITY_UNVERIFIED');
  assert.equal(qualifyEpisodeCatalogPages({
    ...complete(),series:{...SERIES,ProviderIds:{Imdb:ALIAS}},
  }).reason,'SERIES_IDENTITY_MISMATCH');
});

test('ignored StartIndex in a catalog page fails closed, no fake total',()=>{
  const input=complete();
  input.pages[1].response.StartIndex=0;
  assert.equal(qualifyEpisodeCatalogPages(input).reason,
    'PAGE_RESPONSE_CONTRACT_UNVERIFIED');
});

test('truncated 100/100 page does not count as exhausted catalog',()=>{
  const input=complete(101,100);
  input.pages=input.pages.slice(0,1);
  const result=qualifyEpisodeCatalogPages(input);
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'CATALOG_NOT_EXHAUSTIVELY_VERIFIED');
  assert.equal(result.catalogItems,100);
});

test('prefix-only totals and short page with larger global count rejected',()=>{
  const input=complete();
  input.pages[0].response.TotalRecordCount=10;
  const result=qualifyEpisodeCatalogPages(input);
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'PAGE_RESPONSE_CONTRACT_UNVERIFIED');
  const short=complete(11,10);
  short.pages[0].response.Items=short.pages[0].response.Items.slice(0,9);
  short.pages[0].response.Count=9;
  assert.equal(qualifyEpisodeCatalogPages(short).reason,
    'SHORT_PAGE_BEFORE_TRUE_END');
});

test('drifting total between pages rejected',()=>{
  const input=complete();
  input.pages[1].response.TotalRecordCount=12;
  assert.equal(qualifyEpisodeCatalogPages(input).reason,'GLOBAL_TOTAL_DRIFT');
});

test('duplicate episode ID, duplicate coordinates, mismatched SeriesId rejected',()=>{
  let input=complete();
  input.pages[1].response.Items[0].Id=input.pages[0].response.Items[0].Id;
  assert.equal(qualifyEpisodeCatalogPages(input).reason,
    'DUPLICATE_CATALOG_ID_OR_COORDINATE');
  input=complete();
  input.pages[1].response.Items[0].ParentIndexNumber=1;
  input.pages[1].response.Items[0].IndexNumber=1;
  assert.equal(qualifyEpisodeCatalogPages(input).reason,
    'DUPLICATE_CATALOG_ID_OR_COORDINATE');
  input=complete();
  input.pages[0].response.Items[0].SeriesId='untrusted';
  assert.equal(qualifyEpisodeCatalogPages(input).reason,
    'CATALOG_ITEM_CONTRACT_UNVERIFIED');
});

test('catalog listing with missing history episode never synthesizes an Id',()=>{
  const input=complete();
  input.pages[1].response.Items[0].IndexNumber=12;
  const catalog=qualifyEpisodeCatalogPages(input);
  assert.equal(catalog.qualified,true);
  const joined=bindCanonicalProgressToCatalog({
    progressView:progress(),catalog,expectedCanonicalImdb:CANON,
  });
  assert.equal(joined.qualified,false);
  assert.equal(joined.reason,'HISTORY_EPISODE_NOT_IN_CATALOG');
  assert.equal(joined.globalNextUpQualified,false);
});

test('unverified history state and mismatched canonical catalog reject',()=>{
  const catalog=qualifyEpisodeCatalogPages(complete());
  assert.equal(bindCanonicalProgressToCatalog({
    progressView:{qualified:false},catalog,expectedCanonicalImdb:CANON,
  }).reason,'PROGRESS_VIEW_UNQUALIFIED');
  assert.equal(bindCanonicalProgressToCatalog({
    progressView:progress(),catalog,expectedCanonicalImdb:ALIAS,
  }).reason,'CATALOG_NOT_QUALIFIED');
});

test('no production integration or watch-history mutation, only source-provided IDs',()=>{
  const file=readFileSync(
    new URL('../src/nextup-episode-catalog-binding.mjs',import.meta.url),'utf8');
  for(const forbidden of [
    'fetch(','cacheSet(','UPDATE watch_state','INSERT INTO','DELETE FROM',
    'createServer(','TraktClient','PlaybackInfo(',
  ]) assert.equal(file.includes(forbidden),false);
  for(const path of ['../src/index.mjs','../src/jellyfin-gateway.mjs']) {
    const code=readFileSync(new URL(path,import.meta.url),'utf8');
    assert.doesNotMatch(code,/nextup-episode-catalog-binding/);
  }
});
