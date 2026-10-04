import test from 'node:test';
import assert from 'node:assert/strict';
import { APP_VERSION } from '../src/config.mjs';
import { buildManifest, planEvent, progressPercent, validatePushEvent } from '../src/watch-state.mjs';

test('manifest advertises AIOStreams watch_state v2 push and pull', () => {
  const m = buildManifest('abc');
  assert.equal(m.version, APP_VERSION);
  assert.equal(m.watchState.version, 2);
  assert.deepEqual(m.watchState.push.events, [
    'start', 'pause', 'stop', 'played', 'unplayed', 'watchlisted', 'unwatchlisted',
  ]);
  assert.equal(m.watchState.push.bulk, true);
  assert.deepEqual(m.watchState.pull, { items: true, watched: true, watchlist: true, ttlSeconds: 300 });
});

test('progress is computed and clamped', () => {
  assert.equal(progressPercent({ positionMs: 50, durationMs: 100 }), 50);
  assert.equal(progressPercent({ positionMs: 200, durationMs: 100 }), 100);
  assert.equal(progressPercent({ positionMs: 0 }), null);
});

test('AIOStreams unfinished stop preserves stop semantics and lets Trakt classify resume', () => {
  const plan = planEvent({ event: 'stop', positionMs: 850, durationMs: 1000, played: false });
  assert.equal(plan.kind, 'scrobble');
  assert.equal(plan.action, 'stop');
  assert.equal(plan.progress, 85);
});

test('played stop maps to Trakt stop', () => {
  const plan = planEvent({ event: 'stop', positionMs: 950, durationMs: 1000, played: true });
  assert.equal(plan.action, 'stop');
});

test('sub-1% start, pause and unfinished stop are ignored before Trakt', () => {
  for (const event of [
    { event: 'start', positionMs: 0, durationMs: 100000 },
    { event: 'pause', positionMs: 500, durationMs: 100000 },
    { event: 'stop', positionMs: 500, durationMs: 100000, played: false },
  ]) {
    assert.deepEqual(planEvent(event), { kind: 'ignore', reason: 'progress_below_trakt_minimum' });
  }
});

test('explicit played stop below 1% falls back to history add', () => {
  assert.deepEqual(
    planEvent({ event: 'stop', positionMs: 500, durationMs: 100000, played: true }),
    { kind: 'history-add' },
  );
});

test('exactly 1% remains a valid Trakt scrobble boundary', () => {
  assert.deepEqual(
    planEvent({ event: 'pause', positionMs: 1000, durationMs: 100000 }),
    { kind: 'scrobble', action: 'pause', progress: 1 },
  );
  assert.deepEqual(
    planEvent({ event: 'stop', positionMs: 1000, durationMs: 100000, played: false }),
    { kind: 'scrobble', action: 'pause', progress: 1 },
  );
});

test('unknown duration is never treated as zero', () => {
  assert.deepEqual(planEvent({ event: 'pause', positionMs: 500 }), { kind: 'ignore', reason: 'duration_unknown' });
  assert.deepEqual(planEvent({ event: 'stop', positionMs: 500, played: true }), { kind: 'history-add' });
});

test('season and series played marks use bulk plans', () => {
  const season = validatePushEvent({
    id: 'b01', event: 'played', scope: 'season', metaId: 'tt1234567', season: 2,
    videos: [{ videoId: 'tt1234567:2:1', season: 2, episode: 1 }], part: 1, parts: 1,
  });
  const series = validatePushEvent({
    id: 'b02', event: 'unplayed', scope: 'series', metaId: 'tt1234567', season: null,
    videos: [{ videoId: 'tt1234567:1:1', season: 1, episode: 1 }], part: 1, parts: 1,
  });
  assert.deepEqual(planEvent(season), { kind: 'bulk-history-add' });
  assert.deepEqual(planEvent(series), { kind: 'bulk-history-remove' });
});

test('bulk mark rejects malformed parts and cross-season videos', () => {
  assert.throws(
    () => validatePushEvent({
      id: 'b03', event: 'played', scope: 'season', metaId: 'tt1234567', season: 2,
      videos: [{ videoId: 'tt1234567:3:1', season: 3, episode: 1 }], part: 1, parts: 1,
    }),
    /another season/,
  );
  assert.throws(
    () => validatePushEvent({
      id: 'b04', event: 'played', scope: 'series', metaId: 'tt1234567',
      videos: [{ videoId: 'tt1234567:1:1', season: 1, episode: 1 }], part: 2, parts: 1,
    }),
    /part metadata/,
  );
});

test('watchlist movie and series events are accepted and planned', () => {
  const movie = validatePushEvent({ id: 'w01', event: 'watchlisted', scope: 'movie' });
  const show = validatePushEvent({ id: 'w02', event: 'unwatchlisted', scope: 'series' });
  assert.equal(movie.scope, 'movie');
  assert.equal(show.scope, 'series');
  assert.deepEqual(planEvent(movie), { kind: 'watchlist-add' });
  assert.deepEqual(planEvent(show), { kind: 'watchlist-remove' });
});

test('watchlist events reject episode scope', () => {
  assert.throws(
    () => validatePushEvent({ id: 'w03', event: 'watchlisted', scope: 'episode' }),
    /movie or series scope/,
  );
});

test('missing played flag is fail-safe and cannot trigger Trakt 80% watched threshold', () => {
  const plan = planEvent({ event: 'stop', positionMs: 850, durationMs: 1000 });
  assert.equal(plan.action, 'pause');
});
