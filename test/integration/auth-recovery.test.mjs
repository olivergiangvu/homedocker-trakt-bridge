import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { BridgeDb } from '../../src/db.mjs';
import { ManagedTraktClient } from '../../src/managed-trakt.mjs';
import { buildProfileOperationalStatus, buildReadiness } from '../../src/operational-status.mjs';
import { startJsonServer, redirectTraktFetch } from '../helpers/http-fixture.mjs';

function configFor(dataDir) {
  return {
    dataDir,
    dbPath: join(dataDir, 'bridge.db'),
    bridgeSecret: 'auth-recovery-secret-0123456789',
    traktClientId: 'client-id',
    traktClientSecret: 'client-secret',
    redirectUri: 'http://bridge.local/oauth/callback',
    userAgent: 'HomeDocker-Trakt-Bridge/auth-recovery-test',
    pullMaxPages: 10,
    pullIdentityMode: 'trakt',
  };
}

test('invalid_grant clears unusable credentials and exposes reconnect-required state', async (t) => {
  const mock = await startJsonServer(async (req) => {
    if (req.path === '/oauth/token' && req.method === 'POST') {
      assert.equal(req.json.grant_type, 'refresh_token');
      return {
        status: 400,
        body: {
          error: 'invalid_grant',
          error_description: 'The provided authorization grant is invalid',
        },
      };
    }
    return null;
  });
  const restoreFetch = redirectTraktFetch(mock.baseUrl);

  const dataDir = mkdtempSync(join(tmpdir(), 'trakt-bridge-auth-recovery-'));
  const config = configFor(dataDir);
  const profileId = 'cccccccccccccccccccccccc';
  const db = new BridgeDb(config);
  db.createProfile(profileId, 'Auth recovery');
  db.setTokens(profileId, {
    access_token: 'access-old',
    refresh_token: 'refresh-invalid',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000) - 7200,
  });

  t.after(async () => {
    restoreFetch();
    await mock.close();
    db.close();
    rmSync(dataDir, { recursive: true, force: true });
  });

  const trakt = new ManagedTraktClient(config, db);

  await assert.rejects(
    () => trakt.refresh(profileId, true),
    (err) => err?.code === 'reconnect_required' && err?.status === 401,
  );

  assert.equal(db.getTokens(profileId), null);
  assert.equal(db.getProfile(profileId).access_token_enc, null);
  assert.equal(db.countConnectedProfiles(), 0);

  const authRows = db.recentEvents(profileId, 10).filter((row) => row.event === 'auth');
  assert.equal(authRows[0].status, 'error');
  assert.deepEqual(JSON.parse(authRows[0].detail), { state: 'reconnect_required' });

  const readiness = buildReadiness({ db });
  const status = buildProfileOperationalStatus({ db, config, profileId });
  assert.equal(readiness.ready, false);
  assert.equal(readiness.status, 'setup_required');
  assert.equal(status.profile.connectionState, 'reconnect_required');
  assert.equal(status.profile.reconnectRequired, true);

  db.setTokens(profileId, {
    access_token: 'access-new',
    refresh_token: 'refresh-new',
    expires_in: 3600,
    created_at: Math.floor(Date.now() / 1000),
  });

  const recovered = buildProfileOperationalStatus({ db, config, profileId });
  assert.equal(recovered.profile.connected, true);
  assert.equal(recovered.profile.connectionState, 'connected');
  assert.equal(recovered.profile.reconnectRequired, false);
  assert.equal(db.countConnectedProfiles(), 1);

  const latestAuth = db.recentEvents(profileId, 10).find((row) => row.event === 'auth');
  assert.equal(latestAuth.status, 'ok');
  assert.deepEqual(JSON.parse(latestAuth.detail), { state: 'connected' });
});
