import test from 'node:test';
import assert from 'node:assert/strict';
import {
  identityAliasVersion,
  learnShowAlias,
  loadIdentityAliases,
  rewriteShowRows,
} from '../src/identity-alias.mjs';

function fakeDb() {
  const cache = new Map();
  return {
    cacheGet: (key) => cache.get(key) ?? null,
    cacheSet: (key, value) => cache.set(key, structuredClone(value)),
  };
}

test('learns a profile-scoped preferred IMDb alias for a Trakt show', () => {
  const db = fakeDb();
  const learned = learnShowAlias(
    db,
    'profile-a',
    { trakt: 123, imdb: 'tt44051354' },
    'tt44094505',
  );

  assert.equal(learned.changed, true);
  assert.equal(learned.traktShowId, 123);
  assert.equal(learned.preferredMetaId, 'tt44094505');
  assert.equal(learned.traktImdb, 'tt44051354');

  const state = loadIdentityAliases(db, 'profile-a');
  assert.equal(state.shows['123'].preferredMetaId, 'tt44094505');
  assert.equal(state.shows['123'].traktImdb, 'tt44051354');
});

test('rewrites only the IMDb spelling of the same Trakt show on pull', () => {
  const db = fakeDb();
  learnShowAlias(db, 'profile-a', { trakt: 123, imdb: 'tt44051354' }, 'tt44094505');
  const state = loadIdentityAliases(db, 'profile-a');

  const rows = rewriteShowRows([
    {
      show: { ids: { trakt: 123, imdb: 'tt44051354', tmdb: 456, tvdb: 789 } },
      episode: { season: 1, number: 6 },
    },
    {
      show: { ids: { trakt: 999, imdb: 'tt99999999', tmdb: 111 } },
      episode: { season: 1, number: 1 },
    },
  ], state);

  assert.equal(rows[0].show.ids.imdb, 'tt44094505');
  assert.equal(rows[0].show.ids.trakt, 123);
  assert.equal(rows[0].show.ids.tmdb, 456);
  assert.equal(rows[0].show.ids.tvdb, 789);
  assert.equal(rows[1].show.ids.imdb, 'tt99999999');
});

test('identity alias version changes only when the learned preference changes', () => {
  const db = fakeDb();
  const initial = loadIdentityAliases(db, 'profile-a');
  const v0 = identityAliasVersion(initial);

  learnShowAlias(db, 'profile-a', { trakt: 123, imdb: 'tt44051354' }, 'tt44094505');
  const first = loadIdentityAliases(db, 'profile-a');
  const v1 = identityAliasVersion(first);
  assert.notEqual(v1, v0);

  const repeated = learnShowAlias(
    db,
    'profile-a',
    { trakt: 123, imdb: 'tt44051354' },
    'tt44094505',
  );
  assert.equal(repeated.changed, false);
  assert.equal(identityAliasVersion(loadIdentityAliases(db, 'profile-a')), v1);
});
