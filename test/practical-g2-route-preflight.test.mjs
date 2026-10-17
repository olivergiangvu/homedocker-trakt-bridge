import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

test('G2-0 preview route preflight is pure read-only and cannot claim client acceptance', () => {
  const p = spawnSync('python3', [
    '-B', '-m', 'unittest', 'discover', '-s', 'test',
    '-p', 'test_v140_g2_route_preflight.py', '-v',
  ], { encoding: 'utf8', timeout: 15000 });
  assert.equal(p.error, undefined, p.error?.message);
  assert.equal(p.status, 0, p.stderr);
  assert.match(p.stderr, /Ran 6 tests/);
  assert.match(p.stderr, /\bOK\b/);
});
