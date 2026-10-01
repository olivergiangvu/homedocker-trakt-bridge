import test from 'node:test';
import assert from 'node:assert/strict';
import { invalidatesPullCache } from '../src/server.mjs';

test('identity alias learning invalidates pull cache even when Trakt action is pause', () => {
  assert.equal(invalidatesPullCache({
    action: 'scrobble:pause',
    identityAlias: {
      traktShowId: 123,
      preferredMetaId: 'tt44094505',
      traktImdb: 'tt44051354',
      revision: 1,
    },
  }), true);
});

test('successful playback pause invalidates pull cache for immediate convergence', () => {
  assert.equal(invalidatesPullCache({ action: 'scrobble:pause' }), true);
});

test('start scrobble alone does not invalidate pull cache', () => {
  assert.equal(invalidatesPullCache({ action: 'scrobble:start' }), false);
});
