import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { APP_VERSION } from '../src/config.mjs';

const root = new URL('../', import.meta.url);
const file = name => readFileSync(new URL(name, root), 'utf8');

test('v1.4.0 source version is consistent, tag publication remains explicitly guarded', () => {
  const version = JSON.parse(file('package.json')).version;
  assert.equal(version, '1.4.0');
  assert.equal(APP_VERSION, version);
  const release = file('.github/workflows/release.yml');
  assert.match(release, /Block unqualified v1\.4 Gateway publication/);
  assert.match(release, /V140_STABLE_RELEASE_HOLD/);
  assert.match(release, /startsWith\(github\.ref_name, 'v1\.4'\)/);
});

test('RC.1 source exposes one Bridge service with Gateway OFF and no import or Resume normalization', () => {
  const env = file('.env.example');
  const compose = file('compose.example.yml');
  const dockerfile = file('Dockerfile');
  const index = file('src/index.mjs');
  for (const directive of [
    'JELLYFIN_GATEWAY_ENABLED=false',
    'JELLYFIN_GATEWAY_LEGACY_IMPORT=off',
    'JELLYFIN_GATEWAY_RESUME_PASSTHROUGH=false',
  ]) {
    assert.equal(env.split('\n').filter(line => line === directive).length, 1);
  }
  assert.match(index, /config\.jellyfinGatewayEnabled\s*\?\s*createJellyfinGateway/);
  assert.doesNotMatch(compose, /^\s+-\s+"?0\.0\.0\.0:7101:/m);
  assert.doesNotMatch(compose, /^\s+-\s+"?127\.0\.0\.1:7101:/m);
  assert.match(compose, /127\.0\.0\.1:7000:7000/);
  assert.match(dockerfile, /COPY --chown=node:node src \.\/src/);
  assert.doesNotMatch(dockerfile, /gateway-standalone|gateway-canary|odin-g2-shadow/);
});
