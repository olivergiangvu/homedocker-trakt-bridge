import test from 'node:test';
import assert from 'node:assert/strict';
import { ProposedAioIdentityLedger } from '../experiments/aio-verified-identity-ledger.mjs';
import { projectIdentityNextUpRows } from '../experiments/aio-nextup-identity-projection.mjs';

const scope = { uuid: 'synthetic-uuid', persona: 'family-member-1' };
const authorizedSinkId = 'bridge-sink-unique-1';
const relation = {
  kind: 'series',
  traktShowId: 263102,
  canonicalImdb: 'tt44051354',
  imdbAliases: ['tt44094505'],
  proof: 'successful_trakt_scrobble_response',
};

const create = (state) => new ProposedAioIdentityLedger({
  scope, allowedSinkId: authorizedSinkId, restoredState: state ?? null,
});
const pull = (ledger, rels = [relation], rev = 1, overrides = {}) =>
  ledger.applyVerifiedPull({
    scope, sinkId: authorizedSinkId, revision: rev, relations: rels,
    negotiatedCapability: 'identity-equivalences-v1',
    ...overrides,
  });

test('refuses fake sink, another user, missing capability and malformed evidence', () => {
  const ledger = create();
  assert.equal(pull(ledger, [relation], 1, { sinkId: 'other-tracker' }).accepted, false);
  assert.equal(pull(ledger, [relation], 1,
    { scope: { uuid: 'another', persona: scope.persona } }).accepted, false);
  assert.equal(pull(ledger, [relation], 1,
    { scope: { uuid: scope.uuid, persona: 'other' } }).accepted, false);
  assert.equal(pull(ledger, [relation], 1,
    { negotiatedCapability: undefined }).accepted, false);
  assert.equal(pull(ledger, [{ ...relation, proof: 'title_year_guess' }]).accepted, false);
  assert.equal(pull(ledger, [{ ...relation, imdbAliases: ['ttnotimdb'] }]).accepted, false);
  assert.equal(ledger.snapshot().revision, 0);
  assert.equal(ledger.snapshot().relations.length, 0);
});

test('accepts confirmed relationship and creates exact AIO e| series match keys', () => {
  const ledger = create();
  assert.equal(pull(ledger).accepted, true);
  const alias = ledger.canonicalSeriesFor(scope, 's|tt44094505');
  const canonical = ledger.canonicalSeriesFor(scope, 's|tt44051354');
  assert.deepEqual(alias, canonical);
  assert.equal(alias.traktShowId, 263102);
  assert.equal(ledger.virtualEpisodeMatchKey(scope, {
    kind: 'episode', seriesKey: 's|tt44094505',
    season: 1, episode: 9, videoId: 'tt44094505:1:9',
  }), 'e|tt44051354:1:9');
  assert.equal(ledger.virtualEpisodeMatchKey(scope, {
    kind: 'episode', seriesKey: 's|tt44051354',
    season: 1, episode: 10, videoId: 'tt44051354:1:10',
  }), 'e|tt44051354:1:10');
  assert.equal(ledger.virtualEpisodeMatchKey({ uuid: scope.uuid, persona: 'other' }, {
    kind: 'episode', seriesKey: 's|tt44094505',
    season: 1, episode: 9, videoId: 'tt44094505:1:9',
  }), null);
});

test('virtual match does not touch anime absolute numbering or unknown IDs', () => {
  const ledger = create();
  pull(ledger);
  assert.equal(ledger.virtualEpisodeMatchKey(scope, {
    kind: 'episode', seriesKey: 's|tt44094505',
    season: 1, episode: 9, videoId: 'kitsu:31:9',
  }), null);
  assert.equal(ledger.virtualEpisodeMatchKey(scope, {
    kind: 'episode', seriesKey: 's|tt99999999',
    season: 1, episode: 9, videoId: 'tt99999999:1:9',
  }), null);
  assert.equal(ledger.virtualEpisodeMatchKey(scope, {
    kind: 'episode', seriesKey: 's|tt44094505',
    season: 1, episode: 9, videoId: 'tt44094505:9',
  }), null);
});

test('snapshot restores exact scope and revision across restart, not another persona', () => {
  const old = create();
  pull(old);
  const snapshot = JSON.parse(JSON.stringify(old.snapshot()));
  const restored = create(snapshot);
  assert.deepEqual(restored.snapshot(), snapshot);
  assert.equal(restored.canonicalSeriesFor(scope, 's|tt44094505').traktShowId, 263102);
  assert.equal(pull(restored, [relation], 1).reason, 'STALE_OR_REPLAYED_REVISION');
  assert.throws(() => new ProposedAioIdentityLedger({
    scope: { uuid: 'other-uuid', persona: scope.persona },
    allowedSinkId: authorizedSinkId, restoredState: snapshot,
  }), /invalid or wrong-owner/);
});

test('future snapshot must not forget verified old aliases for new playback', () => {
  const ledger = create();
  pull(ledger);
  const next = {
    ...relation,
    imdbAliases: ['tt44094506'],
  };
  const result = pull(ledger, [next], 2);
  assert.equal(result.accepted, true);
  const restored = create(ledger.snapshot());
  assert.equal(restored.canonicalSeriesFor(scope, 's|tt44094505').traktShowId, 263102);
  assert.equal(restored.canonicalSeriesFor(scope, 's|tt44094506').traktShowId, 263102);
  assert.equal(restored.snapshot().relations[0].imdbAliases.length, 2);
});

test('foreign conflicting claim quarantines both relevant logical identity paths', () => {
  const ledger = create();
  pull(ledger);
  const another = {
    ...relation,
    traktShowId: 42, canonicalImdb: 'tt99999999',
  };
  const result = pull(ledger, [another], 2);
  assert.equal(result.accepted, true);
  assert.equal(result.conflicts, 1);
  assert.equal(ledger.canonicalSeriesFor(scope, 's|tt44094505'), null);
  const restored = create(ledger.snapshot());
  assert.equal(restored.canonicalSeriesFor(scope, 's|tt44094505'), null);
});

test('incoming single revision containing duplicate alias is rejected atomically', () => {
  const ledger = create();
  const another = { ...relation, traktShowId: 42, canonicalImdb: 'tt99999999' };
  const attempt = pull(ledger, [relation, another]);
  assert.equal(attempt.accepted, false);
  assert.equal(attempt.reason, 'CROSS_SHOW_ALIAS_CONFLICT');
  assert.equal(ledger.snapshot().revision, 0);
});

test('canonical ID drift is quarantined; prior canonical not silently overwritten', () => {
  const ledger = create();
  pull(ledger);
  const modified = { ...relation, canonicalImdb: 'tt11111111' };
  const result = pull(ledger, [modified], 2);
  assert.equal(result.accepted, true);
  assert.equal(result.conflicts, 1);
  assert.equal(ledger.canonicalSeriesFor(scope, 's|tt44094505'), null);
  assert.equal(ledger.snapshot().relations[0].canonicalImdb, 'tt44051354');
});

test('projection remains one logical group after alias playback across ledger restart', () => {
  const ledger = create();
  pull(ledger);
  const restored = create(ledger.snapshot());
  const allowed = restored.snapshot().relations.map((e) => ({
    ...e, type: 'series',
  }));
  const rows = [
    ...Array.from({ length: 8 }, (_, idx) => ({
      ...scope, kind: 'episode', seriesKey: 's|tt44094505',
      season: 1, episode: idx + 1, played: true,
      positionMs: 0, sortAt: idx + 1000,
    })),
    ...Array.from({ length: 10 }, (_, idx) => ({
      ...scope, kind: 'episode', seriesKey: 's|tt44051354',
      season: 1, episode: idx + 1, played: idx < 9,
      positionMs: idx === 9 ? 10000 : 0,
      sortAt: idx + 2000,
    })),
    {
      ...scope, kind: 'episode', seriesKey: 's|tt44094505',
      season: 1, episode: 11, played: false,
      positionMs: 20000, sortAt: 4000,
    },
  ];
  const copy = structuredClone(rows);
  const view = projectIdentityNextUpRows({
    scope, rows, equivalences: allowed,
  });
  assert.equal(view.shelves.length, 1);
  assert.equal(view.shelves[0].originalEpisodeRows, 19);
  assert.equal(view.shelves[0].anchor.episode, 11);
  assert.deepEqual(rows, copy);
});
