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

test('ordinary playback pause without alias change keeps pull cache', () => {
  assert.equal(invalidatesPullCache({ action: 'scrobble:pause' }), false);
});
