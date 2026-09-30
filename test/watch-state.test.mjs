import test from 'node:test';
import assert from 'node:assert/strict';
import { APP_VERSION } from '../src/config.mjs';
import { buildManifest, planEvent, progressPercent, validatePushEvent } from '../src/watch-state.mjs';

test('manifest advertises AIOStreams watch_state v2 push and pull', () => {
  const m = buildManifest('abc');
  assert.equal(m.version, APP_VERSION);
  assert.equal(m.watchState.version, 2);
  assert.deepEqual(m.watchState.push.events, ['start', 'pause', 'stop', 'played', 'unplayed']);
  assert.equal(m.watchState.push.bulk, false);
  assert.deepEqual(m.watchState.pull, { items: true, watched: true, ttlSeconds: 300 });
});

test('progress is computed and clamped', () => {
  assert.equal(progressPercent({ positionMs: 50, durationMs: 100 }), 50);
  assert.equal(progressPercent({ positionMs: 200, durationMs: 100 }), 100);
  assert.equal(progressPercent({ positionMs: 0 }), null);
});

test('AIOStreams stop below its 90% watched threshold maps to Trakt pause', () => {
  const plan = planEvent({ event: 'stop', positionMs: 850, durationMs: 1000, played: false });
  assert.equal(plan.kind, 'scrobble');
  assert.equal(plan.action, 'pause');
  assert.equal(plan.progress, 85);
});

test('played stop maps to Trakt stop', () => {
  const plan = planEvent({ event: 'stop', positionMs: 950, durationMs: 1000, played: true });
  assert.equal(plan.action, 'stop');
});

test('unknown duration is never treated as zero', () => {
  assert.deepEqual(planEvent({ event: 'pause', positionMs: 500 }), { kind: 'ignore', reason: 'duration_unknown' });
  assert.deepEqual(planEvent({ event: 'stop', positionMs: 500, played: true }), { kind: 'history-add' });
});

test('bulk marks are rejected in v0.2', () => {
  assert.throws(() => validatePushEvent({ id: 'abc', event: 'played', scope: 'series' }), /Bulk marks/);
});

test('missing played flag is fail-safe and cannot trigger Trakt 80% watched threshold', () => {
  const plan = planEvent({ event: 'stop', positionMs: 850, durationMs: 1000 });
  assert.equal(plan.action, 'pause');
});
