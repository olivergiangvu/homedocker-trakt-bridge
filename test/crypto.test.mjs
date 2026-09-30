import test from 'node:test';
import assert from 'node:assert/strict';
import { decryptSecret, deriveProfileKey, encryptSecret, safeEqual } from '../src/crypto.mjs';

test('AES-GCM secrets round-trip', () => {
  const secret = 'a'.repeat(64);
  const encoded = encryptSecret('token-value', secret);
  assert.notEqual(encoded, 'token-value');
  assert.equal(decryptSecret(encoded, secret), 'token-value');
});

test('derived profile keys are deterministic and purpose-separated', () => {
  const secret = 'bridge-secret';
  const a = deriveProfileKey(secret, 'setup', 'abc');
  const b = deriveProfileKey(secret, 'setup', 'abc');
  const c = deriveProfileKey(secret, 'addon', 'abc');
  assert.equal(a, b);
  assert.notEqual(a, c);
  assert.ok(safeEqual(a, b));
  assert.equal(safeEqual(a, c), false);
});
