import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { classifyNextUpPagination, assessAdaptiveFirstPage } from '../src/nextup-adaptive-window.mjs';
import { observeVerifiedShowIdentity, loadVerifiedIdentityGraph } from '../src/identity-continuity.mjs';

const old = 'tt44094505', canonical = 'tt44051354';
const row = (i, options={}) => ({
  Id: 'episode-' + i, SeriesId: options.series ?? 'series-' + i,
  Type: 'Episode',
  UserData: {PlaybackPositionTicks:options.ticks??0,
    ...(options.date?{LastPlayedDate:options.date}:{})},
});
function shelf(rows, opts={}) {
  return { Items:structuredClone(rows),
    StartIndex:opts.startIndex ?? 0,
    TotalRecordCount:opts.total ?? rows.length,
    Count:rows.length };
}
function graph() {
  const cache=new Map();
  const db={cacheGet:k=>cache.get(k),cacheSet:(k,v)=>cache.set(k,structuredClone(v))};
  observeVerifiedShowIdentity(db,'p',
    {trakt:263102,imdb:canonical,tmdb:276470},old);
  return loadVerifiedIdentityGraph(db,'p');
}
const metadata = id => id === 'series-A' ? old
  : id === 'series-C' ? canonical : null;
const ids = Array.from({length:50},(_,i)=>
  i===0 ? row(0,{series:'series-A'}) :
  i===1 ? row(1,{series:'series-C',ticks:10000}) :
  row(i));
const first = shelf(ids.slice(0,20),{total:20});
const full = shelf(ids,{total:50});

test('AIO-2026: StartIndex=20 responds with identical first20 and 0 echo', () => {
  const outcome = classifyNextUpPagination({
    first:shelf(ids.slice(0,20),{startIndex:0}),
    second:shelf(ids.slice(0,20),{startIndex:0}),limit:20,
  });
  assert.deepEqual(outcome,{
    mode:'OFFSET_IGNORED_OBSERVED',reason:'REPEATED_FULL_FIRST_PAGE',
    echo:'MISMATCH',overlapCount:20,
  });
});

test('native Jellyfin-like disjoint pages and truthful StartIndex are observed, not guaranteed', () => {
  const result=classifyNextUpPagination({
    first:shelf(ids.slice(0,20),{total:50,startIndex:0}),
    second:shelf(ids.slice(20,40),{total:50,startIndex:20}),limit:20,
  });
  assert.equal(result.mode,'OFFSET_HONORED_OBSERVED');
  assert.equal(result.overlapCount,0);
});

test('backend lacking StartIndex echo is not falsely called offset honoring', () => {
  const a=shelf(ids.slice(0,20));const b=shelf(ids.slice(20,40));
  delete a.StartIndex;delete b.StartIndex;
  const result=classifyNextUpPagination({first:a,second:b});
  assert.equal(result.mode,'OFFSET_PLAUSIBLE_NO_ECHO');
});

test('broken offsets or overlapping pages remain unqualified', () => {
  const a=shelf(ids.slice(0,20));
  const wrong=shelf(ids.slice(20,40),{startIndex:0});
  assert.equal(classifyNextUpPagination({first:a,second:wrong}).reason,
    'SECOND_PAGE_OFFSET_MISMATCH');
  const overlap=shelf([ids[0],...ids.slice(21,40)],{startIndex:20});
  assert.equal(classifyNextUpPagination({first:a,second:overlap}).reason,
    'CROSS_PAGE_OVERLAP');
});

test('50-card full AIO window supports *visible shadow* dedup even with unknown global total', async () => {
  let calls=0;
  const output = await assessAdaptiveFirstPage({
    original:first,windowA:full,windowB:structuredClone(full),
    clientLimit:20,windowLimit:50,identityVerified:true,
    verifiedGraph:graph(),
    resolveSeriesImdb:id=>{ calls++; return metadata(id); },
  });
  assert.equal(output.status,'SHADOW_VISIBLE_CANDIDATE');
  assert.equal(output.reason,'BOUNDED_PREFIX_ONLY');
  assert.equal(output.candidateRemoved,1);
  assert.equal(output.observedWindowItems,50);
  assert.equal(output.projectedFirstPageItems,20);
  assert.equal(output.windowCompleteObserved,false);
  assert.equal(output.clientResponseChanged,false);
  assert.equal(output.globalPaginationQualified,false);
  assert.ok(calls>0);
  // Entire source response, history and IMDb labels stay unmodified.
  assert.equal(full.Items.length,50);
  assert.equal(full.Items[0].Id,'episode-0');
  assert.equal(first.Items.length,20);
});

test('verified duplicate outside client's first20 has no visible candidate change', async () => {
  const rows=[...Array.from({length:50},(_,i)=>row(i))];
  rows[30]=row(30,{series:'series-A'});
  rows[31]=row(31,{series:'series-C',ticks:8000});
  const r=await assessAdaptiveFirstPage({
    original:shelf(rows.slice(0,20)),windowA:shelf(rows),windowB:shelf(rows),
    identityVerified:true,clientLimit:20,verifiedGraph:graph(),
    resolveSeriesImdb:metadata,
  });
  assert.equal(r.status,'SHADOW_NONVISIBLE_CANDIDATE');
  assert.equal(r.candidateRemoved,1);
  assert.equal(r.clientVisibleDifference,false);
});

test('short complete-looking 25 window retains only observational completeness status', async () => {
  const rows=ids.slice(0,25);
  const r=await assessAdaptiveFirstPage({
    original:shelf(rows.slice(0,20)),windowA:shelf(rows),windowB:shelf(rows),
    identityVerified:true,verifiedGraph:graph(),resolveSeriesImdb:metadata,
  });
  assert.equal(r.status,'SHADOW_VISIBLE_CANDIDATE');
  assert.equal(r.reason,'BOUNDED_SHORT_WINDOW');
  assert.equal(r.windowCompleteObserved,true);
  assert.equal(r.globalPaginationQualified,false);
});

test('fail closed on wrong user, changed preview, unstable full window, duplicate episode IDs', async () => {
  let calls=0;
  const input={original:first,windowA:full,windowB:full,
    verifiedGraph:graph(),resolveSeriesImdb:()=>{calls++;return old;}};
  let r=await assessAdaptiveFirstPage({...input,identityVerified:false});
  assert.equal(r.reason,'USER_IDENTITY_UNVERIFIED');
  assert.equal(calls,0);
  const unstable=structuredClone(full);
  unstable.Items[21].UserData.PlaybackPositionTicks=2026;
  r=await assessAdaptiveFirstPage({...input,identityVerified:true,windowB:unstable});
  assert.equal(r.reason,'WINDOW_DRIFT');
  r=await assessAdaptiveFirstPage({...input,identityVerified:true,
    original:shelf(ids.slice(1,21))});
  assert.equal(r.reason,'WINDOW_PREFIX_MISMATCH');
  const repeated=structuredClone(full);
  repeated.Items[20].Id=repeated.Items[19].Id;
  r=await assessAdaptiveFirstPage({...input,identityVerified:true,
    windowA:repeated,windowB:structuredClone(repeated)});
  assert.equal(r.reason,'WINDOW_EPISODE_ID_OVERLAP');
  assert.equal(calls,0);
});

test('ambiguous play states and unknown metadata fail open with zero projected removals', async () => {
  const competitor=structuredClone(ids);
  competitor[0].UserData.PlaybackPositionTicks=10;
  const r=await assessAdaptiveFirstPage({
    original:shelf(competitor.slice(0,20)),
    windowA:shelf(competitor),windowB:shelf(competitor),
    identityVerified:true,verifiedGraph:graph(),resolveSeriesImdb:metadata,
  });
  assert.equal(r.status,'NO_PROVEN_ALIAS_REMOVAL');
  assert.equal(r.candidateRemoved,0);
  const unknown=await assessAdaptiveFirstPage({
    original:first,windowA:full,windowB:full,
    identityVerified:true,verifiedGraph:graph(),resolveSeriesImdb:()=>null,
  });
  assert.equal(unknown.status,'NO_PROVEN_ALIAS_REMOVAL');
});

test('unsupported query shape is not represented: caller must validate first-page query', async () => {
  assert.equal((await assessAdaptiveFirstPage({
    original:first,windowA:full,windowB:full,clientLimit:50,
    identityVerified:true,verifiedGraph:graph(),resolveSeriesImdb:metadata,
  })).reason,'INVALID_BOUNDS');
  const movie=shelf(ids.slice(0,20));
  movie.Items[0].Type='Movie';
  assert.equal((await assessAdaptiveFirstPage({
    original:movie,windowA:full,windowB:full,
    identityVerified:true,verifiedGraph:graph(),resolveSeriesImdb:metadata,
  })).reason,'SOURCE_SCHEMA_UNVERIFIED');
});

test('shadow adapter is not imported into production, no auth/Trakt side effects', () => {
  for (const file of ['../src/jellyfin-gateway.mjs','../src/index.mjs']) {
    assert.doesNotMatch(readFileSync(new URL(file,import.meta.url),'utf8'),
      /nextup-adaptive-window/);
  }
});
