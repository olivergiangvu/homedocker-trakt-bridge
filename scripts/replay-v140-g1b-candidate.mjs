/**
 * G1B: isolated real-AIO-snapshot + verified RC6 legacy-identity replay.
 *
 * Node runs only in ephemeral Docker --network none, no mounted production DB,
 * no Trakt/AIO credentials and no host-published ports. All upstream calls are
 * an injected, strict in-memory fetch stub populated from an AIO-authenticated
 * snapshot captured by the host-side Python orchestrator.
 *
 * This demonstrates real input replay + synthetic duplicate, NOT direct
 * Gateway<->AIO network auth nor real-client or host ingress acceptance.
 */
import assert from 'node:assert/strict';
import { createJellyfinGateway } from '../src/jellyfin-gateway.mjs';
import { importLegacyIdentityAliases } from '../src/identity-legacy-import.mjs';
import { loadVerifiedIdentityGraph } from '../src/identity-continuity.mjs';

const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const TEST_TOKEN = 'G1B_OFFLINE_SYNTHETIC_TOKEN_ONLY';
const UPSTREAM = 'http://g1b-snapshot.invalid/jellyfin';
const PATH = '/Shows/NextUp?Limit=20';

async function input() {
  let size = 0;
  const parts = [];
  for await (const chunk of process.stdin) {
    const buf = Buffer.from(chunk);
    size += buf.length;
    if (size > MAX_INPUT_BYTES) throw new Error('snapshot_exceeded_bound');
    parts.push(buf);
  }
  const parsed = JSON.parse(Buffer.concat(parts).toString('utf8'));
  assert.ok(parsed && typeof parsed === 'object');
  assert.match(parsed.profile, /^[a-f0-9]{24}$/);
  assert.match(parsed.user, /^[A-Za-z0-9_-]{8,128}$/);
  assert.ok(parsed.legacy && typeof parsed.legacy === 'object');
  assert.ok(parsed.nextup && Array.isArray(parsed.nextup.Items));
  assert.ok(parsed.nextup.Items.length <= 20);
  assert.ok(parsed.details && typeof parsed.details === 'object');
  return parsed;
}

function memoryDb(data) {
  const cache = new Map([['identity-alias:v1:' + data.profile, structuredClone(data.legacy)]]);
  let inserted = 0;
  const db = {
    getProfile(profile) {
      return profile === data.profile ? { access_token_enc: 'MEMORY_MARKER_ONLY' } : null;
    },
    cachePeek(k) { return structuredClone(cache.get(k) ?? null); },
    cacheGet(k) { return structuredClone(cache.get(k) ?? null); },
    cacheInsertIfAbsent(k, value) {
      if (cache.has(k)) return false;
      cache.set(k, structuredClone(value));
      inserted++;
      return true;
    },
    cacheSet() { throw new Error('G1B_FORBIDS_NONIMPORT_WRITES'); },
  };
  return { db, writes: () => inserted, legacy: () => cache.get('identity-alias:v1:' + data.profile) };
}

function syntheticCase(graph, compete = false) {
  const entry = Object.values(graph.shows)
    .find(row => Array.isArray(row.aliases) && row.aliases.length > 0);
  assert.ok(entry, 'verified_alias_pair_required');
  const source = {
    Items: [
      { Id: 'g1b_fake_alias_episode', SeriesId: 'g1b_fake_alias_series',
        Type: 'Episode', IndexNumber: 8,
        UserData: { PlaybackPositionTicks: compete ? 12000 : 0,
          LastPlayedDate: '2026-10-11T10:00:00Z' } },
      { Id: 'g1b_fake_canonical_episode', SeriesId: 'g1b_fake_canonical_series',
        Type: 'Episode', IndexNumber: 8,
        UserData: { PlaybackPositionTicks: 25000,
          LastPlayedDate: '2026-10-12T10:00:00Z' } },
    ],
    TotalRecordCount: 2, Count: 2,
    Opaque: 'FAKE_ROWS_NEVER_INSERTED_IN_AIO',
  };
  return {
    source,
    details: {
      g1b_fake_alias_series: {
        Id: 'g1b_fake_alias_series', Type: 'Series',
        ProviderIds: { Imdb: entry.aliases[0] },
      },
      g1b_fake_canonical_series: {
        Id: 'g1b_fake_canonical_series', Type: 'Series',
        ProviderIds: { Imdb: entry.canonicalImdb },
      },
    },
  };
}

async function runGateway(data, db, source, details, { wrongUser = false } = {}) {
  let incoming = 0;
  let disallowed = 0;
  const fetchImpl = async (raw, opts) => {
    const url = new URL(raw);
    if (url.origin !== 'http://g1b-snapshot.invalid'
      || !url.pathname.startsWith('/jellyfin/')
      || opts.method !== 'GET'
      || opts.headers.get('x-emby-token') !== TEST_TOKEN) {
      disallowed++;
      throw new Error('upstream_request_outside_exact_memory_fixture');
    }
    incoming++;
    const p = url.pathname.slice('/jellyfin'.length);
    let payload;
    if (p === '/Users/Me') {
      payload = { Id: wrongUser ? (data.user === 'g1b_test_other' ? 'g1b_test_other2' : 'g1b_test_other') : data.user };
    } else if (p === '/Shows/NextUp' && url.search === '?Limit=20') {
      payload = source;
    } else if (p.startsWith('/Items/') && Object.hasOwn(details, p.slice(7))) {
      payload = details[p.slice(7)];
    } else {
      disallowed++;
      throw new Error('unknown_memory_fixture_route');
    }
    return new Response(JSON.stringify(payload), {
      status: 200, headers: { 'content-type': 'application/json',
        'x-source-provenance': 'g1b-offline-injected-snapshot' },
    });
  };
  const gateway = createJellyfinGateway({
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayUpstreamUrl: UPSTREAM,
      jellyfinGatewayUserId: data.user,
      jellyfinGatewayProfileId: data.profile,
      jellyfinGatewayResumePassThrough: false,
    },
    db,
    fetchImpl,
  });
  try {
    await new Promise((resolve, reject) => {
      gateway.once('error', reject);
      gateway.listen(0, '127.0.0.1', resolve);
    });
    const address = 'http://127.0.0.1:' + gateway.address().port + PATH;
    const r = await fetch(address, {
      method: 'GET', headers: { 'X-Emby-Token': TEST_TOKEN },
      signal: AbortSignal.timeout(15000),
    });
    assert.equal(r.status, 200);
    const body = await r.json();
    return { body, requests: incoming, disallowed,
      metrics: gateway.gatewayMetricsSnapshot() };
  } finally {
    await new Promise(resolve => gateway.close(resolve));
  }
}

function unchanged(source, result) {
  assert.deepEqual(result.body, source, 'source_projection_must_be_unchanged');
  assert.equal(result.metrics.nextUpRemovedItems, 0);
  assert.equal(result.disallowed, 0);
}

async function main() {
  const data = await input();
  const m = memoryDb(data);
  const sourceCopy = structuredClone(data.legacy);
  const originalNextUp = structuredClone(data.nextup);
  assert.equal(importLegacyIdentityAliases(m.db, data.profile).status, 'DRY_RUN_READY');
  assert.equal(m.writes(), 0, 'dry_run_must_not_write_RAM');
  const actual = importLegacyIdentityAliases(m.db, data.profile, { dryRun: false });
  assert.equal(actual.status, 'LEGACY_IMPORTED');
  assert.equal(m.writes(), 1);
  assert.deepEqual(m.legacy(), sourceCopy, 'legacy_cache_must_remain_identical');
  assert.equal(importLegacyIdentityAliases(m.db, data.profile, { dryRun: false }).status,
    'GRAPH_ALREADY_EXISTS');
  const graph = loadVerifiedIdentityGraph(m.db, data.profile);
  const aliases = Object.values(graph.shows).filter(row => row.aliases.length > 0);
  assert.equal(graph.revision, 1);
  assert.equal(aliases.length, 1);

  // Live 20-item sample from AIO's own authenticated session. If there is no
  // verified duplicate, identity-aware Gateway must be an *exact passthrough*.
  const live = await runGateway(data, m.db, data.nextup, data.details);
  assert.equal(live.disallowed, 0);
  assert.ok(live.requests >= 1);
  // G1A had no visible pair; this step is a contract on that exact sample.
  unchanged(data.nextup, live);
  assert.deepEqual(data.nextup, originalNextUp);

  // Actual v1.4 HTTP server + real verified alias imported IN RAM,
  // but two fake episode cards constructed exclusively inside this process.
  const positive = syntheticCase(graph, false);
  const yes = await runGateway(data, m.db, positive.source, positive.details);
  assert.equal(yes.disallowed, 0);
  assert.equal(yes.body.Items.length, 1);
  assert.deepEqual(yes.body.Items, [positive.source.Items[1]]);
  assert.equal(yes.body.TotalRecordCount, 1);
  assert.equal(yes.body.Count, 1);
  assert.equal(yes.body.Opaque, positive.source.Opaque);
  assert.equal(yes.metrics.nextUpRemovedItems, 1);

  const competing = syntheticCase(graph, true);
  unchanged(competing.source, await runGateway(data, m.db,
    competing.source, competing.details));
  unchanged(positive.source, await runGateway(data, m.db,
    positive.source, positive.details, { wrongUser: true }));

  // No disk DB, HTTP egress, real token, user/account details or image changes.
  // All identifiers remain only in input RAM and are not printed.
  assert.equal(m.writes(), 1);
  process.stdout.write(JSON.stringify({
    audit: 'G1B_REAL_SOURCE_ISOLATED_GATEWAY_MEMORY_REPLAY',
    status: 'OFFLINE_SNAPSHOT_GATEWAY_REPLAY_PASS',
    connected_bridge_legacy_shows: actual.showCount,
    imported_graph_alias_groups: aliases.length,
    graph_revision_in_ram: graph.revision,
    actual_v140_js_importer_and_http_gateway_executed: true,
    aio_snapshot_rows_replayed: data.nextup.Items.length,
    live_snapshot_unchanged: true,
    synthetic_alias_before: 2,
    synthetic_alias_after: 1,
    synthetic_positive_filter_pass: true,
    two_active_resumes_passthrough: true,
    mismatched_user_passthrough: true,
    gateway_http_network_real_aio: false,
    authenticated_real_aio_snapshot_prepared_by_host: true,
    aio_user_to_bridge_profile_ownership_independently_proven: false,
    real_client_qualified: false,
    production_db_writes: 0,
    ram_only_identity_graph_writes: 1,
    real_aio_or_trakt_calls_from_container: 0,
    public_ports_or_nginx_changed: false,
    no_real_tokens_transmitted_to_container: true,
    g1_complete: false,
  }) + '\n');
}

main().catch(() => {
  process.stderr.write('G1B_STOP_ISOLATED_GATEWAY_REPLAY_FAILED\n');
  process.exitCode = 5;
});
