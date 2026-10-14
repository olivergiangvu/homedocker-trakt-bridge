import test from 'node:test';
import assert from 'node:assert/strict';
import {
  identityEquivalenceProposal,
  loadVerifiedIdentityGraph,
  observeVerifiedShowIdentity,
  verifiedCanonicalForImdb,
} from '../src/identity-continuity.mjs';
import { TraktClient } from '../src/trakt.mjs';

function fakeDb() {
  const cache = new Map();
  return {
    cacheGet: (k) => structuredClone(cache.get(k) ?? null),
    cacheSet: (k, v) => cache.set(k, structuredClone(v)),
    entries: () => cache.size,
  };
}

const fixed = { trakt: 263102, imdb: 'tt44051354', tmdb: 276470 };

test('verified scrobble learns two IMDb spellings for one show without rewriting history', () => {
  const db = fakeDb();
  const first = observeVerifiedShowIdentity(db, 'profile-1', fixed, 'tt44094505');
  assert.equal(first.status, 'VERIFIED_STORED');
  assert.equal(first.aliasCount, 1);
  const graph = loadVerifiedIdentityGraph(db, 'profile-1');
  assert.deepEqual(graph.shows['263102'], {
    canonicalImdb: 'tt44051354',
    tmdb: 276470,
    tvdb: null,
    aliases: ['tt44094505'],
  });
  assert.deepEqual(verifiedCanonicalForImdb(graph, 'tt44094505'), {
    traktShowId: 263102,
    canonicalImdb: 'tt44051354',
    verified: true,
  });
  const proposal = identityEquivalenceProposal(graph);
  assert.deepEqual(proposal.identities, [{
    type: 'series',
    traktShowId: 263102,
    canonicalImdb: 'tt44051354',
    imdbAliases: ['tt44094505'],
    tmdb: 276470,
    proof: 'successful_trakt_scrobble_response',
  }]);
  assert.equal(proposal.status, 'NOT_SUPPORTED_BY_AIO_UPSTREAM_YET');
  assert.equal(verifyWriteSideEffects(db), 1);
});

function verifyWriteSideEffects(db) {
  return db.entries();
}

test('multiple verified aliases cannot replace prior alias on a newer stop', () => {
  const db = fakeDb();
  for (const alias of ['tt44094505', 'tt44094506', 'tt44094505']) {
    observeVerifiedShowIdentity(db, 'profile-1', fixed, alias);
  }
  const g = loadVerifiedIdentityGraph(db, 'profile-1');
  assert.deepEqual(g.shows['263102'].aliases, ['tt44094505', 'tt44094506']);
  assert.equal(g.revision, 2);
});

test('conflicting Trakt show claim is quarantined without canonical merge', () => {
  const db = fakeDb();
  observeVerifiedShowIdentity(db, 'profile-1', fixed, 'tt44094505');
  const clash = observeVerifiedShowIdentity(db, 'profile-1',
    { trakt: 42, imdb: 'tt99999999' }, 'tt44094505');
  assert.equal(clash.status, 'QUARANTINED');
  assert.equal(clash.reason, 'imdb_claimed_by_different_trakt_show');
  const g = loadVerifiedIdentityGraph(db, 'profile-1');
  assert.deepEqual(Object.keys(g.shows), ['263102']);
  assert.equal(g.conflicts.length, 1);
  assert.equal(verifiedCanonicalForImdb(g, 'tt44094505'), null);
  assert.deepEqual(identityEquivalenceProposal(g).identities, []);
  assert.equal(
    observeVerifiedShowIdentity(db, 'profile-1',
      { trakt: 42, imdb: 'tt99999999' }, 'tt44094505').changed,
    false
  );
});

test('Trakt returned changed canonical IMDb and conflicting TMDB fail closed', () => {
  const db = fakeDb();
  observeVerifiedShowIdentity(db, 'p', fixed, 'tt44094505');
  const changed = observeVerifiedShowIdentity(db, 'p',
    { trakt: 263102, imdb: 'tt99999999', tmdb: 276470 },
    'tt44094506');
  assert.equal(changed.status, 'QUARANTINED');
  assert.equal(changed.reason, 'canonical_imdb_changed');
  const badProvider = observeVerifiedShowIdentity(db, 'p',
    { trakt: 263102, imdb: 'tt44051354', tmdb: 276471 },
    'tt44094507');
  assert.equal(badProvider.status, 'QUARANTINED');
  assert.equal(badProvider.reason, 'tmdb_disagreement');
  const g = loadVerifiedIdentityGraph(db, 'p');
  assert.equal(g.shows['263102'].canonicalImdb, 'tt44051354');
  assert.deepEqual(g.shows['263102'].aliases, ['tt44094505']);
});

test('profile scope isolation, invalid evidence does not write', () => {
  const db = fakeDb();
  assert.equal(observeVerifiedShowIdentity(db, 'p', { trakt: 1 }, 'tt44094505').changed, false);
  assert.equal(db.entries(), 0);
  observeVerifiedShowIdentity(db, 'p', fixed, 'tt44094505');
  assert.deepEqual(loadVerifiedIdentityGraph(db, 'q').shows, {});
  assert.equal(verifiedCanonicalForImdb(loadVerifiedIdentityGraph(db, 'q'), 'tt44094505'), null);
});

test('canonical-only Trakt stop is not an invented raw alias', () => {
  const db = fakeDb();
  observeVerifiedShowIdentity(db, 'p', fixed, 'tt44051354');
  assert.deepEqual(loadVerifiedIdentityGraph(db, 'p').shows['263102'].aliases, []);
  assert.deepEqual(identityEquivalenceProposal(loadVerifiedIdentityGraph(db, 'p')).identities, []);
});

test('never learn identity from unrelated pause, failed scrobble or a speculative Trakt GET', async () => {
  const db = fakeDb();
  const client = new TraktClient({
    traktClientId: 'example', userAgent: 'test', historyDedupeSeconds: 60,
  }, db);
  client.requestDetailed = async (_p, _path, _) => ({
    status: 201,
    data: {
      action: 'pause',
      show: { ids: fixed },
      episode: { ids: { trakt: 301 } },
    },
  });
  const event = {
    scope: 'episode', event: 'pause',
    metaId: 'tt44094505', videoId: 'tt44094505:1:6',
    season: 1, episode: 6,
  };
  const plan = { kind: 'scrobble', action: 'pause', progress: 20 };
  await client.applyEvent('p', event, plan);
  assert.equal(db.entries() > 0, true); // existing watermark writes are legitimate
  assert.deepEqual(loadVerifiedIdentityGraph(db, 'p').shows, {});
  const real = await client.applyEvent('p', { ...event, event: 'stop' }, plan);
  assert.equal(real.identityContinuity.status, 'VERIFIED_STORED');
  assert.equal(loadVerifiedIdentityGraph(db, 'p').shows['263102'].aliases[0], 'tt44094505');
});

test('no episode timeline or watched timestamp is part of the proposal', () => {
  const db = fakeDb();
  observeVerifiedShowIdentity(db, 'p', fixed, 'tt44094505');
  const json = JSON.stringify(identityEquivalenceProposal(loadVerifiedIdentityGraph(db, 'p')));
  assert.equal(json.includes('watched_at'), false);
  assert.equal(json.includes('positionMs'), false);
  assert.equal(json.includes('persona'), false);
});
