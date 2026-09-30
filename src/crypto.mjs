import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
  randomBytes,
  timingSafeEqual,
} from 'node:crypto';

const AAD = Buffer.from('homedocker-trakt-bridge:v1', 'utf8');

function keyFromSecret(secret) {
  return createHash('sha256').update(secret, 'utf8').digest();
}

export function encryptSecret(plaintext, secret) {
  if (plaintext == null) return null;
  const key = keyFromSecret(secret);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(AAD);
  const ciphertext = Buffer.concat([cipher.update(String(plaintext), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1.${iv.toString('base64url')}.${tag.toString('base64url')}.${ciphertext.toString('base64url')}`;
}

export function decryptSecret(encoded, secret) {
  if (!encoded) return null;
  const [version, ivB64, tagB64, dataB64] = String(encoded).split('.');
  if (version !== 'v1' || !ivB64 || !tagB64 || !dataB64) throw new Error('Unsupported encrypted secret format');
  const key = keyFromSecret(secret);
  const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(ivB64, 'base64url'));
  decipher.setAAD(AAD);
  decipher.setAuthTag(Buffer.from(tagB64, 'base64url'));
  return Buffer.concat([
    decipher.update(Buffer.from(dataB64, 'base64url')),
    decipher.final(),
  ]).toString('utf8');
}

export function deriveProfileKey(secret, purpose, profileId) {
  return createHmac('sha256', keyFromSecret(secret))
    .update(`${purpose}:${profileId}`, 'utf8')
    .digest('base64url');
}

export function randomToken(bytes = 32) {
  return randomBytes(bytes).toString('base64url');
}

export function sha256(value) {
  return createHash('sha256').update(String(value), 'utf8').digest('hex');
}

export function safeEqual(a, b) {
  const ah = createHash('sha256').update(String(a ?? ''), 'utf8').digest();
  const bh = createHash('sha256').update(String(b ?? ''), 'utf8').digest();
  return timingSafeEqual(ah, bh);
}
