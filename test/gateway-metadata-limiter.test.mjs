import test from 'node:test';
import assert from 'node:assert/strict';
import { createMetadataLimiter } from '../src/jellyfin-gateway.mjs';

test('metadata limiter keeps concurrent upstream lookups globally bounded', async () => {
  const limiter = createMetadataLimiter(3, 24);
  let active = 0;
  let peak = 0;
  const work = Array.from({ length: 18 }, (_, i) => limiter.run(async () => {
    active++;
    peak = Math.max(peak, active);
    try {
      await new Promise(resolve => setTimeout(resolve, 7));
      return i;
    } finally {
      active--;
    }
  }));
  assert.deepEqual(await Promise.all(work), Array.from({ length: 18 }, (_, i) => i));
  assert.equal(peak, 3);
  assert.equal(limiter.snapshot().peakActive, 3);
  assert.equal(limiter.snapshot().active, 0);
  assert.equal(limiter.snapshot().queued, 0);
});

test('over-capacity requests fail immediately and do not poison later work', async () => {
  const limiter = createMetadataLimiter(1, 1);
  let releaseFirst;
  const first = limiter.run(() => new Promise(resolve => { releaseFirst = resolve; }));
  const second = limiter.run(async () => 'second');
  await assert.rejects(
    limiter.run(async () => 'must-fail'),
    error => error.code === 'METADATA_QUEUE_FULL'
  );
  assert.deepEqual(limiter.snapshot(), { active: 1, queued: 1, peakActive: 1 });
  releaseFirst('first');
  assert.equal(await first, 'first');
  assert.equal(await second, 'second');
  assert.equal(limiter.snapshot().active, 0);
  assert.equal(await limiter.run(async () => 'recovered'), 'recovered');
});

test('rejected/throwing upstream metadata work releases slot to waiter', async () => {
  const limiter = createMetadataLimiter(1, 2);
  const first = limiter.run(async () => { throw new Error('mock_upstream_error'); });
  const second = limiter.run(async () => 'healthy');
  await assert.rejects(first, /mock_upstream_error/);
  assert.equal(await second, 'healthy');
  assert.equal(limiter.snapshot().active, 0);
  assert.equal(limiter.snapshot().queued, 0);
});

test('invalid limits fail closed', () => {
  assert.throws(() => createMetadataLimiter(0), /invalid metadata/);
  assert.throws(() => createMetadataLimiter(4, -1), /invalid metadata/);
  assert.throws(() => createMetadataLimiter(Infinity, 3), /invalid metadata/);
});
