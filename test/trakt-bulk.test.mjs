import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';

function client() {
  const db = { getTokens() { return null; } };
  return new TraktClient({ pullMaxPages: 500 }, db);
}

test('bulk played writes one nested show history request', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => ({ kind: 'show', show: { ids: { trakt: 42, imdb: 'tt0168366' } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const event = {
    event: 'played',
    scope: 'series',
    at: 1757441718,
    videos: [
      { videoId: 'tt0168366:2:2', season: 2, episode: 2 },
      { videoId: 'tt0168366:1:1', season: 1, episode: 1 },
      { videoId: 'tt0168366:2:1', season: 2, episode: 1 },
    ],
    part: 1,
    parts: 1,
  };
  const result = await trakt.applyEvent('p1', event, { kind: 'bulk-history-add' });

  assert.equal(call.path, '/sync/history');
  assert.equal(call.options.method, 'POST');
  assert.equal(result.action, 'history:add');
  assert.equal(result.bulk, true);
  assert.equal(result.videos, 3);
  assert.deepEqual(call.options.body, {
    shows: [{
      ids: { trakt: 42, imdb: 'tt0168366' },
      seasons: [
        { number: 1, episodes: [{ number: 1, watched_at: '2025-09-09T16:55:18.000Z' }] },
        { number: 2, episodes: [
          { number: 1, watched_at: '2025-09-09T16:55:18.000Z' },
          { number: 2, watched_at: '2025-09-09T16:55:18.000Z' },
        ] },
      ],
    }],
  });
});

test('bulk unplayed writes only listed episodes to history remove', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => ({ kind: 'show', show: { ids: { trakt: 42, tvdb: 76703 } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const event = {
    event: 'unplayed',
    scope: 'season',
    season: 2,
    videos: [
      { videoId: 'tt0168366:2:1', season: 2, episode: 1 },
      { videoId: 'tt0168366:2:2', season: 2, episode: 2 },
      { videoId: 'tt0168366:2:2', season: 2, episode: 2 },
    ],
    part: 2,
    parts: 3,
  };
  const result = await trakt.applyEvent('p1', event, { kind: 'bulk-history-remove' });

  assert.equal(call.path, '/sync/history/remove');
  assert.equal(result.action, 'history:remove');
  assert.equal(result.bulk, true);
  assert.equal(result.part, 2);
  assert.equal(result.parts, 3);
  assert.deepEqual(call.options.body, {
    shows: [{
      ids: { trakt: 42, tvdb: 76703 },
      seasons: [{ number: 2, episodes: [{ number: 1 }, { number: 2 }] }],
    }],
  });
});

test('bulk history rejects anime absolute-number video ids', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => ({ kind: 'show', show: { ids: { trakt: 42 } } });
  trakt.request = async () => { throw new Error('should not request'); };

  await assert.rejects(
    () => trakt.applyEvent('p1', {
      event: 'played',
      scope: 'series',
      videos: [{ videoId: 'kitsu:42323:7', season: 1, episode: 7 }],
    }, { kind: 'bulk-history-add' }),
    /Anime\/absolute episode numbering/,
  );
});
