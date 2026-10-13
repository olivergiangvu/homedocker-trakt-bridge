import assert from 'node:assert/strict';
import test from 'node:test';
import {
  parseVerifiedAliasGroups,
  reconcileNextUpCandidates,
} from '../contrib/aiostreams/src/nextup-identity.ts';

const trakt = { Id: 'jf-trakt-e10', UserData: { Played: false, PlaybackPositionTicks: 3466260 } };
const aio = { Id: 'jf-aio-e09', UserData: { Played: false, PlaybackPositionTicks: 0 } };
const alias = 'tt44051354,tt44094505';
const spellings = new Map([
  [trakt.Id, 'tt44051354'],
  [aio.Id, 'tt44094505'],
]);

test('RC7 regression: S01E10 Trakt resume wins stale AIO S01E09, without changing rows', () => {
  const original = [trakt, aio];
  assert.deepEqual(reconcileNextUpCandidates(original, spellings, alias), [trakt]);
  assert.deepEqual(original, [trakt, aio]);
  assert.deepEqual(reconcileNextUpCandidates([aio, trakt], spellings, alias), [trakt]);
});

test('default config is a no-op, as is malformed alias syntax', () => {
  for (const input of ['', 'bad', 'tt44051354,tt44051354',
    'tt44051354,tt44094505;tt44094505,tt0000001',
    'tt44051354,tt44094505;unknown,tt0000001']) {
    assert.deepEqual(reconcileNextUpCandidates([trakt, aio], spellings, input),
      [trakt, aio]);
  }
});

test('unrelated or title-only overlap is never collapsed', () => {
  const unrelated = { Id: 'jf-another-series', Name: "Ok! Let's Get Divorced",
    UserData: { Played: false, PlaybackPositionTicks: 0 } };
  const sources = new Map([...spellings, [unrelated.Id, 'tt12345678']]);
  assert.deepEqual(
    reconcileNextUpCandidates([trakt, aio, unrelated], sources, alias),
    [trakt, unrelated]
  );
});

test('two active resume candidates remain both (intentional rewatch ambiguous)', () => {
  const resumedAio = { ...aio, UserData: { Played: false, PlaybackPositionTicks: 9876543 } };
  assert.deepEqual(
    reconcileNextUpCandidates([trakt, resumedAio], spellings, alias),
    [trakt, resumedAio]
  );
});

test('no positive resume: do not hide legitimate NextUp episodes', () => {
  const unplayedTrakt = { ...trakt, UserData: { Played: false, PlaybackPositionTicks: 0 } };
  assert.deepEqual(
    reconcileNextUpCandidates([unplayedTrakt, aio], spellings, alias),
    [unplayedTrakt, aio]
  );
});

test('only a single alias in captured page never suppresses rows', () => {
  const onlyTrakt = new Map([
    [trakt.Id, 'tt44051354'], [aio.Id, 'tt44051354'],
  ]);
  assert.deepEqual(
    reconcileNextUpCandidates([trakt, aio], onlyTrakt, alias),
    [trakt, aio]
  );
});

test('safely ignores missing item-to-source mapping and played rows', () => {
  const played = { ...trakt, UserData: { Played: true, PlaybackPositionTicks: 9 } };
  assert.deepEqual(reconcileNextUpCandidates([played, aio], spellings, alias),
    [played, aio]);
  assert.deepEqual(
    reconcileNextUpCandidates([trakt, aio], new Map([[trakt.Id, 'tt44051354']]), alias),
    [trakt, aio]
  );
});

test('multiple independent verified alias groups stay isolated', () => {
  const otherResume = { Id: 'other-a', UserData: { Played: false, PlaybackPositionTicks: 90 } };
  const otherStale = { Id: 'other-b', UserData: { Played: false, PlaybackPositionTicks: 0 } };
  const sources = new Map([...spellings,
    [otherResume.Id, 'tt88888888'], [otherStale.Id, 'tt99999999']]);
  assert.deepEqual(
    reconcileNextUpCandidates([aio, otherStale, trakt, otherResume], sources,
      alias + ';tt88888888,tt99999999'),
    [trakt, otherResume]
  );
});

test('invalid aliases fail closed (no suppression), strict public IDs only', () => {
  assert.equal(parseVerifiedAliasGroups('abc,tt44094505'), null);
  assert.equal(parseVerifiedAliasGroups('tt44051354,tt44094505;tt44051354,tt99999999'), null);
  const group = parseVerifiedAliasGroups(alias);
  assert.equal(group?.get('tt44094505'), group?.get('tt44051354'));
});
