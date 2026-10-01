import test from 'node:test';
import assert from 'node:assert/strict';

import { summarizeEventDetail } from '../src/operational-status.mjs';

test('operator summary keeps Trakt 429 endpoint and Retry-After visible', () => {
  const summary = summarizeEventDetail({
    event: 'start',
    detail: 'trakt_429:Trakt API 429 endpoint=/scrobble/start retry_after=2',
  });
  assert.equal(summary, 'Trakt 429 · /scrobble/start · retry 2s');
});

test('stale pull summary keeps upstream endpoint and retry delay visible', () => {
  const summary = summarizeEventDetail({
    event: 'pull',
    detail: JSON.stringify({
      source: 'stale-cache',
      items: 97,
      ageSeconds: 18,
      upstreamError: 'trakt_429',
      upstreamPath: '/sync/last_activities',
      retryAfter: '174',
    }),
  });
  assert.equal(
    summary,
    'Stale cache · 97 items · age 18s · trakt_429 · /sync/last_activities · retry 174s',
  );
});
