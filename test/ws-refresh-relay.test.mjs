import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import WebSocket, { WebSocketServer } from 'ws';
import { installWatchRefreshRelay, matchesAioSocketPath } from '../src/ws-refresh-relay.mjs';

test('exact Jellyfin WS paths only, not HTTP or other services', () => {
  assert.ok(matchesAioSocketPath('/jellyfin/socket'));
  assert.ok(matchesAioSocketPath('/jellyfin/u/abc123/socket'));
  assert.ok(matchesAioSocketPath('/jellyfin/p/abc123/emby/websocket'));
  assert.equal(matchesAioSocketPath('/Shows/NextUp'), false);
  assert.equal(matchesAioSocketPath('/jellyfin/foo/socket/evil'), false);
});

test('relay sends one refresh for a new Bridge import and forwards WS traffic', async t => {
  const dir=mkdtempSync(join(tmpdir(),'bridge-ws-'));
  const dbPath=join(dir,'aio.db');
  const db=new DatabaseSync(dbPath);
  const userId='aaaaaaaaaaaaaaaa';
  db.exec(`
    CREATE TABLE watch_sinks (id TEXT, addon_name TEXT, uuid TEXT, persona TEXT, retired_at INTEGER);
    CREATE TABLE watch_state (uuid TEXT,persona TEXT,sink_id TEXT,origin TEXT,updated_at INTEGER,item_key TEXT);
    INSERT INTO watch_sinks VALUES ('bridge','homedocker-trakt-bridge','config1','',NULL);
  `);
  t.after(()=> {db.close();rmSync(dir,{recursive:true,force:true});});
  const upstream=http.createServer((req,res)=>{
    if (req.url==='/jellyfin/Users/Me' && req.headers['x-emby-token']==='authorized') {
      res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({Id:userId}));
      return;
    }
    res.writeHead(401);res.end();
  });
  const upstreamWs=new WebSocketServer({noServer:true});
  upstream.on('upgrade',(req,socket,head)=>{
    if (req.url!=='/jellyfin/socket?api_key=authorized') {socket.destroy();return;}
    upstreamWs.handleUpgrade(req,socket,head,ws=>{
      ws.on('message',data=>ws.send(data));
    });
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(()=>{upstreamWs.close();upstream.close();});
  const base='http://127.0.0.1:'+upstream.address().port+'/jellyfin';
  const gateway=http.createServer((_req,res)=>{res.writeHead(404);res.end();});
  const stop=installWatchRefreshRelay(gateway,{
    enabled:true,options:{base,userId},dbPath
  });
  t.after(()=>{stop();gateway.close();});
  gateway.listen(0,'127.0.0.1');await once(gateway,'listening');
  const url='ws://127.0.0.1:'+gateway.address().port+'/jellyfin/socket?api_key=authorized';
  const client=new WebSocket(url);
  t.after(()=>client.terminate());
  await once(client,'open');

  const messages=[];
  client.on('message',msg=>messages.push(String(msg)));
  client.send('original-message');
  await new Promise((resolve,reject)=>{
    const deadline=setTimeout(()=>reject(new Error('upstream WS was not forwarded')),4000);
    const poll=setInterval(()=>{
      if (messages.includes('original-message')){clearTimeout(deadline);clearInterval(poll);resolve();}
    },30);
  });
  db.prepare('INSERT INTO watch_state VALUES (?,?,?,?,?,?)').run(
    'config1','','bridge','import',Date.now(),'e|tt00000001:1:1'
  );
  await new Promise((resolve,reject)=>{
    const deadline=setTimeout(()=>reject(new Error('import notification was not forwarded')),6500);
    const poll=setInterval(()=>{
      if (messages.some(m=>{try {
        const x=JSON.parse(m);
        return x.MessageType==='UserDataChanged' && x.Data?.UserId===userId;
      } catch{return false;}})){
        clearTimeout(deadline);clearInterval(poll);resolve();
      }
    },30);
  });
  assert.equal(messages.filter(m=>m.includes('UserDataChanged')).length,1);
});
