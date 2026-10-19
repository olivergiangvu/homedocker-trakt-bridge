import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {verifiedSpellingSet,buildCanonicalProgressView} from '../src/nextup-canonical-progress-view.mjs';

const graph = Object.freeze({
  shows: {'263102':{
    canonicalImdb:'tt44051354',aliases:['tt44094505'],tmdb:276470,
  }},
  conflicts:[],
});
const row = (imdb,episode,{season=1,played=true,positionMs=0}={}) => ({
  imdb,season,episode,played,positionMs,
});
const c='tt44051354', a='tt44094505';
const example = [
  ...Array.from({length:11},(_,i)=>row(c,i+1)),
  ...Array.from({length:8},(_,i)=>row(a,i+1)),
];

test('RC3-F 11+8 rows with eight overlapping coordinates -> 11 immutable logical episodes',()=>{
  const before=structuredClone(example);
  const result=buildCanonicalProgressView({
    graph,traktShowId:'263102',rows:example,
  });
  assert.equal(result.qualified,true);
  assert.equal(result.reason,'VERIFIED_LOCAL_LOGICAL_VIEW_ONLY');
  assert.equal(result.logicalEpisodes.length,11);
  assert.deepEqual(result.metrics,{
    inputRows:19,canonicalEpisodes:11,aliasEpisodes:8,
    overlapEpisodes:8,canonicalOnlyEpisodes:3,aliasOnlyEpisodes:0,
    conflicts:0,activeResumes:0,aliasesPreserved:8,
  });
  assert.equal(result.logicalEpisodes.filter(x=>x.sourceSpelling==='alias').length,0);
  assert.equal(result.sourceDataChanged,false);
  assert.equal(result.clientResponseChanged,false);
  assert.equal(result.playableItemQualified,false);
  assert.equal(result.globalPaginationQualified,false);
  assert.deepEqual(example,before);
  assert.equal(result.logicalEpisodes.every(x=>x.played && x.positionMs===0),true);
});

test('canonical active resume preferred over inactive verified alias without changing input',()=>{
  const rows=[
    row(c,3,{played:false,positionMs:12345}),
    row(a,3,{played:false,positionMs:0}),
  ];
  const result=buildCanonicalProgressView({graph,traktShowId:'263102',rows});
  assert.equal(result.qualified,true);
  assert.equal(result.logicalEpisodes[0].sourceSpelling,'canonical');
  assert.equal(result.logicalEpisodes[0].positionMs,12345);
  assert.equal(result.metrics.activeResumes,1);
});

test('alias active resume retained as alias evidence; never invent canonical playable item',()=>{
  const rows=[
    row(c,3,{played:false,positionMs:0}),
    row(a,3,{played:false,positionMs:456}),
    row(a,4,{played:false,positionMs:123}),
  ];
  const result=buildCanonicalProgressView({graph,traktShowId:'263102',rows});
  assert.equal(result.qualified,true);
  assert.equal(result.logicalEpisodes.length,2);
  assert.equal(result.logicalEpisodes[0].sourceSpelling,'alias');
  assert.equal(result.logicalEpisodes[0].hasCanonicalEvidence,true);
  assert.equal(result.logicalEpisodes[1].hasCanonicalEvidence,false);
  assert.equal(result.logicalEpisodes[1].sourceSpelling,'alias');
  assert.equal(result.playableItemQualified,false);
});

test('two active resumes quarantine group rather than choose largest position',()=>{
  const result=buildCanonicalProgressView({
    graph,traktShowId:'263102',
    rows:[
      row(c,3,{played:false,positionMs:999}),
      row(a,3,{played:false,positionMs:888}),
    ],
  });
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'PLAYBACK_STATE_CONFLICT');
  assert.equal(result.logicalEpisodes.length,0);
});

test('inconsistent played status quarantined, not merged by OR',()=>{
  const result=buildCanonicalProgressView({
    graph,traktShowId:'263102',
    rows:[row(c,1,{played:true}),row(a,1,{played:false})],
  });
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'PLAYBACK_STATE_CONFLICT');
});

test('duplicate coordinates under one IMDb refuse automatic resolution',()=>{
  const result=buildCanonicalProgressView({
    graph,traktShowId:'263102',rows:[row(c,1),row(c,1),row(a,1)],
  });
  assert.equal(result.qualified,false);
  assert.equal(result.reason,'DUPLICATE_EPISODE_COORDINATE');
});

test('unknown IMDb, negative episode/position and movie-like rows all fail closed',()=>{
  for (const rows of [
    [row('tt99999999',1)],
    [row(c,-1)],
    [row(c,1,{positionMs:-1})],
    [{imdb:c,season:0,episode:1,played:false}],
    [{imdb:c,season:0,episode:1,played:1,positionMs:0}],
  ]) {
    const result=buildCanonicalProgressView({graph,traktShowId:'263102',rows});
    assert.equal(result.qualified,false);
    assert.equal(result.reason,'ROWS_NOT_VERIFIED');
  }
});

test('graph ambiguity across two shows and quarantine conflicts invalidate whole view',()=>{
  const conflicting=structuredClone(graph);
  conflicting.shows['263103']={canonicalImdb:a,aliases:[]};
  assert.equal(verifiedSpellingSet(conflicting,'263102'),null);
  assert.equal(buildCanonicalProgressView({
    graph:conflicting,traktShowId:'263102',rows:example,
  }).qualified,false);
  const quarantined=structuredClone(graph);
  quarantined.conflicts=[{id:a,reason:'collision'}];
  assert.equal(verifiedSpellingSet(quarantined,'263102'),null);
});

test('missing graph, mismatched Trakt identity and unverified same-title spelling fail closed',()=>{
  for (const x of [null,{}, {shows:{}},graph]) {
    const result=buildCanonicalProgressView({
      graph:x,traktShowId:'9999',rows:[row(a,1)],
    });
    assert.equal(result.qualified,false);
  }
  assert.deepEqual(verifiedSpellingSet(graph,'263102'),[c,a]);
});

test('never smuggle NextUp item IDs or side effects into a pure read model',()=>{
  for(const file of ['../src/jellyfin-gateway.mjs','../src/index.mjs']){
    assert.doesNotMatch(readFileSync(new URL(file,import.meta.url),'utf8'),
      /nextup-canonical-progress-view/);
  }
  const source=readFileSync(
    new URL('../src/nextup-canonical-progress-view.mjs',import.meta.url),'utf8');
  for(const phrase of ['node:http','fetch(','cacheSet(','INSERT INTO','UPDATE watch_state']){
    assert.equal(source.includes(phrase),false);
  }
});
