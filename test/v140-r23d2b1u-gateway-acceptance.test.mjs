/**
 * R23D-2B1U — isolated, synthetic HTTP contract acceptance.
 * NO real AIOStreams, Jellyfin, Remux, Trakt credentials, writable DB or host.
 * Only the already implemented read-only Jellyfin Gateway routes are exposed.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { observeVerifiedShowIdentity } from '../src/identity-continuity.mjs';

const PROFILE = 'a'.repeat(24);
const ACCOUNT_A = 'a11aaaaaaaaaaaaa';
const ACCOUNT_B = 'b22bbbbbbbbbbbbb';
const TOKEN_A = 'B1U-synthetic-A-only-token';
const TOKEN_B = 'B1U-synthetic-B-only-token';
const CANON = 'tt44051354';
const ALIAS = 'tt44094505';
const ITEMS_A = [
  { Id:'aliasEp', SeriesId:'seriesAlias', Type:'Episode', IndexNumber:8,
    UserData:{PlaybackPositionTicks:0, LastPlayedDate:'2026-10-11T10:00:00Z'} },
  { Id:'canonEp', SeriesId:'seriesCanon', Type:'Episode', IndexNumber:8,
    UserData:{PlaybackPositionTicks:25000, LastPlayedDate:'2026-10-12T10:00:00Z'} },
];
const NEXT_A = JSON.stringify({ Items: ITEMS_A, TotalRecordCount:2,
  StartIndex:0, OtherOpaque:'AIO-own-data' });
const NEXT_B = JSON.stringify({ Items:[{Id:'B-only',SeriesId:'B-series',
  Type:'Episode',UserData:{PlaybackPositionTicks:111}}],TotalRecordCount:1 });
const RESUME_A = JSON.stringify({
  Items:[
    {Id:'movieA',Type:'Movie',UserData:{PlaybackPositionTicks:222}},
    {Id:'epA',Type:'Episode',SeriesId:'seriesCanon',
     UserData:{PlaybackPositionTicks:333}},
  ],TotalRecordCount:2,StartIndex:20,Source:'upstream-AIO-prefix-count',
},null,2);
const RESUME_B = JSON.stringify({
  Items:[{Id:'movieB',Type:'Movie',UserData:{PlaybackPositionTicks:999}}],
  TotalRecordCount:1,StartIndex:20,Source:'B-only',
},null,2);

async function serve(handler) {
  const server=http.createServer(handler);
  server.listen(0,'127.0.0.1');
  await once(server,'listening');
  return { server, base:'http://127.0.0.1:'+server.address().port };
}
function send(res,status,body,headers={}) {
  res.writeHead(status,{'content-type':'application/json',
    'x-aio-contract':'synthetic-opaque',...headers});
  res.end(body);
}
function readonlyProofDb() {
  const entries=new Map();
  const writeDb={
    cacheGet:key => structuredClone(entries.get(key)??null),
    cacheSet:(key,value)=>entries.set(key,structuredClone(value)),
  };
  observeVerifiedShowIdentity(writeDb,PROFILE,{
    trakt:263102,imdb:CANON,tmdb:276470,
  },ALIAS);
  let reads=0,writes=0;
  const readonly={
    cacheGet(key) {reads++;return structuredClone(entries.get(key)??null);},
    cacheSet() {writes++;throw new Error('SECOND_HISTORY_WRITER_FORBIDDEN');},
    transaction() {writes++;throw new Error('TRANSACTION_FORBIDDEN');},
    prepare() {writes++;throw new Error('SQL_FORBIDDEN');},
  };
  return { db:readonly,counts:()=>({reads,writes}) };
}
async function fixture(t,{resume=false,profile=PROFILE}={}) {
  const calls=[];
  const upstream=await serve((req,res)=>{
    const route=new URL(req.url,'http://fake-upstream.invalid');
    const token=req.headers['x-emby-token'];
    const user=token===TOKEN_A?ACCOUNT_A:token===TOKEN_B?ACCOUNT_B:null;
    calls.push({method:req.method,path:route.pathname,search:route.search,
      user:user??'unauthorized',hasAuthorization:!!token});
    if (req.method!=='GET')return send(res,405,'{"error":"upstream-readonly"}');
    if (!user)return send(res,401,'{"error":"upstream-denied"}');
    if (!route.pathname.startsWith('/jellyfin/')) {
      return send(res,404,'{"error":"non-jellyfin-route"}');
    }
    const path=route.pathname.slice('/jellyfin'.length);
    if (path==='/Users/Me')return send(res,200,JSON.stringify({Id:user}));
    if (path==='/Shows/NextUp')return send(res,200,user===ACCOUNT_A?NEXT_A:NEXT_B);
    if (path==='/Items/seriesAlias'||path==='/Items/seriesCanon') {
      if (user!==ACCOUNT_A)return send(res,403,'{"error":"other-user-series"}');
      return send(res,200,JSON.stringify({Id:path.split('/').pop(),
        Type:'Series',ProviderIds:{Imdb:path.includes('Alias')?ALIAS:CANON}}));
    }
    if (path==='/UserItems/Resume'||/^\/Users\/[A-Za-z0-9_-]{8,128}\/Items\/Resume$/.test(path)) {
      // Important: upstream, NOT the Gateway, authorizes per-user legacy
      // routes. Test cannot certify AIO's real production user ACL.
      const legacy=path.match(/^\/Users\/([^/]+)\/Items\/Resume$/);
      if (legacy && legacy[1]!==user) return send(res,403,'{"error":"upstream-cross-user-block"}');
      if (route.searchParams.get('StartIndex')!=='20')return send(res,400,'{"error":"page"}');
      return send(res,200,user===ACCOUNT_A?RESUME_A:RESUME_B);
    }
    return send(res,404,'{"error":"upstream-unknown"}');
  });
  t.after(()=>upstream.server.close());

  const proof=readonlyProofDb();
  const gateway=createJellyfinGateway({
    db:proof.db,
    config:{
      jellyfinGatewayEnabled:true,
      jellyfinGatewayUpstreamUrl:upstream.base+'/jellyfin',
      jellyfinGatewayUserId:ACCOUNT_A,
      jellyfinGatewayProfileId:profile,
      jellyfinGatewayResumePassThrough:resume,
    },
  });
  gateway.listen(0,'127.0.0.1');
  await once(gateway,'listening');
  t.after(()=>gateway.close());
  const base='http://127.0.0.1:'+gateway.address().port;
  const request=(path,token=TOKEN_A,init={})=>
    fetch(base+path,{...init,headers:{...(token?{'X-Emby-Token':token}:{}),
      ...(init.headers??{})}});
  return {request,calls,proof,gateway};
}
const nextup='/Shows/NextUp?Limit=20';
const resume='/UserItems/Resume?Limit=20&StartIndex=20&MediaTypes=Video';
const legacy=user=>'/Users/'+user+'/Items/Resume?Limit=20&StartIndex=20&MediaTypes=Video';

test('B1U: correct upstream user + verified alias hides only inactive twin without changing source state',async t=>{
  const f=await fixture(t);
  const r=await f.request(nextup);
  assert.equal(r.status,200);
  const body=await r.json();
  assert.deepEqual(body.Items,[ITEMS_A[1]]);
  assert.equal(body.TotalRecordCount,1);
  assert.equal(body.OtherOpaque,'AIO-own-data');
  assert.deepEqual(JSON.parse(NEXT_A).Items,ITEMS_A);
  assert.deepEqual(f.calls.map(c=>c.path),[
    '/jellyfin/Shows/NextUp','/jellyfin/Users/Me',
    '/jellyfin/Items/seriesAlias','/jellyfin/Items/seriesCanon',
  ]);
  assert.ok(f.calls.every(c=>c.method==='GET'&&c.user===ACCOUNT_A));
  assert.equal(f.proof.counts().writes,0);
  assert.equal(f.gateway.gatewayMetricsSnapshot().nextUpRemovedItems,1);
});

test('B1U: other account and invalid token never receive verified canonical A projection',async t=>{
  const f=await fixture(t);
  const b=await f.request(nextup,TOKEN_B);
  assert.equal(b.status,200);
  assert.equal(await b.text(),NEXT_B);
  assert.deepEqual(f.calls.map(c=>c.path),[
    '/jellyfin/Shows/NextUp','/jellyfin/Users/Me',
  ]);
  const denied=await f.request(nextup,'invalid-synthetic');
  assert.equal(denied.status,401);
  assert.equal(await denied.text(),'{"error":"upstream-denied"}');
  assert.equal(f.calls.filter(c=>c.path.startsWith('/jellyfin/Items/')).length,0);
  assert.equal(f.proof.counts().writes,0);
});

test('B1U: wrong or absent profile graph never suppresses AIO episodes',async t=>{
  const f=await fixture(t,{profile:'b'.repeat(24)});
  const r=await f.request(nextup);
  assert.equal(r.status,200);
  assert.equal(await r.text(),NEXT_A);
  assert.equal(f.calls.filter(c=>c.path==='/jellyfin/Users/Me').length,0);
  assert.equal(f.proof.counts().writes,0);
});

test('B1U: Resume is off by default; enabled Resume preserves bytes, pages, movies and AIO ACL',async t=>{
  const off=await fixture(t);
  assert.equal((await off.request(resume)).status,404);
  assert.equal(off.calls.length,0);

  const f=await fixture(t,{resume:true});
  for (const path of [resume,legacy(ACCOUNT_A)]) {
    const a=await f.request(path);
    assert.equal(a.status,200);
    assert.equal(a.headers.get('x-aio-contract'),'synthetic-opaque');
    assert.equal(await a.text(),RESUME_A); // whitespace + totals untouched
  }
  const b=await f.request(resume,TOKEN_B);
  assert.equal(b.status,200);
  assert.equal(await b.text(),RESUME_B);
  const cross=await f.request(legacy(ACCOUNT_B));
  assert.equal(cross.status,403);
  assert.equal(await cross.text(),'{"error":"upstream-cross-user-block"}');
  assert.equal(f.proof.counts().writes,0);
  assert.ok(f.calls.every(c=>c.method==='GET'));
  assert.equal(f.calls.filter(c=>c.path==='/jellyfin/Users/Me').length,0);
  // This proves mocked upstream ACL behavior, not independent Gateway ACL.
});

test('B1U: playback writes, Trakt endpoints and other Jellyfin routes cannot proxy through Gateway',async t=>{
  const f=await fixture(t,{resume:true});
  for (const path of ['/Sessions/Playing','/Sessions/Playing/Progress',
    '/Sessions/Playing/Stopped','/Sync/History','/Users/Me',
    '/Items/seriesAlias','/trakt/sync/history','/Users/'+ACCOUNT_A+'/PlayedItems']) {
    const r=await f.request(path,TOKEN_A,{method:'POST'});
    assert.equal(r.status,404,path);
  }
  for (const path of [nextup,resume,legacy(ACCOUNT_A)]) {
    const r=await f.request(path,TOKEN_A,{method:'POST'});
    assert.equal(r.status,405,path);
    assert.equal(r.headers.get('allow'),'GET');
  }
  assert.equal(f.calls.length,0);
  assert.equal(f.proof.counts().writes,0);
  const internalMetrics=JSON.stringify(f.gateway.gatewayMetricsSnapshot());
  for (const secret of [TOKEN_A,TOKEN_B,ACCOUNT_A,CANON,ALIAS]) {
    assert.equal(internalMetrics.includes(secret),false);
  }
  assert.equal((await f.request('/gateway/metrics')).status,404);
});

test('B1U: series-specific and paginated NextUp are upstream-defined, not rewritten',async t=>{
  const f=await fixture(t);
  for(const path of ['/Shows/NextUp?SeriesId=seriesAlias',
    '/Shows/NextUp?StartIndex=20&Limit=20']) {
    const r=await f.request(path);
    assert.equal(r.status,200);
    assert.equal(await r.text(),NEXT_A);
  }
  assert.equal(f.calls.filter(c=>c.path==='/jellyfin/Users/Me').length,0);
  assert.equal(f.proof.counts().writes,0);
});
