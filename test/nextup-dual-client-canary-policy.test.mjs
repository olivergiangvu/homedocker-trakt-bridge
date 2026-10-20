import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CANARY_CLIENT,
  classifyCanaryRead,
  previewAppendOpportunity,
} from '../src/nextup-dual-client-canary-policy.mjs';

const mapped = 'mapped-user';
const desktop = {
  method: 'GET',
  path: '/Shows/NextUp',
  query: 'userId=mapped-user&Limit=24&EnableResumable=false',
  client: CANARY_CLIENT.AIO_DESKTOP,
  authenticatedUserId: mapped,
  expectedMappedUserId: mapped,
};

test('Desktop native client actually uses Jellyfin NextUp with explicit user/24/no-resume', () => {
  assert.equal(classifyCanaryRead(desktop), 'AIO_DESKTOP_NEXTUP_READ');
  assert.equal(classifyCanaryRead({
    ...desktop,
    query: 'EnableResumable=false&Limit=24&userId=mapped-user',
  }), 'AIO_DESKTOP_NEXTUP_READ');
  for (const query of [
    'userId=other&Limit=24&EnableResumable=false',
    'userId=mapped-user&Limit=24&EnableResumable=false&StartIndex=24',
    'userId=mapped-user&Limit=50&EnableResumable=false',
    'userId=mapped-user&Limit=24&EnableResumable=true',
    'userId=mapped-user&Limit=24&EnableResumable=false&userId=mapped-user',
  ]) assert.equal(classifyCanaryRead({ ...desktop, query }), 'REQUEST_NOT_QUALIFIED');
});

test('auth, QuickConnect, Resume, HTTP mutations are not NextUp canaries', () => {
  for (const path of [
    '/Users/AuthenticateByName',
    '/Users/AuthenticateWithQuickConnect',
    '/QuickConnect/Initiate',
    '/QuickConnect/Connect',
    '/UserItems/Resume',
    '/Items/abcdef',
    '/Shows/NextUp/extra',
  ]) assert.equal(classifyCanaryRead({ ...desktop, path }),
    'UPSTREAM_PASSTHROUGH');
  assert.equal(classifyCanaryRead({ ...desktop, method: 'POST' }),
    'UPSTREAM_PASSTHROUGH');
});

test('never trust UUID, userId URL param or client label as authenticated identity', () => {
  assert.equal(classifyCanaryRead({
    ...desktop, authenticatedUserId: 'different-user',
  }), 'USER_NOT_QUALIFIED');
  assert.equal(classifyCanaryRead({
    ...desktop, expectedMappedUserId: undefined,
  }), 'USER_NOT_QUALIFIED');
  assert.equal(classifyCanaryRead({
    ...desktop, authenticatedUserId: null,
  }), 'USER_NOT_QUALIFIED');
});

test('Odin canary remains unqualified until actual request contract is observed', () => {
  assert.equal(classifyCanaryRead({
    ...desktop, client: CANARY_CLIENT.ODIN,
  }), 'ODIN_CONTRACT_PENDING');
  assert.equal(classifyCanaryRead({
    ...desktop, client: CANARY_CLIENT.ODIN, path: '/UserItems/Resume',
  }), 'UPSTREAM_PASSTHROUGH');
});

const episode = {
  Type: 'Episode',
  Id: 'a1' + '0'.repeat(30),
  SeriesId: 'a1' + '1'.repeat(30),
  UserData: { Played: false },
};
const proof = {
  mappedUser: true,
  offWindow: true,
  seriesValidatedByBackend: true,
  episodeInRealCatalog: true,
  episodeDetailValidatedByBackend: true,
};
const short = {
  Items: Array.from({ length: 12 }, (_, idx) => ({
    Type: 'Episode',
    Id: 'a1' + String(idx).padStart(30, '0'),
    SeriesId: 'a2' + String(idx).padStart(30, '0'),
  })),
  StartIndex: 0,
  TotalRecordCount: 12,
  Count: 12,
};
// Prevent accidental matching the synthetic Episode's own ID.
const validShort = {
  ...short,
  Items: short.Items.map((x, idx) => ({
    ...x,
    Id: 'b1' + String(idx).padStart(30, '0'),
  })),
};

test('canary research can identify one safe short-page placement without modifying response', () => {
  const before = JSON.stringify(validShort);
  assert.equal(previewAppendOpportunity({
    upstream: validShort, episode, proof,
  }), 'SHADOW_CANDIDATE_INSERTABLE_ON_SHORT_PAGE_ONLY');
  assert.equal(JSON.stringify(validShort), before);
});

test('full Desktop NextUp page cannot silently evict an item or exceed 24', () => {
  const full = {
    Items: Array.from({ length: 24 }, (_, i) => ({ Id: String(i) })),
    StartIndex: 0,
    TotalRecordCount: 24,
  };
  assert.equal(previewAppendOpportunity({
    upstream: full, episode, proof,
  }), 'FULL_PAGE_NO_INSERTION');
});

test('unknown global total, paging and unverified source remain passthrough', () => {
  assert.equal(previewAppendOpportunity({
    upstream: { ...validShort, StartIndex: 24 }, episode, proof,
  }), 'UNSAFE_OR_UNKNOWN_PAGE');
  assert.equal(previewAppendOpportunity({
    upstream: { ...validShort, TotalRecordCount: 861 }, episode, proof,
  }), 'UNSAFE_OR_UNKNOWN_PAGE');
  assert.equal(previewAppendOpportunity({
    upstream: validShort, episode, proof: { ...proof, mappedUser: false },
  }), 'OFFWINDOW_EVIDENCE_INCOMPLETE');
  assert.equal(previewAppendOpportunity({
    upstream: validShort,
    episode: { ...episode, UserData: { Played: true } }, proof,
  }), 'EPISODE_SHAPE_UNQUALIFIED');
  assert.equal(previewAppendOpportunity({
    upstream: validShort,
    episode: { ...episode, SeriesId: validShort.Items[0].SeriesId }, proof,
  }), 'ALREADY_PRESENT');
});

test('pure module never imports live Gateway, sends HTTP or changes host state', async () => {
  const { readFile } = await import('node:fs/promises');
  const source = await readFile(
    new URL('../src/nextup-dual-client-canary-policy.mjs', import.meta.url),
    'utf8'
  );
  for (const forbidden of [
    'createServer(', 'listen(', 'fetch(', 'process.env',
    'gatewayFilteringEnabled', 'setGatewayFilteringEnabled',
    'docker compose', 'INSERT INTO', 'UPDATE watch_state',
    'DELETE FROM watch_state',
  ]) assert.equal(source.includes(forbidden), false, forbidden);
});
