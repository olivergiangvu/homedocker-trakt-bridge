import test from 'node:test';
import assert from 'node:assert/strict';

import {
  directBulkHistoryPayload,
  directHistoryPayload,
  directMediaTarget,
  directScrobblePayload,
  directWatchlistPayload,
  mediaFromScrobbleResponse,
  sourceSemanticMediaKey,
} from '../src/direct-media.mjs';

test('episode scrobble uses show provider ids directly without canonical resolution', () => {
  const event = {
    scope: 'episode',
    metaId: 'tt14261112',
    videoId: 'tt14261112:2:5',
    season: 2,
    episode: 5,
    ids: { tmdb: 111111, tvdb: 473467 },
  };
  const { target, body } = directScrobblePayload(event, 34.92);

  assert.equal(target.kind, 'episode');
  assert.deepEqual(body, {
    show: {
      ids: {
        imdb: 'tt14261112',
        tmdb: 111111,
        tvdb: 473467,
      },
    },
    episode: { season: 2, number: 5 },
    progress: 34.92,
  });
});

test('episode history uses nested show/season/episode payload like Odin', () => {
  const event = {
    scope: 'episode',
    metaId: 'tt14261112',
    videoId: 'tt14261112:2:5',
    season: 2,
    episode: 5,
    at: 1791119452,
  };
  const add = directHistoryPayload(event, false);
  const remove = directHistoryPayload(event, true);

  assert.equal(add.body.shows[0].ids.imdb, 'tt14261112');
  assert.equal(add.body.shows[0].seasons[0].episodes[0].number, 5);
  assert.ok(add.body.shows[0].seasons[0].episodes[0].watched_at);
  assert.deepEqual(remove.body.shows[0].seasons[0].episodes[0], { number: 5 });
});

test('movie and series watchlist payloads use direct provider ids', () => {
  assert.deepEqual(
    directWatchlistPayload({ scope: 'movie', metaId: 'tt1234567' }).body,
    { movies: [{ ids: { imdb: 'tt1234567' } }] },
  );
  assert.deepEqual(
    directWatchlistPayload({ scope: 'series', metaId: 'tt7654321' }).body,
    { shows: [{ ids: { imdb: 'tt7654321' } }] },
  );
});

test('bulk history groups episodes without resolving the show through public metadata', () => {
  const { body } = directBulkHistoryPayload({
    scope: 'series',
    metaId: 'tt1234567',
    at: 1000,
    videos: [
      { videoId: 'tt1234567:2:2', season: 2, episode: 2 },
      { videoId: 'tt1234567:1:3', season: 1, episode: 3 },
      { videoId: 'tt1234567:2:1', season: 2, episode: 1 },
    ],
  }, true);

  assert.deepEqual(
    body.shows[0].seasons.map((s) => [s.number, s.episodes.map((e) => e.number)]),
    [[1, [3]], [2, [1, 2]]],
  );
});

test('source semantic key stays independent of provider-id subset differences', () => {
  const base = {
    scope: 'episode',
    metaId: 'tt14261112',
    videoId: 'tt14261112:2:5',
    season: 2,
    episode: 5,
  };
  assert.equal(
    sourceSemanticMediaKey({ ...base, ids: { imdb: 'tt14261112' } }),
    sourceSemanticMediaKey({ ...base, ids: { imdb: 'tt14261112', tmdb: 111111, tvdb: 473467 } }),
  );
});

test('scrobble response can teach canonical Trakt ids without a public lookup', () => {
  const target = directMediaTarget({
    scope: 'episode',
    metaId: 'tt14261112',
    season: 2,
    episode: 5,
  });
  const media = mediaFromScrobbleResponse(target, {
    action: 'pause',
    episode: { ids: { trakt: 999, tvdb: 777 } },
    show: { ids: { trakt: 188204, imdb: 'tt14261112' } },
  });

  assert.equal(media.episode.ids.trakt, 999);
  assert.equal(media.show.ids.trakt, 188204);
});

test('unsupported anime absolute numbering remains fail-safe', () => {
  assert.throws(
    () => directMediaTarget({
      scope: 'episode',
      metaId: 'kitsu:1',
      videoId: 'kitsu:1:12',
      season: 1,
      episode: 12,
    }),
    /No Trakt-compatible provider ID|Anime\/absolute/,
  );
});
