/**
 * G1C practical direct AIO↔Gateway acceptance.
 * Production AIO network namespace is shared temporarily to access its
 * localhost Jellyfin listener. No AIO data/volumes are mounted, no process
 * inside AIO is altered, no ports published, and the Gateway listener is
 * local to its own process and closed before the test exits.
 *
 * Unlike G1B, the live AIO token MUST enter this one ephemeral process via
 * a bounded anonymous stdin pipe. Never pass secrets via env, argv or logs.
 * Authenticated GET only; never write playback/history/Trakt/AIO SQLite.
 *
 * This test proves same-session Gateway→real-AIO GET behavior and an explicit
 * technical user/profile configuration. It CANNOT independently attest that
 * the real-world AIO account belongs to that Bridge profile or prove client
 * ingress. Keep G1 incomplete until ownership sign-off and client pilot.
 */
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';
import { loadVerifiedIdentityGraph } from '../src/identity-continuity.mjs';

const AIO_BASE = 'http://127.0.0.1:3000/jellyfin';
const QUERY = '/Shows/NextUp?Limit=20';
const IMDB = /^tt\d{7,12}$/;
const VALID_USER = /^[A-Za-z0-9_-]{8,128}$/;
const VALID_PROFILE = /^[a-f0-9]{24}$/;
const SERIES = /^[A-Za-z0-9_-]{1,128}$/;
const TOKEN = /^[^\s\x00-\x1f\x7f]{16,1024}$/;
const LIMIT = 5 * 1024 * 1024;

function validate(payload) {
  assert.ok(payload && typeof payload === 'object' && !Array.isArray(payload));
  assert.match(payload.user, VALID_USER);
  assert.match(payload.profile, VALID_PROFILE);
  assert.match(payload.token, TOKEN);
  assert.ok(payload.legacy && typeof payload.legacy === 'object'
    && !Array.isArray(payload.legacy));
  assert.ok(payload.legacy.shows && typeof payload.legacy.shows === 'object'
    && !Array.isArray(payload.legacy.shows));
  assert.ok(Object.keys(payload.legacy.shows).length > 0);
}

async function boundedJson(response, limit = 2 * 1024 * 1024) {
  assert.equal(response.status, 200);
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body) {
    length += chunk.byteLength;
    if (length > limit) throw new Error('UPSTREAM_BODY_OVER_LIMIT');
    chunks.push(Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

export async function runDirectAioCandidate(data, {
  upstreamBase = AIO_BASE,
  requestPath = QUERY,
} = {}) {
  validate(data);
  // CLI uses the fixed AIO loopback URL. Test injection is *only* in unit
  // tests importing this function with a localhost fake upstream.
  const base = new URL(upstreamBase);
  assert.equal(base.protocol, 'http:');
  assert.equal(base.hostname, '127.0.0.1');
  assert.match(base.pathname, /^\/jellyfin\/?$/);
  assert.equal(requestPath, QUERY);

  const cache = new Map([
    ['identity-alias:v1:' + data.profile, structuredClone(data.legacy)],
  ]);
  let ramWrites = 0;
  const db = {
    getProfile: p => p === data.profile
      ? { access_token_enc: 'RAM_ONLY_FAKE_CONNECTED_MARKER' } : null,
    cachePeek: k => structuredClone(cache.get(k) ?? null),
    cacheGet: k => structuredClone(cache.get(k) ?? null),
    cacheInsertIfAbsent(k, value) {
      if (cache.has(k)) return false;
      cache.set(k, structuredClone(value));
      ramWrites++;
      return true;
    },
    cacheSet() { throw new Error('NO_BRIDGE_SQLITE_WRITES_ALLOWED'); },
  };
  const imported = importLegacyIdentityAliases(db, data.profile,
    { dryRun: false });
  assert.equal(imported.status, 'LEGACY_IMPORTED');
  assert.equal(ramWrites, 1);
  const graph = loadVerifiedIdentityGraph(db, data.profile);
  assert.equal(graph.revision, 1);
  const aliases = Object.values(graph.shows).filter(
    s => Array.isArray(s.aliases) && s.aliases.length > 0);
  assert.equal(aliases.length, 1);

  const counters = {
    nextup: 0, usersMe: 0, series: 0, other: 0,
  };
  // Runtime outbound allowlist: only exact AIO same-token Jellyfin GETs.
  // Does not claim kernel-level egress isolation from all AIO peers.
  const allowedFetch = async (rawUrl, opts = {}) => {
    const url = new URL(rawUrl);
    if (url.origin !== base.origin
      || url.pathname !== base.pathname.replace(/\/$/, '') + '/Shows/NextUp'
        && url.pathname !== base.pathname.replace(/\/$/, '') + '/Users/Me'
        && !(url.pathname.startsWith(base.pathname.replace(/\/$/, '') + '/Items/')
             && SERIES.test(url.pathname.split('/').pop()))
      || opts.method !== 'GET' || opts.redirect !== 'manual') {
      counters.other++;
      throw new Error('DISALLOWED_UPSTREAM_GET');
    }
    const suffix = url.pathname.slice(base.pathname.replace(/\/$/, '').length);
    if (suffix === '/Shows/NextUp') {
      if (url.search !== '?Limit=20') throw new Error('NEXTUP_QUERY_CHANGED');
      counters.nextup++;
    } else if (suffix === '/Users/Me') {
      if (url.search) throw new Error('USER_QUERY_CHANGED');
      counters.usersMe++;
    } else if (suffix.startsWith('/Items/')) {
      if (url.search) throw new Error('SERIES_QUERY_CHANGED');
      counters.series++;
    }
    const requestHeaders = new Headers(opts.headers);
    if (requestHeaders.get('x-emby-token') !== data.token) {
      throw new Error('CREDENTIAL_NOT_SAME_SESSION');
    }
    return fetch(url, {
      method: 'GET', headers: requestHeaders,
      redirect: 'manual', signal: opts.signal,
    });
  };
  const user = await boundedJson(await allowedFetch(base + '/Users/Me', {
    method: 'GET', redirect: 'manual',
    headers: { 'X-Emby-Token': data.token },
    signal: AbortSignal.timeout(8000),
  }), 128 * 1024);
  assert.equal(user.Id, data.user, 'AIO_USER_ID_MISMATCH');

  const getSource = () => allowedFetch(base + QUERY, {
    method: 'GET', redirect: 'manual',
    headers: { 'X-Emby-Token': data.token },
    signal: AbortSignal.timeout(15000),
  }).then(r => boundedJson(r));

  const original = await getSource();
  assert.ok(Array.isArray(original.Items) && original.Items.length <= 20);
  assert.ok(original.Items.every(item => item && item.Type === 'Episode'
    && SERIES.test(item.SeriesId || '')));
  assert.equal(new Set(original.Items.map(item => item.Id)).size, original.Items.length);

  const gateway = createJellyfinGateway({
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayUpstreamUrl: base.toString(),
      jellyfinGatewayUserId: data.user,
      jellyfinGatewayProfileId: data.profile,
      jellyfinGatewayResumePassThrough: false,
    },
    db,
    fetchImpl: allowedFetch,
  });
  let through, metrics;
  try {
    await new Promise((ok, fail) => {
      gateway.once('error', fail);
      gateway.listen(0, '127.0.0.1', ok);
    });
    const url = 'http://127.0.0.1:' + gateway.address().port + QUERY;
    const response = await fetch(url, {
      method: 'GET', headers: { 'X-Emby-Token': data.token },
      signal: AbortSignal.timeout(25000),
    });
    through = await boundedJson(response);
    metrics = gateway.gatewayMetricsSnapshot();
  } finally {
    if (gateway.listening) {
      await new Promise(resolveClose => gateway.close(resolveClose));
    }
  }
  const after = await getSource();
  assert.deepEqual(after, original, 'AIO_SOURCE_CHANGED_DURING_PROBE');
  // Previously no real matching pair in first 20; safely STOP if live
  // dataset differs from that acceptance requirement, do not quietly pass.
  assert.deepEqual(through, original,
    'LIVE_NEXTUP_CHANGED_REVIEW_SOURCE_BEFORE_ANY_FILTER');
  assert.equal(metrics.nextUpRequests, 1);
  assert.equal(metrics.nextUpRemovedItems, 0);
  assert.equal(metrics.failOpenNormalizationErrors, 0);
  assert.equal(metrics.upstreamTransportFailures, 0);
  assert.equal(metrics.nextUpUnchangedResponses, 1);
  assert.ok(counters.usersMe >= 2);
  assert.ok(counters.nextup >= 3);
  assert.ok(counters.series > 0, 'METADATA_GETS_MUST_HAPPEN');
  assert.equal(counters.other, 0);
  assert.equal(ramWrites, 1);

  return {
    audit: 'G1C_DIRECT_AIO_GATEWAY_SAME_SESSION_GET',
    status: 'DIRECT_AIO_GATEWAY_GET_PASS',
    actual_v140_gateway_authenticated_aio_get: true,
    exact_configured_user_matched_users_me: true,
    one_connected_bridge_legacy_profile_used_in_ram: true,
    verified_alias_groups_in_ram: aliases.length,
    real_nextup_source_items: original.Items.length,
    source_stable_during_gateway_probe: true,
    gateway_preserved_real_nextup: true,
    series_metadata_same_session_gets: counters.series,
    synthetic_alias_2_to_1_previously_verified_at_g1b: true,
    explicit_user_to_profile_technical_configuration: true,
    independent_user_to_profile_ownership_proven: false,
    real_client_qualified: false,
    production_sqlite_writes: 0,
    watch_history_or_playback_writes_requested: 0,
    aio_http_methods: ['GET'],
    trakt_http_calls: 0,
    ram_only_graph_insertions: ramWrites,
    no_public_port_or_nginx_route: true,
    user_id_profile_id_imdb_ids_or_token_printed: false,
    g1_complete: false,
  };
}

async function readStdin() {
  let len = 0;
  const b = [];
  for await (const item of process.stdin) {
    const c = Buffer.from(item);
    len += c.length;
    if (len > LIMIT) throw new Error('INPUT_TOO_LARGE');
    b.push(c);
  }
  return JSON.parse(Buffer.concat(b).toString('utf8'));
}

async function cli() {
  const result = await runDirectAioCandidate(await readStdin());
  process.stdout.write(JSON.stringify(result) + '\n');
}

const invoked = process.argv[1]
  && fileURLToPath(import.meta.url) === resolve(process.argv[1]);
if (invoked) {
  cli().catch(() => {
    // Errors may embed upstream/headers/secrets: print ONLY static marker.
    process.stderr.write('G1C_STOP_DIRECT_AIO_CANDIDATE_UNQUALIFIED\n');
    process.exitCode = 5;
  });
}
