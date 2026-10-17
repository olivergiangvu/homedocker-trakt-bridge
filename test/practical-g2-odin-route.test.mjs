import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyOdinPilotRequest as route,
  createOdinPilotCounters,
} from '../src/odin-pilot-route.mjs';

const alias = 'john_doe_12345678';
const scenarios = [
  ['GET', '/jellyfin/Shows/NextUp?Limit=20', 'nextup'],
  ['GET', '/jellyfin/Shows/NextUp?UserId=private&Limit=17', 'nextup'],
  ['GET', '/jellyfin/UserItems/Resume?Limit=20', 'resume'],
  ['GET', '/jellyfin/Users/' + alias + '/Items/Resume', 'resume'],
  ['GET', '/jellyfin/Users/Me', 'userdata'],
  ['GET', '/jellyfin/Users/' + alias + '/Items?Recursive=true', 'userdata'],
  ['POST', '/jellyfin/Users/AuthenticateByName', 'authentication'],
  ['POST', '/jellyfin/Sessions/Playing', 'playback_write'],
  ['POST', '/jellyfin/Sessions/Playing/Progress', 'playback_write'],
  ['POST', '/jellyfin/Sessions/Playing/Stopped', 'playback_write'],
  ['GET', '/jellyfin/Items/series_id?Fields=ProviderIds', 'other_jellyfin'],
  ['GET', '/jellyfin/System/Info/Public', 'other_jellyfin'],
  ['GET', '/jellyfin/Shows/NextUp/Other', 'other_jellyfin'],
  ['POST', '/jellyfin/Shows/NextUp', 'other_jellyfin'],
  ['GET', '/Shows/NextUp?Limit=20', 'other_aio'],
  ['GET', '/jellyfin/u/alias/Shows/NextUp', 'other_jellyfin'],
  ['GET', '/jellyfin/Shows%2fNextUp', 'other_jellyfin'],
];

test('Odin observe-only default classifies exact route and passes EVERYTHING to AIO', () => {
  for (const [method, path, expected] of scenarios) {
    const result = route(method, path);
    assert.equal(result.category, expected, method + ': ' + expected);
    assert.equal(result.upstream, 'aio');
    assert.ok(!Object.hasOwn(result, 'upstreamPath'));
  }
});

test('Odin intercept candidate rewrites ONLY exact GET prefixed NextUp', () => {
  for (const [method, path, expected] of scenarios) {
    const r = route(method, path, { mode: 'intercept' });
    assert.equal(r.category, expected);
    if (method === 'GET' && expected === 'nextup') {
      assert.equal(r.upstream, 'gateway');
      assert.equal(r.upstreamPath, '/Shows/NextUp');
    } else {
      assert.equal(r.upstream, 'aio');
      assert.equal(r.upstreamPath, undefined);
    }
  }
});

test('Odin route matching does not leak query, token or user IDs into aggregates', () => {
  const agg = createOdinPilotCounters();
  const secret = 'SECRET_TOKEN_123456789';
  const user = 'MY_PERSONAL_USER_ID';
  const input = [
    'GET', '/jellyfin/Shows/NextUp?Authorization=' + secret + '&UserId=' + user,
  ];
  const result = route(input[0], input[1], { mode: 'observe' });
  agg.record(result);
  const dump = JSON.stringify(agg.snapshot());
  assert.equal(agg.snapshot().nextup, 1);
  assert.equal(agg.snapshot().gatewayForwarded, 0);
  for (const s of [secret, user, 'Authorization', 'UserId', 'MY_PERSONAL']) {
    assert.ok(!dump.includes(s));
  }
  assert.deepEqual(Object.keys(result).sort(), ['category', 'upstream']);
});

test('Odin suspicious URI / invalid method must never intercept', () => {
  for (const target of [
    'http://127.0.0.1/jellyfin/Shows/NextUp',
    '//evil.invalid/jellyfin/Shows/NextUp',
    '/jellyfin/Shows/NextUp#fragment',
    '/jellyfin/Shows/NextUp\\bad',
    '/jellyfin/Shows/NextUp\n',
    '',
    'x'.repeat(5000),
  ]) {
    assert.equal(route('GET', target, { mode: 'intercept' }).upstream, 'aio');
  }
  for (const method of ['TRACE', 'CONNECT', 'get', '']) {
    assert.equal(route(method, '/jellyfin/Shows/NextUp',
      { mode: 'intercept' }).upstream, 'aio');
  }
  assert.throws(() => route('GET', '/jellyfin/Shows/NextUp',
    { mode: 'auto' }), /odin_pilot_mode_invalid/);
});

test('Odin unknown Continue Watching implementation must be observed, not called a Gateway PASS', () => {
  const agg = createOdinPilotCounters();
  for (const request of [
    ['GET', '/jellyfin/UserItems/Resume?Limit=20'],
    ['GET', '/jellyfin/Users/' + alias + '/Items/Resume'],
    ['GET', '/jellyfin/Users/' + alias + '/Items?SortBy=DatePlayed'],
  ]) agg.record(route(...request));
  const result = agg.snapshot();
  assert.equal(result.resume, 2);
  assert.equal(result.userdata, 1);
  assert.equal(result.nextup, 0);
  assert.equal(result.gatewayForwarded, 0);
});
