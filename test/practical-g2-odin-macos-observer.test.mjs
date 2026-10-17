import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

test('G2-1A Odin Mac ephemeral nginx observer has anonymous counters & default no-run', () => {
  const p = spawnSync('python3', ['-B', '-m', 'unittest', 'discover',
    '-s', 'test', '-p', 'test_v140_g2_odin_macos_observer.py', '-v'],
    { encoding: 'utf8', timeout: 15000 });
  assert.equal(p.error, undefined, p.error?.message);
  assert.equal(p.status, 0, p.stderr);
  assert.match(p.stderr, /Ran 7 tests/);
  assert.match(p.stderr, /\bOK\b/);
});
