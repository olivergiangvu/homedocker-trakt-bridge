import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCanaryIdentityProof, canaryGatewayConfig, launchCanaryGateway,
} from '../src/gateway-standalone.mjs';
import { writeFileSync, chmodSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const PROFILE = 'a'.repeat(24);
const valid = {
  contract: 'homedocker-canary-identity-proof-v1',
  profileId: PROFILE,
  graph: {
    revision: 1, conflicts: [],
    shows: { 263102: {
      canonicalImdb: 'tt44051354', aliases: ['tt44094505'],
      tmdb: null, tvdb: null,
    } },
  },
};

test('isolated sidecar accepts only exact sanitized known RC6 mapping', () => {
  const proof = parseCanaryIdentityProof(JSON.stringify(valid));
  assert.equal(proof.profileId, PROFILE);
  assert.equal(proof.graph.shows['263102'].aliases[0], 'tt44094505');
  for (const tampered of [
    { ...valid, profileId: '../bad-profile' },
    { ...valid, graph: { ...valid.graph, revision: 44 } },
    { ...valid, graph: { ...valid.graph, conflicts: [{ id: 'tt44094505' }] } },
    { ...valid, graph: { ...valid.graph, shows: { ...valid.graph.shows, 42: {} } } },
    { ...valid, graph: { ...valid.graph, shows: {
      263102: { ...valid.graph.shows[263102], aliases: ['tt99999999'] },
    } } },
  ]) {
    assert.throws(() => parseCanaryIdentityProof(JSON.stringify(tampered)));
  }
});

test('isolated process has no Bridge tokens, DB writer or Trakt client', () => {
  const env = {
    JELLYFIN_GATEWAY_PORT: '7101',
    JELLYFIN_GATEWAY_AIO_USER_ID: '12345678abcdef',
    JELLYFIN_GATEWAY_UPSTREAM_URL: 'http://aiostreams:3000',
  };
  const cfg = canaryGatewayConfig(parseCanaryIdentityProof(JSON.stringify(valid)), env);
  assert.equal(cfg.jellyfinGatewayProfileId, PROFILE);
  assert.equal(cfg.jellyfinGatewayUpstreamUrl, 'http://aiostreams:3000');
  assert.equal(Object.keys(cfg).some(k => /secret|trakt|oauth|database|sqlite/i.test(k)), false);
});

test('sidecar reads only private proof and creates a NextUp-only HTTP listener', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'proof-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, 'identity-proof.json');
  writeFileSync(file, JSON.stringify(valid), { mode: 0o600 });
  chmodSync(file, 0o600);
  const env = {
    GATEWAY_PROOF_FILE: file,
    JELLYFIN_GATEWAY_PORT: '17017',
    JELLYFIN_GATEWAY_UPSTREAM_URL: 'http://127.0.0.1:32000',
    JELLYFIN_GATEWAY_AIO_USER_ID: '12345678abcdef',
  };
  const service = launchCanaryGateway({ env });
  t.after(() => { if (service.server.listening) service.shutdown(); });
  await new Promise((resolve, reject) => service.server.listen(0, '127.0.0.1', resolve)
    .once('error', reject));
  const port = service.server.address().port;
  const health = await fetch('http://127.0.0.1:' + port + '/health');
  assert.equal(health.status, 200);
  assert.equal((await health.json()).adapter, 'nextup-only');
  const other = await fetch('http://127.0.0.1:' + port + '/Users/Me');
  assert.equal(other.status, 404);
});

test('rejects world-readable identity proof', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'proof-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, 'identity-proof.json');
  writeFileSync(file, JSON.stringify(valid), { mode: 0o644 });
  chmodSync(file, 0o644);
  assert.throws(() => launchCanaryGateway({ env: {
    GATEWAY_PROOF_FILE: file,
    JELLYFIN_GATEWAY_PORT: '17019',
    JELLYFIN_GATEWAY_UPSTREAM_URL: 'http://127.0.0.1:3240',
    JELLYFIN_GATEWAY_AIO_USER_ID: '12345678abcdef',
  } }));
});
