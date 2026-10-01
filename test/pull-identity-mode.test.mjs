import test from 'node:test';
import assert from 'node:assert/strict';

import { rowsForPullIdentity } from '../src/identity-alias.mjs';

const aliases = {
  revision: 1,
  shows: {
    '123': {
      preferredMetaId: 'tt44094505',
      traktImdb: 'tt44051354',
      updatedAt: 0,
    },
  },
};

const row = {
  show: {
    ids: {
      trakt: 123,
      imdb: 'tt44051354',
      tmdb: 276470,
      tvdb: 480791,
    },
  },
  episode: { season: 1, number: 7 },
  progress: 2.55,
};

test('trakt pull identity mode preserves upstream Trakt IMDb spelling', () => {
  const rows = rowsForPullIdentity([row], aliases, 'trakt');
  assert.equal(rows[0].show.ids.imdb, 'tt44051354');
  assert.equal(rows[0].show.ids.tmdb, 276470);
  assert.equal(rows[0].show.ids.tvdb, 480791);
});

test('aiostreams pull identity mode keeps v0.3.5 learned alias rewrite behavior', () => {
  const rows = rowsForPullIdentity([row], aliases, 'aiostreams');
  assert.equal(rows[0].show.ids.imdb, 'tt44094505');
  assert.equal(rows[0].show.ids.tmdb, 276470);
  assert.equal(rows[0].show.ids.tvdb, 480791);
});

test('trakt mode does not mutate the source row', () => {
  const rows = rowsForPullIdentity([row], aliases, 'trakt');
  assert.equal(rows[0], row);
  assert.equal(row.show.ids.imdb, 'tt44051354');
});
