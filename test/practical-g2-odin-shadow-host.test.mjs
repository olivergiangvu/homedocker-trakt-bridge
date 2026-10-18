import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

test('G2-1B host launcher consent / privilege drop / anonymous report suite', () => {
  const p = spawnSync('python3', [
    '-B', 'test/test_v140_g2_odin_shadow_host.py', '-v',
  ], { encoding: 'utf8', timeout: 15000 });
  assert.equal(p.status, 0, 'Shadow host offline safety tests must pass');
  assert.match(p.stderr, /Ran 7 tests/);
  assert.match(p.stderr, /OK/);
  assert.doesNotMatch(p.stderr, /Traceback/);
});
