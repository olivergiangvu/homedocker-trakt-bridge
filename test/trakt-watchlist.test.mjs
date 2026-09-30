import test from 'node:test';
import assert from 'node:assert/strict';
import { TraktClient } from '../src/trakt.mjs';

function client() {
  const db = { getTokens() { return null; } };
  return new TraktClient({ pullMaxPages: 500 }, db);
}

test('watchlisted movie writes POST /sync/watchlist with movie ids', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => ({ kind: 'movie', movie: { ids: { trakt: 1, imdb: 'tt1234567' } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const result = await trakt.applyEvent('p1', { event: 'watchlisted', scope: 'movie' }, { kind: 'watchlist-add' });
  assert.equal(result.action, 'watchlist:add');
  assert.equal(call.path, '/sync/watchlist');
  assert.equal(call.options.method, 'POST');
  assert.deepEqual(call.options.body, { movies: [{ ids: { trakt: 1, imdb: 'tt1234567' } }] });
});

test('unwatchlisted series writes POST /sync/watchlist/remove with show ids', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => ({ kind: 'show', show: { ids: { trakt: 2, tvdb: 81189 } } });
  let call;
  trakt.request = async (profileId, path, options) => { call = { profileId, path, options }; return {}; };

  const result = await trakt.applyEvent('p1', { event: 'unwatchlisted', scope: 'series' }, { kind: 'watchlist-remove' });
  assert.equal(result.action, 'watchlist:remove');
  assert.equal(call.path, '/sync/watchlist/remove');
  assert.equal(call.options.method, 'POST');
  assert.deepEqual(call.options.body, { shows: [{ ids: { trakt: 2, tvdb: 81189 } }] });
});

test('ignored playback event does not resolve media or touch Trakt', async () => {
  const trakt = client();
  trakt.resolveMedia = async () => { throw new Error('should not resolve'); };
  trakt.request = async () => { throw new Error('should not request'); };
  const result = await trakt.applyEvent('p1', {}, { kind: 'ignore', reason: 'duration_unknown' });
  assert.deepEqual(result, { ignored: 'duration_unknown' });
});
