import test from 'node:test';
import assert from 'node:assert/strict';
import {
  attachIdentityEquivalenceToPull,
  identityEquivalencePullExtension,
  IDENTITY_EQUIVALENCE_REQUEST_HEADER,
} from '../src/identity-equivalence-pull.mjs';
import { buildManifest } from '../src/watch-state.mjs';
import { loadConfig } from '../src/config.mjs';
import { observeVerifiedShowIdentity } from '../src/identity-continuity.mjs';
import { ProposedAioIdentityLedger } from '../experiments/aio-verified-identity-ledger.mjs';

function db() {
  const items = new Map();
  return {
    cacheGet: (key) => structuredClone(items.get(key) || null),
    cacheSet: (key, value) => items.set(key, structuredClone(value)),
  };
}

const known = { trakt: 263102, imdb: 'tt44051354', tmdb: 276470 };
const scope = { uuid: 'synthetic-aio-user', persona: '' };

test('defaults preserve exact public Watch State response even if header is sent', () => {
  const store = db();
  observeVerifiedShowIdentity(store, 'p1', known, 'tt44094505');
  const raw = Object.freeze({
    version: 'trakt-state-v1',
    items: [{ metaId: 'tt44051354', videoId: 'tt44051354:1:10' }],
  });
  assert.equal(IDENTITY_EQUIVALENCE_REQUEST_HEADER, 'x-aiostreams-identity-equivalences');
  assert.strictEqual(attachIdentityEquivalenceToPull(raw, store, 'p1', 'v1', false), raw);
  assert.strictEqual(attachIdentityEquivalenceToPull(raw, store, 'p1', undefined, true), raw);
  assert.strictEqual(attachIdentityEquivalenceToPull(raw, store, 'p1', 'v2', true), raw);
  assert.equal(Object.hasOwn(buildManifest('p1').watchState.pull, 'identityEquivalences'), false);
  assert.equal(buildManifest('p1', 300, true).watchState.pull.identityEquivalences, 'v1');
});

test('negotiated response contains only scoped proven canonical aliases, not watch events', () => {
  const store = db();
  const first = observeVerifiedShowIdentity(store, 'profile-A', known, 'tt44094505');
  assert.equal(first.status, 'VERIFIED_STORED');
  observeVerifiedShowIdentity(store, 'profile-B', { trakt: 99, imdb: 'tt99999999' }, 'tt88888888');
  const base = Object.freeze({ version: 'trakt-unchanged', items: [] });
  const ext = attachIdentityEquivalenceToPull(base, store, 'profile-A', 'v1', true);
  assert.notStrictEqual(ext, base);
  assert.equal(ext.version, base.version);
  assert.strictEqual(base.identityEquivalences, undefined);
  assert.deepEqual(ext.identityEquivalences, {
    capability: 'identity-equivalences-v1',
    revision: 1,
    relations: [{
      kind: 'series',
      traktShowId: 263102,
      canonicalImdb: 'tt44051354',
      imdbAliases: ['tt44094505'],
      proof: 'successful_trakt_scrobble_response',
    }],
    quarantinedImdbs: [],
    quarantinedTraktShows: [],
    conflictCount: 0,
  });
  assert.equal(JSON.stringify(ext).includes('tt99999999'), false);
  assert.equal(JSON.stringify(ext).includes('watched_at'), false);
});

test('identity graph changes independently of unchanged Trakt last_activities', () => {
  const store = db();
  const raw = { version: 'same-trakt-version' };
  assert.equal(identityEquivalencePullExtension(store, 'profile-A', 'v1', true), null);
  observeVerifiedShowIdentity(store, 'profile-A', known, 'tt44094505');
  const before = attachIdentityEquivalenceToPull(raw, store, 'profile-A', 'v1', true);
  observeVerifiedShowIdentity(store, 'profile-A', known, 'tt44094506');
  const after = attachIdentityEquivalenceToPull(raw, store, 'profile-A', 'v1', true);
  assert.equal(before.version, after.version);
  assert.equal(before.identityEquivalences.revision, 1);
  assert.equal(after.identityEquivalences.revision, 2);
  assert.deepEqual(after.identityEquivalences.relations[0].imdbAliases,
    ['tt44094505', 'tt44094506']);
});

test('new cross-show conflict quarantines AIO previously accepted alias evidence', () => {
  const store = db();
  observeVerifiedShowIdentity(store, 'p', known, 'tt44094505');
  const ledger = new ProposedAioIdentityLedger({
    scope, allowedSinkId: 'authorized-trakt-sink',
  });
  const use = (extension) => ledger.applyVerifiedPull({
    scope, sinkId: 'authorized-trakt-sink',
    negotiatedCapability: extension.capability,
    revision: extension.revision,
    relations: extension.relations,
    quarantinedImdbs: extension.quarantinedImdbs,
    quarantinedTraktShows: extension.quarantinedTraktShows,
  });
  const prior = identityEquivalencePullExtension(store, 'p', 'v1', true);
  assert.equal(use(prior).accepted, true);
  assert.equal(ledger.canonicalSeriesFor(scope, 's|tt44094505').traktShowId, 263102);
  const clash = observeVerifiedShowIdentity(
    store, 'p', { trakt: 42, imdb: 'tt99999999' }, 'tt44094505',
  );
  assert.equal(clash.status, 'QUARANTINED');
  const next = identityEquivalencePullExtension(store, 'p', 'v1', true);
  assert.deepEqual(next.relations, []);
  assert.deepEqual(next.quarantinedImdbs, ['tt44094505']);
  assert.deepEqual(next.quarantinedTraktShows, [42]);
  assert.equal(next.revision, 2);
  assert.equal(use(next).accepted, true);
  assert.equal(ledger.canonicalSeriesFor(scope, 's|tt44094505'), null);
  assert.equal(ledger.snapshot().quarantined.includes(263102), true);
});

test('experimental config flag is opt-in, not changed by published defaults', () => {
  const backup = new Map([
    ['PUBLIC_BASE_URL', process.env.PUBLIC_BASE_URL],
    ['TRAKT_CLIENT_ID', process.env.TRAKT_CLIENT_ID],
    ['TRAKT_CLIENT_SECRET', process.env.TRAKT_CLIENT_SECRET],
    ['BRIDGE_SECRET_KEY', process.env.BRIDGE_SECRET_KEY],
    ['ADMIN_KEY', process.env.ADMIN_KEY],
    ['IDENTITY_EQUIVALENCES_ENABLED', process.env.IDENTITY_EQUIVALENCES_ENABLED],
  ]);
  try {
    Object.assign(process.env, {
      PUBLIC_BASE_URL: 'http://127.0.0.1:7000',
      TRAKT_CLIENT_ID: 'test',
      TRAKT_CLIENT_SECRET: 'test',
      BRIDGE_SECRET_KEY: '0123456789abcdef0123456789abcdef',
      ADMIN_KEY: '0123456789abcdef0123456789abcdef',
    });
    delete process.env.IDENTITY_EQUIVALENCES_ENABLED;
    assert.equal(loadConfig().identityEquivalencesEnabled, false);
    process.env.IDENTITY_EQUIVALENCES_ENABLED = 'true';
    assert.equal(loadConfig().identityEquivalencesEnabled, true);
  } finally {
    for (const [k, v] of backup) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
});
