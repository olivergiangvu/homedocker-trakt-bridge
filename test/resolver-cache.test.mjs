import test from 'node:test';
import assert from 'node:assert/strict';

import { TraktClient } from '../src/trakt.mjs';

function fakeDb() {
  const rows = new Map();
  return {
    rows,
    cacheGet(key) { return rows.get(key) ?? null; },
    cacheSet(key, value) { rows.set(key, structuredClone(value)); },
  };
}

function config() {
  return {
    historyDedupeSeconds: 300,
    pullIdentityMode: 'trakt',
  };
}

const fullEpisodeEvent = {
  scope: 'episode',
  metaId: 'tt36885662',
  videoId: 'tt36885662:1:2',
  season: 1,
  episode: 2,
  ids: {
    imdb: 'tt36885662',
    tmdb: 291084,
    tvdb: 463505,
  },
};

const sparseEpisodeEvent = {
  scope: 'episode',
  metaId: 'tt36885662',
  videoId: 'tt36885662:1:2',
  season: 1,
  episode: 2,
};

test('show resolver cache survives provider-id subset differences', async () => {
  const db = fakeDb();
  const client = new TraktClient(config(), db);
  let lookups = 0;

  client.lookupExternal = async (ids, type) => {
    lookups += 1;
    assert.equal(type, 'show');
    return {
      type: 'show',
      show: {
        ids: {
          trakt: 285217,
          imdb: 'tt36885662',
          tmdb: 291084,
          tvdb: 463505,
        },
      },
    };
  };

  const first = await client.resolveShow(fullEpisodeEvent);
  const second = await client.resolveShow(sparseEpisodeEvent);

  assert.equal(first.show.ids.trakt, 285217);
  assert.equal(second.show.ids.trakt, 285217);
  assert.equal(lookups, 1, 'same show must not be re-resolved when later event carries fewer provider IDs');

  assert.ok(db.rows.has('resolver:v2:show:imdb:tt36885662'));
  assert.ok(db.rows.has('resolver:v2:show:tmdb:291084'));
  assert.ok(db.rows.has('resolver:v2:show:tvdb:463505'));
  assert.ok(db.rows.has('resolver:v2:show:trakt:285217'));
});

test('episode resolver cache keys by canonical Trakt show and season/episode', async () => {
  const db = fakeDb();
  const client = new TraktClient(config(), db);
  let showLookups = 0;
  let episodeLookups = 0;

  client.lookupExternal = async (_ids, type) => {
    showLookups += 1;
    assert.equal(type, 'show');
    return {
      type: 'show',
      show: {
        ids: {
          trakt: 285217,
          imdb: 'tt36885662',
          tmdb: 291084,
          tvdb: 463505,
        },
      },
    };
  };

  client.publicRequest = async (path) => {
    episodeLookups += 1;
    assert.equal(path, '/shows/285217/seasons/1/episodes/2');
    return { ids: { trakt: 13714753, tvdb: 11444592 } };
  };

  const first = await client.resolveEpisode(fullEpisodeEvent);
  const second = await client.resolveEpisode(sparseEpisodeEvent);

  assert.equal(first.episode.ids.trakt, 13714753);
  assert.equal(second.episode.ids.trakt, 13714753);
  assert.equal(showLookups, 1);
  assert.equal(episodeLookups, 1, 'episode lookup must not repeat for the same canonical show/season/episode');
  assert.ok(db.rows.has('episode:v2:trakt-show:285217:1:2'));
});

test('movie resolver cache survives provider-id subset differences', async () => {
  const db = fakeDb();
  const client = new TraktClient(config(), db);
  let lookups = 0;

  client.lookupExternal = async (_ids, type) => {
    lookups += 1;
    assert.equal(type, 'movie');
    return {
      type: 'movie',
      movie: {
        title: 'Example Movie',
        year: 2026,
        ids: {
          trakt: 900001,
          imdb: 'tt12345678',
          tmdb: 12345,
        },
      },
    };
  };

  const full = {
    scope: 'movie',
    metaId: 'tt12345678',
    ids: { imdb: 'tt12345678', tmdb: 12345 },
  };
  const sparse = {
    scope: 'movie',
    metaId: 'tt12345678',
  };

  await client.resolveMovie(full);
  await client.resolveMovie(sparse);

  assert.equal(lookups, 1);
  assert.ok(db.rows.has('resolver:v2:movie:imdb:tt12345678'));
  assert.ok(db.rows.has('resolver:v2:movie:tmdb:12345'));
  assert.ok(db.rows.has('resolver:v2:movie:trakt:900001'));
});

test('legacy resolver entries are promoted into alias/canonical cache without an upstream lookup', async () => {
  const db = fakeDb();
  const client = new TraktClient(config(), db);

  const legacyShowKey = 'show:tt36885662:{"imdb":"tt36885662"}';
  db.cacheSet(legacyShowKey, {
    ids: {
      trakt: 285217,
      imdb: 'tt36885662',
      tmdb: 291084,
      tvdb: 463505,
    },
  });

  client.lookupExternal = async () => {
    throw new Error('legacy cache should avoid upstream lookup');
  };

  const resolved = await client.resolveShow(sparseEpisodeEvent);
  assert.equal(resolved.show.ids.trakt, 285217);
  assert.ok(db.rows.has('resolver:v2:show:imdb:tt36885662'));
  assert.ok(db.rows.has('resolver:v2:show:trakt:285217'));
});
