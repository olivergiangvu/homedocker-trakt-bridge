import test from 'node:test';
import assert from 'node:assert/strict';
import { projectIdentityNextUpRows } from '../experiments/aio-nextup-identity-projection.mjs';

const scope = { uuid: 'synthetic-aio-user', persona: 'viewer' };
const valid = [{
  type: 'series',
  canonicalImdb: 'tt44051354',
  imdbAliases: ['tt44094505'],
  traktShowId: 263102,
  proof: 'successful_trakt_scrobble_response',
}];

function episode(base, number, opts = {}) {
  return {
    ...scope,
    kind: 'episode',
    seriesKey: 's|' + base,
    season: 1,
    episode: number,
    played: true,
    positionMs: 0,
    sortAt: 1000 + number,
    ...opts,
  };
}
function fixture() {
  return [
    ...Array.from({ length: 8 }, (_, i) => episode('tt44094505', i + 1)),
    ...Array.from({ length: 9 }, (_, i) => episode('tt44051354', i + 1, { sortAt: 3000 + i })),
    episode('tt44051354', 10,
      { played: false, positionMs: 28 * 60_000, sortAt: 8000 }),
  ];
}

test('real 8+10 null-match AIO timeline becomes one display group without data loss', () => {
  const rows = fixture();
  const original = structuredClone(rows);
  const projection = projectIdentityNextUpRows({
    scope, rows, equivalences: valid,
  });
  assert.equal(projection.readOnly, true);
  assert.equal(projection.shelves.length, 1);
  const shelf = projection.shelves[0];
  assert.equal(shelf.logicalSeriesKey, 'trakt|263102');
  assert.equal(shelf.status, 'VERIFIED_EQUIVALENCE');
  assert.equal(shelf.canonicalImdb, 'tt44051354');
  assert.equal(shelf.originalEpisodeRows, 18);
  assert.equal(shelf.originalResumeRows, 1);
  assert.equal(shelf.originalWatchedRows, 17);
  assert.deepEqual(shelf.sourceSeriesKeys, ['s|tt44051354', 's|tt44094505']);
  assert.deepEqual(shelf.anchor, {
    sourceSeriesKey: 's|tt44051354', season: 1, episode: 10,
    played: false, positionMs: 28 * 60_000, sortAt: 8000,
  });
  assert.deepEqual(rows, original);
});

test('new playback on older alias stays one verified display group, not dropped', () => {
  const rows = [
    ...fixture(),
    episode('tt44094505', 11,
      { played: false, positionMs: 12 * 60_000, sortAt: 12000 }),
  ];
  const result = projectIdentityNextUpRows({
    scope, rows, equivalences: valid,
  });
  assert.equal(result.shelves.length, 1);
  const shelf = result.shelves[0];
  assert.equal(shelf.originalEpisodeRows, 19);
  assert.equal(shelf.anchor.sourceSeriesKey, 's|tt44094505');
  assert.equal(shelf.anchor.episode, 11);
  assert.equal(shelf.status, 'VERIFIED_EQUIVALENCE');
});

test('without verified relation, each raw IMDb is independent (fail closed)', () => {
  const result = projectIdentityNextUpRows({
    scope, rows: fixture(), equivalences: [],
  });
  assert.equal(result.shelves.length, 2);
  assert.ok(result.shelves.every(x => x.status === 'RAW_SEPARATE'));
});

test('never mix another persona or UUID, or group same-title unrelated series', () => {
  const rows = [
    ...fixture(),
    episode('tt44094505', 7, { persona: 'someone-else', sortAt: 99000 }),
    episode('tt44094505', 7, { uuid: 'someone-else', sortAt: 99000 }),
    episode('tt12345678', 4, { sortAt: 9000 }),
  ];
  const result = projectIdentityNextUpRows({
    scope, rows, equivalences: valid,
  });
  assert.equal(result.shelves.length, 2);
  assert.equal(result.shelves.find(x => x.logicalSeriesKey === 'trakt|263102')
    .originalEpisodeRows, 18);
  assert.equal(result.shelves.find(x => x.logicalSeriesKey === 'raw|tt12345678')
    .originalEpisodeRows, 1);
});

test('unverified cross-show competing alias is quarantined; do not merge', () => {
  const conflicting = [
    ...valid,
    { ...valid[0], traktShowId: 42,
      canonicalImdb: 'tt99999999', imdbAliases: ['tt44094505'] },
  ];
  const result = projectIdentityNextUpRows({
    scope, rows: fixture(), equivalences: conflicting,
  });
  assert.equal(result.shelves.length, 2);
  assert.ok(result.shelves.every(s => s.status === 'RAW_SEPARATE'));
});

test('simultaneously competing resumes expose a diagnostic, never erase a row', () => {
  const rows = [
    ...fixture(),
    episode('tt44094505', 9,
      { played: false, positionMs: 3 * 60_000, sortAt: 8500 }),
    episode('tt44051354', 10,
      { played: false, positionMs: 37 * 60_000, sortAt: 8501 }),
  ];
  const result = projectIdentityNextUpRows({
    scope, rows, equivalences: valid,
  });
  assert.equal(result.shelves.length, 1);
  assert.equal(result.shelves[0].status, 'AMBIGUOUS_RESUMES_REVIEW');
  assert.equal(result.shelves[0].originalEpisodeRows, 20);
});

test('invalid scope or oversized evidence input aborts', () => {
  assert.throws(() => projectIdentityNextUpRows({
    scope: { uuid: 'x' }, rows: [], equivalences: valid,
  }));
  assert.throws(() => projectIdentityNextUpRows({
    scope, rows: new Array(10001).fill(null), equivalences: valid,
  }));
});
