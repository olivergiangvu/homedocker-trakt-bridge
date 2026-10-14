import test from 'node:test';
import assert from 'node:assert/strict';
import { filterNextUpForVerifiedAliases } from '../src/jellyfin-nextup-filter.mjs';
import { loadVerifiedIdentityGraph, observeVerifiedShowIdentity } from '../src/identity-continuity.mjs';

const canonical = 'tt44051354', alias = 'tt44094505';
function graph() {
  const c = new Map();
  const db = {
    cacheGet: k => c.get(k) ?? null,
    cacheSet: (k, v) => c.set(k, structuredClone(v)),
  };
  observeVerifiedShowIdentity(db, 'p', {
    trakt: 263102, imdb: canonical, tmdb: 276470,
  }, alias);
  return loadVerifiedIdentityGraph(db, 'p');
}
const item = (series, episode, {
  ticks = 0, time = undefined, imdb = undefined,
} = {}) => ({
  Id: series + ':' + episode,
  SeriesId: series,
  Type: 'Episode',
  IndexNumber: episode,
  UserData: {
    PlaybackPositionTicks: ticks,
    ...(time ? { LastPlayedDate: time } : {}),
  },
  ...(imdb ? { ProviderIds: { Imdb: imdb } } : {}),
});
const seriesToImdb = { opaqueSeriesA: alias, opaqueSeriesC: canonical };
const resolveSeriesImdb = id => seriesToImdb[id] ?? null;

test('canonical resume wins and all original AIO episode IDs remain unchanged', async () => {
  const response = {
    Items: [item('opaqueSeriesA', 9),
      item('opaqueSeriesC', 10, { ticks: 300000000 })],
    TotalRecordCount: 2,
    StartIndex: 0,
  };
  const saved = structuredClone(response);
  const filtered = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(), resolveSeriesImdb,
  });
  assert.equal(filtered.Items.length, 1);
  assert.strictEqual(filtered.Items[0], response.Items[1]);
  assert.equal(filtered.Items[0].SeriesId, 'opaqueSeriesC');
  assert.equal(filtered.TotalRecordCount, 1);
  assert.deepEqual(response, saved);
});

test('after playback using old alias newer timestamp wins without AIO drop', async () => {
  const response = {
    Items: [
      item('opaqueSeriesC', 10, { ticks: 100000000, time: '2026-10-13T13:00:00Z' }),
      item('opaqueSeriesA', 11, { ticks: 700000000, time: '2026-10-14T13:00:00Z' }),
    ],
    TotalRecordCount: 2,
  };
  const result = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(), resolveSeriesImdb,
  });
  assert.equal(result.Items.length, 1);
  assert.equal(result.Items[0].Id, 'opaqueSeriesA:11');
});

test('two simultaneous incomparable resumes preserve both records visibly', async () => {
  const response = { Items: [
    item('opaqueSeriesC', 10, { ticks: 300000000 }),
    item('opaqueSeriesA', 9, { ticks: 400000000 }),
  ], TotalRecordCount: 2 };
  const result = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(), resolveSeriesImdb,
  });
  assert.strictEqual(result, response);
});

test('unresolved series details, malformed reply or missing graph passes through', async () => {
  const response = { Items: [item('opaqueSeriesA', 9), item('opaqueSeriesC', 10)],
    TotalRecordCount: 2 };
  assert.strictEqual(await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(), resolveSeriesImdb: () => { throw Error('upstream down') },
  }), response);
  assert.strictEqual(await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: { shows: {}, conflicts: [] }, resolveSeriesImdb,
  }), response);
  assert.strictEqual(await filterNextUpForVerifiedAliases({ Foo: 1 }, {
    verifiedGraph: graph(), resolveSeriesImdb,
  }).then(x => x.Foo), 1);
});

test('episode ProviderIds.Imdb must never be confused with series provider id', async () => {
  const response = { Items: [
    item('unknownA', 9, { imdb: alias }), item('unknownC', 10, { imdb: canonical }),
  ], TotalRecordCount: 2 };
  const result = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(), resolveSeriesImdb: async () => null,
  });
  assert.strictEqual(result, response);
});

test('different show remains independent, exact SeriesId lookup only', async () => {
  const response = { Items: [
    item('opaqueSeriesA', 9), item('otherSeries', 1),
    item('opaqueSeriesC', 10, { ticks: 1000 }),
  ], TotalRecordCount: 3 };
  const result = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(),
    resolveSeriesImdb: id => id === 'otherSeries' ? 'tt12345678' : resolveSeriesImdb(id),
  });
  assert.equal(result.Items.length, 2);
  assert.ok(result.Items.some(x => x.SeriesId === 'otherSeries'));
});

test('at most 100 items and no change to upstream response for unknown shapes', async () => {
  const response = { Items: Array.from({ length: 101 }, () => item('opaqueSeriesA', 9)) };
  let calls = 0;
  const result = await filterNextUpForVerifiedAliases(response, {
    verifiedGraph: graph(),
    resolveSeriesImdb: () => { calls++; return alias },
  });
  assert.strictEqual(result, response);
  assert.equal(calls, 0);
});
