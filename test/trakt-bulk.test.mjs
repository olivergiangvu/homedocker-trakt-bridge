import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';

function client() {
  const db = { getTokens() { return null; } };
  return new TraktClient({ pullMaxPages: 500 }, db);
}

test('bulk played writes one nested Trakt history request for listed videos only', async () => {
  const trakt = client();
  trakt.resolveShow = async () => ({ kind: 'show', show: { ids: { trakt: 42, imdb: 'tt1234567' } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const event = {
    event: 'played', scope: 'series', metaId: 'tt1234567', at: 1790750000, part: 1, parts: 1,
    videos: [
      { videoId: 'tt1234567:2:2', season: 2, episode: 2 },
      { videoId: 'tt1234567:1:3', season: 1, episode: 3 },
      { videoId: 'tt1234567:2:1', season: 2, episode: 1 },
      { videoId: 'tt1234567:2:1', season: 2, episode: 1 },
    ],
    ids: { imdb: 'tt1234567' },
  };

  const result = await trakt.applyEvent('p1', event, { kind: 'bulk-history-add' });
  assert.equal(result.action, 'history:bulk-add');
  assert.equal(result.videos, 4);
  assert.equal(call.path, '/sync/history');
  assert.equal(call.options.method, 'POST');
  const watchedAt = new Date(event.at * 1000).toISOString();
  assert.deepEqual(call.options.body, {
    shows: [{
      ids: { trakt: 42, imdb: 'tt1234567' },
      seasons: [
        { number: 1, episodes: [{ number: 3, watched_at: watchedAt }] },
        { number: 2, episodes: [{ number: 1, watched_at: watchedAt }, { number: 2, watched_at: watchedAt }] },
      ],
    }],
  });
});

test('bulk unplayed removes only listed episodes in one request', async () => {
  const trakt = client();
  trakt.resolveShow = async () => ({ kind: 'show', show: { ids: { trakt: 42, tvdb: 123 } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const event = {
    event: 'unplayed', scope: 'season', metaId: 'tvdb:123', season: 3, part: 1, parts: 1,
    videos: [
      { videoId: 'tvdb:123:3:1', season: 3, episode: 1 },
      { videoId: 'tvdb:123:3:2', season: 3, episode: 2 },
    ],
    ids: { tvdb: '123' },
  };

  const result = await trakt.applyEvent('p1', event, { kind: 'bulk-history-remove' });
  assert.equal(result.action, 'history:bulk-remove');
  assert.equal(call.path, '/sync/history/remove');
  assert.deepEqual(call.options.body, {
    shows: [{
      ids: { trakt: 42, tvdb: 123 },
      seasons: [{ number: 3, episodes: [{ number: 1 }, { number: 2 }] }],
    }],
  });
});

test('bulk anime spaced videos fail closed before writing Trakt', async () => {
  const trakt = client();
  trakt.resolveShow = async () => { throw new Error('should not resolve'); };
  trakt.request = async () => { throw new Error('should not request'); };
  await assert.rejects(
    () => trakt.applyEvent('p1', {
      event: 'played', scope: 'series', metaId: 'tt1234567', part: 1, parts: 1,
      videos: [{ videoId: 'kitsu:123:7', season: 1, episode: 7 }],
    }, { kind: 'bulk-history-add' }),
    /Anime\/absolute episode numbering/,
  );
});
