import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

test('G1A read-only Python audit synthetic validation (no Docker/network)', () => {
  const result = spawnSync('python3', [
    '-B', '-m', 'unittest', 'discover', '-s', 'test',
    '-p', 'test_v140_g1_live_inputs.py', '-v',
  ], { encoding: 'utf8', timeout: 15000 });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /Ran 10 tests/);
  assert.match(result.stderr, /\bOK\b/);
});
