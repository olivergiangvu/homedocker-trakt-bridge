/**
 * G2-1B one-client HomeDocker shadow candidate; no production Bridge process.
 *
 * ONLY CLI invocation starts listeners. Input is the privileged host-side,
 * read-only legacy snapshot via anonymous stdin, never argv/env/file.
 * No Trakt tokens, AIO tokens, passwords, media titles or watch state enter.
 *
 * The actual Odin client's AIO auth is held transiently by ingress, passed
 * through to the original AIO and to the same-process-owned Gateway once per
 * shadow GET. It never enters the JSON snapshot or logs.
 */
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { createOdinShadowIngress } from '../src/odin-g2-shadow-ingress.mjs';
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';
import { loadVerifiedIdentityGraph } from '../src/identity-continuity.mjs';

const PROFILE = /^[a-f0-9]{24}$/;
const USER = /^[A-Za-z0-9_-]{8,128}$/;
const SERIES = /^[A-Za-z0-9_-]{1,128}$/;
const SOURCE = 'http://127.0.0.1:3240/jellyfin';
const LIMIT = 4 * 1024 * 1024;
const MIN_LIFETIME_MS = 1000;
const MAX_LIFETIME_MS = 3 * 60 * 1000;
const READ_RESTRICTED = new Set(['/Users/Me', '/Shows/NextUp']);

export function validateShadowSnapshot(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)
    || !PROFILE.test(data.profile ?? '')
    || !USER.test(data.user ?? '')
    || !data.legacy || typeof data.legacy !== 'object'
    || !data.legacy.shows || typeof data.legacy.shows !== 'object'
    || Object.keys(data.legacy.shows).length < 1
    || Object.keys(data.legacy.shows).length > 10000) {
    throw new Error('SHADOW_STOP_INVALID_RAM_INPUT');
  }
  return true;
}

export function buildShadowRamDb(data) {
  validateShadowSnapshot(data);
  const cache = new Map([
    ['identity-alias:v1:' + data.profile, structuredClone(data.legacy)],
  ]);
  let graphInsertions = 0;
  const db = {
    getProfile: p => p === data.profile
      ? { access_token_enc: 'SYNTHETIC_CONNECTED_PROFILE_MARKER_RAM_ONLY' } : null,
    cachePeek: k => structuredClone(cache.get(k) ?? null),
    cacheGet: k => structuredClone(cache.get(k) ?? null),
    cacheInsertIfAbsent(k, v) {
      if (cache.has(k)) return false;
      cache.set(k, structuredClone(v));
      graphInsertions++;
      return true;
    },
    cacheSet() { throw new Error('SHADOW_STOP_SQLITE_WRITE_FORBIDDEN'); },
  };
  const outcome = importLegacyIdentityAliases(db, data.profile, { dryRun: false });
  if (outcome.status !== 'LEGACY_IMPORTED' || graphInsertions !== 1) {
    throw new Error('SHADOW_STOP_IDENTITY_IMPORT_UNQUALIFIED');
  }
  const graph = loadVerifiedIdentityGraph(db, data.profile);
  const aliasGroups = Object.values(graph.shows).filter(
    x => x.aliases?.length > 0).length;
  if (graph.revision !== 1 || aliasGroups !== 1
    || Object.keys(graph.shows).length !== Object.keys(data.legacy.shows).length) {
    throw new Error('SHADOW_STOP_GRAPH_SCOPE_CHANGED');
  }
  return { db, aliasGroups, showCount: Object.keys(graph.shows).length };
}

function validateLoopbackBase(base) {
  const u = new URL(base);
  if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1'
    || !/^\d+$/.test(u.port) || +u.port < 1024
    || u.pathname !== '/jellyfin' || u.search || u.hash
    || u.username || u.password) {
    throw new Error('SHADOW_STOP_AIO_ORIGIN_UNVERIFIED');
  }
  return u.origin;
}

export function makeRestrictedAioFetch(base, delegate = fetch) {
  const origin = validateLoopbackBase(base);
  const permittedPrefix = '/jellyfin';
  const families = ['nextup', 'users_me', 'metadata'];
  const counters = { get_only: 0, disallowed: 0 };
  for (const name of families) {
    counters[name + '_attempted'] = 0;
    counters[name + '_http_2xx'] = 0;
    counters[name + '_http_4xx'] = 0;
    counters[name + '_http_5xx'] = 0;
    counters[name + '_http_other'] = 0;
    counters[name + '_transport_error'] = 0;
    counters[name + '_aborted'] = 0;
  }
  const guardedFetch = async (raw, opts = {}) => {
    let u;
    try { u = new URL(raw); } catch {
      counters.disallowed++;
      throw new Error('SHADOW_STOP_UNEXPECTED_AIO_TARGET');
    }
    const route = u.pathname.startsWith(permittedPrefix)
      ? u.pathname.slice(permittedPrefix.length) : '';
    const allowed = READ_RESTRICTED.has(route)
      || (route.startsWith('/Items/')
        && SERIES.test(route.slice('/Items/'.length)));
    if (u.origin !== origin || !allowed || opts.method !== 'GET'
      || opts.redirect !== 'manual' || u.hash
      || (route !== '/Shows/NextUp' && u.search)
      || /(?:^|&)(?:token|api_key|apikey|authorization|access_token)=/i.test(u.search.slice(1))) {
      counters.disallowed++;
      throw new Error('SHADOW_STOP_AIO_GET_BOUNDARY');
    }
    counters.get_only++;
    const family = route === '/Shows/NextUp' ? 'nextup'
      : route === '/Users/Me' ? 'users_me' : 'metadata';
    counters[family + '_attempted']++;
    try {
      const response = await delegate(raw, opts);
      const classification = response.status >= 200 && response.status < 300
        ? '2xx' : response.status >= 400 && response.status < 500
          ? '4xx' : response.status >= 500 && response.status < 600
            ? '5xx' : 'other';
      counters[family + '_http_' + classification]++;
      return response;
    } catch (error) {
      // Only scalar counters: no path, ID, media, token, query, error name
      // or message is retained, emitted or logged.
      counters[family + '_transport_error']++;
      if (opts.signal?.aborted) counters[family + '_aborted']++;
      throw error;
    }
  };
  return { guardedFetch, counters };
}

async function listen(server, port) {
  if (!Number.isSafeInteger(port) || port < 0 || port > 65535) {
    throw new Error('SHADOW_STOP_PORT_INVALID');
  }
  await new Promise((ok, fail) => {
    server.once('error', fail);
    server.listen(port, '127.0.0.1', ok);
  });
  return server.address().port;
}
async function close(server) {
  if (!server.listening) return;
  server.closeAllConnections();
  await new Promise(resolveClose => server.close(resolveClose));
}

export async function startOdinShadowCandidate(data, {
  aioBase = SOURCE,
  gatewayPort = 17432,
  ingressPort = 17431,
  shadowApproved = true,
  fetchDelegate = fetch,
} = {}) {
  const { db, showCount, aliasGroups } = buildShadowRamDb(data);
  const { guardedFetch, counters: aioread } = makeRestrictedAioFetch(
    aioBase, fetchDelegate);
  const gateway = createJellyfinGateway({
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayUpstreamUrl: aioBase,
      jellyfinGatewayUserId: data.user,
      jellyfinGatewayProfileId: data.profile,
      jellyfinGatewayResumePassThrough: false,
      jellyfinGatewayNextUpTimeoutMs: 15000,
    }, db, fetchImpl: guardedFetch,
  });
  let ingress = null;
  try {
    const port = await listen(gateway, gatewayPort);
    ingress = createOdinShadowIngress({
      aioBase,
      gatewayBase: 'http://127.0.0.1:' + port,
      trustedGatewayServer: gateway,
      shadowApproved,
      // Independent absolute shadow deadline; only GET copied to Gateway.
      // Odin's original AIO response is NEVER delayed by this timer.
      shadowTimeoutMs: 25000,
    });
    await listen(ingress, ingressPort);
  } catch {
    if (ingress) await close(ingress);
    await close(gateway);
    throw new Error('SHADOW_STOP_LOOPBACK_STARTUP_FAILED');
  }
  return {
    snapshot: () => {
      const g = gateway.gatewayMetricsSnapshot();
      const i = ingress.shadowMetricsSnapshot();
      return {
        audit: 'G2_ODIN_SHADOW_ACTUAL_GATEWAY_SESSION',
        status: 'SHADOW_SESSION_FINISHED_NOT_RELEASED',
        ingress_nextup: i.client_nextup_requests,
        ingress_resume: i.client_resume_requests,
        ingress_client_aio_2xx: i.client_aio_response_2xx,
        ingress_client_aio_4xx: i.client_aio_response_4xx,
        ingress_client_aio_5xx: i.client_aio_response_5xx,
        shadow_attempted: i.shadow_attempted,
        shadow_completed: i.shadow_completed,
        shadow_errors: i.shadow_errors,
        shadow_timeouts: i.shadow_timeouts,
        shadow_2xx: i.shadow_response_2xx,
        shadow_4xx: i.shadow_response_4xx,
        shadow_5xx: i.shadow_response_5xx,
        gateway_nextup_gets: g.nextUpRequests,
        gateway_alias_removed_items: g.nextUpRemovedItems,
        gateway_fail_open_normalization: g.failOpenNormalizationErrors,
        gateway_identity_excluded_query: g.nextUpIdentityExcludedQuery,
        gateway_excluded_series_id_only: g.nextUpExcludedSeriesIdOnly,
        gateway_excluded_parent_id_only: g.nextUpExcludedParentIdOnly,
        gateway_excluded_start_index_zero_only: g.nextUpExcludedStartIndexZeroOnly,
        gateway_excluded_start_index_other_only: g.nextUpExcludedStartIndexOtherOnly,
        gateway_excluded_multiple_keys: g.nextUpExcludedMultipleKeys,
        gateway_identity_missing_auth: g.nextUpIdentityMissingAuth,
        gateway_identity_no_aliases: g.nextUpIdentityNoAliases,
        gateway_identity_invalid_items: g.nextUpIdentityInvalidItems,
        gateway_identity_below_two_items: g.nextUpIdentityBelowTwoItems,
        gateway_identity_over_limit: g.nextUpIdentityOverLimit,
        gateway_identity_eligible: g.nextUpIdentityEligible,
        gateway_identity_user_verified: g.nextUpIdentityUserVerified,
        gateway_identity_user_mismatch: g.nextUpIdentityUserMismatch,
        gateway_upstream_transport_failures: g.upstreamTransportFailures,
        gateway_nextup_responses_completed: g.nextUpCompletedResponses,
        gateway_nextup_upstream_samples: g.nextUpUpstreamSamples,
        gateway_nextup_upstream_max_ms: g.nextUpUpstreamMaxMs,
        gateway_nextup_response_max_ms: g.nextUpResponseMaxMs,
        gateway_fail_open_normalization_errors: g.failOpenNormalizationErrors,
        aio_nextup_attempted: aioread.nextup_attempted,
        aio_nextup_http_2xx: aioread.nextup_http_2xx,
        aio_nextup_http_4xx: aioread.nextup_http_4xx,
        aio_nextup_http_5xx: aioread.nextup_http_5xx,
        aio_nextup_transport_error: aioread.nextup_transport_error,
        aio_nextup_aborted: aioread.nextup_aborted,
        aio_users_me_attempted: aioread.users_me_attempted,
        aio_users_me_http_2xx: aioread.users_me_http_2xx,
        aio_users_me_transport_error: aioread.users_me_transport_error,
        aio_metadata_attempted: aioread.metadata_attempted,
        aio_metadata_http_2xx: aioread.metadata_http_2xx,
        aio_metadata_transport_error: aioread.metadata_transport_error,
        aio_get_only: aioread.get_only,
        disallowed_gateway_aio_requests: aioread.disallowed,
        verified_legacy_show_count: showCount,
        verified_legacy_alias_groups: aliasGroups,
        forwarded_watch_state_writes: 0,
        production_sqlite_writes: 0,
        real_odin_gateway_client_qualified: false,
        shadow_canary_complete: false,
        filter_response_sent_to_odin: false,
        release_hold_active: true,
      };
    },
    async close() {
      await close(ingress);
      await close(gateway);
    },
  };
}

async function readOnceFromStdin() {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.byteLength;
    if (bytes > LIMIT) throw new Error('SHADOW_STOP_INPUT_OVER_LIMIT');
    chunks.push(Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function cli() {
  const data = await readOnceFromStdin();
  const session = await startOdinShadowCandidate(data);
  // No IDs, tokens or metadata in stdout. This READY line is only after both
  // own loopback listeners are bound.
  process.stdout.write('G2_SHADOW_RUNTIME_READY_LOOPBACK_ONLY\n');
  let done = false;
  let timeout;
  const shutdown = async () => {
    if (done) return;
    done = true;
    clearTimeout(timeout);
    await session.close();
    process.stdout.write(JSON.stringify(session.snapshot()) + '\n');
  };
  process.once('SIGTERM', () => { shutdown().catch(() => process.exit(5)); });
  process.once('SIGINT', () => { shutdown().catch(() => process.exit(5)); });
  timeout = setTimeout(() => { shutdown().catch(() => process.exit(5)); },
    MAX_LIFETIME_MS);
  // Keep the event loop running via listeners; maximum 3m, stop early with Ctrl+C.
  if (MAX_LIFETIME_MS < MIN_LIFETIME_MS) throw new Error('INVALID_LIFETIME');
}
const invoked = process.argv[1]
  && fileURLToPath(import.meta.url) === resolve(process.argv[1]);
if (invoked) {
  cli().catch(() => {
    process.stderr.write('G2_SHADOW_STOP_CANDIDATE_UNQUALIFIED\n');
    process.exitCode = 5;
  });
}
