import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { auditNextUpShadow, summarizeShadowAliasCandidate } from '../src/nextup-pagination-shadow.mjs';
import { observeVerifiedShowIdentity, loadVerifiedIdentityGraph } from '../src/identity-continuity.mjs';

const item = (i, series = 'series-' + i, ticks = 0) => ({
  Id: 'episode-' + i, SeriesId: series, Type: 'Episode',
  UserData: { PlaybackPositionTicks: ticks },
});
function source(n, { countMode = 'prefix', mutate = null, startBroken = false } = {}) {
  const all = Array.from({length:n}, (_, i) => item(i));
  const queries = [];
  const fetchPage = async ({ startIndex, limit, signal }) => {
    assert.ok(signal && !signal.aborted);
    queries.push([startIndex, limit]);
    const Items = structuredClone(all.slice(startIndex, startIndex + limit));
    mutate?.(Items, {startIndex, limit, calls: queries.length});
    return {
      Items,
      StartIndex: startBroken && startIndex > 0 ? 0 : startIndex,
      TotalRecordCount: countMode === 'global' ? n : startIndex + Items.length,
      Count: Items.length,
    };
  };
  return {fetchPage, queries};
}

test('prefix totals 20/20 + 20/20 + 7 and stable second sweep are shadow-qualified', async () => {
  const x = source(47);
  const result = await auditNextUpShadow({fetchPage:x.fetchPage});
  assert.equal(result.qualified, true);
  assert.equal(result.totalItems, 47);
  assert.equal(result.reason, 'DOUBLE_SWEEP_SHORT_END_OBSERVED');
  assert.equal(result.proofType, 'SHADOW_ONLY_NONTRANSACTIONAL');
  assert.deepEqual(x.queries, [
    [0,20],[20,20],[40,20],[0,20],[20,20],[40,20],
  ]);
  assert.equal(result.requests, 6);
  assert.equal(result.pages, 3);
});

test('global totals and full first 50 page cannot by themselves block shadow observation', async () => {
  const x = source(60, {countMode:'global'});
  const result = await auditNextUpShadow({fetchPage:x.fetchPage,limit:50});
  assert.equal(result.qualified, true);
  assert.equal(result.totalItems, 60);
  assert.deepEqual(x.queries, [[0,50],[50,50],[0,50],[50,50]]);
});

test('full pages through bounded window fail closed without manufacturing a count', async () => {
  const x = source(250);
  const result = await auditNextUpShadow({fetchPage:x.fetchPage});
  assert.equal(result.qualified, false);
  assert.equal(result.reason, 'FULL_PAGE_BUDGET_EXHAUSTED');
  assert.equal(result.items, null);
  assert.equal(x.queries.length, 4);
});

test('50/50+50/50 is not proof even when upstream prefix total says 50', async () => {
  const x = source(200);
  const result = await auditNextUpShadow({fetchPage:x.fetchPage,limit:50});
  assert.equal(result.qualified, false);
  assert.equal(result.reason, 'ITEM_BUDGET');
  assert.equal(x.queries.length, 2);
});

test('ignored StartIndex, overlapping IDs and empty tail do not qualify', async () => {
  const broken = source(30,{startBroken:true});
  assert.equal((await auditNextUpShadow({fetchPage:broken.fetchPage})).reason, 'PAGE_CONTRACT');
  const overlapping = source(30, {
    mutate: (items, {startIndex}) => {
      if (startIndex === 20 && items.length) items[0].Id = 'episode-0';
    },
  });
  assert.equal((await auditNextUpShadow({fetchPage:overlapping.fetchPage})).reason,
    'OVERLAPPING_PAGES');
  const empty = source(20);
  assert.equal((await auditNextUpShadow({fetchPage:empty.fetchPage})).reason,
    'EMPTY_TAIL_UNPROVEN');
});

test('unverifiable totals and page Count fail closed without changing the source', async () => {
  let bad = source(28);
  const incoherent = async (q) => {
    const page = await bad.fetchPage(q);
    if (q.startIndex === 20) page.TotalRecordCount = 500;
    return page;
  };
  assert.equal((await auditNextUpShadow({fetchPage:incoherent})).reason, 'INCOHERENT_TOTALS');
  bad = source(28);
  const wrongCount = async (q) => {
    const page = await bad.fetchPage(q);
    page.Count += 1;
    return page;
  };
  assert.equal((await auditNextUpShadow({fetchPage:wrongCount})).reason, 'PAGE_CONTRACT');
});

test('mutable first or second page on replay is never treated as a stable snapshot', async () => {
  const x = source(25, { mutate: (items, {calls}) => {
    if (calls === 3) items[0].UserData.PlaybackPositionTicks = 999;
  }});
  const r = await auditNextUpShadow({fetchPage:x.fetchPage});
  assert.equal(r.qualified, false);
  assert.equal(r.reason,'SNAPSHOT_CHANGED');
  assert.equal(r.items,null);
  assert.deepEqual(x.queries,[[0,20],[20,20],[0,20]]);
});

test('authentication/transport rejection, oversized response, budget validation fail closed', async () => {
  assert.equal((await auditNextUpShadow({
    fetchPage:async()=>{throw Error('x-emby-token=SENSITIVE');},
  })).reason, 'PAGE_UNAVAILABLE');
  assert.equal((await auditNextUpShadow({
    fetchPage: async()=>({
      StartIndex:0,TotalRecordCount:1,
      Items:[{...item(0), Extra:'a'.repeat(2*1024*1024)}],
    }),
  })).reason,'PAGE_TOO_LARGE');
  await assert.rejects(() => auditNextUpShadow({fetchPage: async()=>({}),limit:51}),
    /invalid_rc3_shadow_bounds/);
  assert.equal((await auditNextUpShadow({
    fetchPage:async()=> new Promise(()=>{}),timeoutMs:100,
  })).reason,'PAGE_UNAVAILABLE');
});

test('offline verified alias projection is numerical only, never supplies replacement pages', async () => {
  const all = [item(1,'seriesAlias'),item(2,'seriesCanon',999)];
  const fetchPage = async ({startIndex}) => ({
    StartIndex:startIndex, TotalRecordCount:all.length,
    Items: startIndex === 0 ? structuredClone(all) : [],
  });
  const audit = await auditNextUpShadow({fetchPage,limit:20});
  assert.equal(audit.qualified,true);
  const cache = new Map();
  const db = {cacheGet:k=>cache.get(k),cacheSet:(k,v)=>cache.set(k,v)};
  observeVerifiedShowIdentity(db,'p',{trakt:263102,imdb:'tt44051354',tmdb:276470},'tt44094505');
  const graph = loadVerifiedIdentityGraph(db,'p');
  const projection = await summarizeShadowAliasCandidate(audit,{
    verifiedGraph:graph,
    resolveSeriesImdb:id=>id==='seriesCanon'?'tt44051354':'tt44094505',
  });
  assert.deepEqual(projection,{
    decision:'SHADOW_CANDIDATE_ONLY',removed:1,observedItems:2,
    clientResponseChanged:false,
  });
  assert.equal(audit.items.length,2);
  const incomplete = await summarizeShadowAliasCandidate({qualified:false});
  assert.deepEqual(incomplete,{decision:'NOT_QUALIFIED',removed:0});
});

test('two active resumes and unknown metadata never produce a shadow removal', async () => {
  const all = [item(1,'seriesAlias',99),item(2,'seriesCanon',999)];
  const audit = await auditNextUpShadow({
    fetchPage:async({startIndex})=>({StartIndex:startIndex,TotalRecordCount:2,
      Items:startIndex===0?all:[]}),
  });
  const cache = new Map();
  const db = {cacheGet:k=>cache.get(k),cacheSet:(k,v)=>cache.set(k,v)};
  observeVerifiedShowIdentity(db,'p',{trakt:263102,imdb:'tt44051354'},'tt44094505');
  const graph = loadVerifiedIdentityGraph(db,'p');
  const summary = await summarizeShadowAliasCandidate(audit,{
    verifiedGraph:graph,
    resolveSeriesImdb:id=>id==='seriesCanon'?'tt44051354':'tt44094505',
  });
  assert.equal(summary.removed,0);
  assert.equal(summary.decision,'NO_VERIFIED_REMOVAL');
});

test('production Gateway remains isolated; no RC3 import or runtime switch', () => {
  const gateway = readFileSync(new URL('../src/jellyfin-gateway.mjs', import.meta.url),'utf8');
  const entry = readFileSync(new URL('../src/index.mjs', import.meta.url),'utf8');
  for (const source of [gateway, entry]) {
    assert.doesNotMatch(source,/nextup-pagination-shadow/);
  }
});
