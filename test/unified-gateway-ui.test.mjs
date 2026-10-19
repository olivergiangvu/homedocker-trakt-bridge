import test from 'node:test';
import assert from 'node:assert/strict';
import { renderUnifiedGatewayPanel } from '../src/unified-gateway-panel.mjs';
import { buildGatewayDashboardStatus } from '../src/gateway-dashboard.mjs';

const PROFILE = 'a'.repeat(24);
const USER = '11111111abcdef00';
const status = (extras = {}) => ({
  state: 'listener_active_not_client_qualified',
  enabled: true, listening: true,
  upstreamConfigured: true, userMappingConfigured: true,
  nextUpMode: 'short_first_page_only', resumeMode: 'off',
  identity: { verifiedShows: 17, aliasGroups: 1,
    verifiedAliases: 1, quarantinedConflicts: 0 },
  telemetry: { nextUpRequests: 7, nextUpNormalizedResponses: 2,
    nextUpRemovedItems: 2, nextUpUnchangedResponses: 5,
    nextUpPagedPassThrough: 3, failOpenNormalizationErrors: 0,
    nextUpAverageMs: 92, aioUpstreamAverageMs: 51,
    metadataActive: 2, metadataQueued: 0,
    upstreamTransportFailures: 0, upstreamNon200Responses: 0 },
  ...extras,
});

test('unified panel has identity, safety, telemetry and navigation in ONE dashboard', () => {
  const html = renderUnifiedGatewayPanel(status());
  for (const label of [
    'AIO Identity Gateway', 'Unified Bridge', 'history authority',
    'Verified aliases', 'Gateway listener', 'User identity mapping',
    'NextUp response policy', 'Removed duplicate cards',
    'Paged requests passed through', 'Fail-open normalizations',
    'Gateway mean latency', 'AIO mean latency', 'Metadata active / queued',
    'V140_STABLE_RELEASE_HOLD', 'Production client routing is not qualified',
  ]) assert.ok(html.includes(label), label);
  assert.match(html, /id="aio-identity-gateway"/);
  assert.ok(!html.includes('github.com'));
  assert.match(html, /Read-only/);
  assert.match(html, />17</);
  assert.match(html, />7</);
});

test('Gateway panel escapes diagnostic URL and never adds mutating controls', () => {
  const html = renderUnifiedGatewayPanel(status(), {
    diagnosticsUrl: '/u/test/status.json?key="><script>alert(1)</script>',
  });
  assert.ok(!html.includes('<script>'));
  assert.match(html, /&lt;script&gt;/);
  assert.ok(!html.includes('<form'));
  assert.ok(!html.includes('onclick='));
  assert.match(html, /Read-only JSON diagnostics/);
});

test('disabled panel says OFF, not running nor client qualified', () => {
  const html = renderUnifiedGatewayPanel(status({
    state: 'disabled', enabled: false, listening: false,
    telemetry: null, userMappingConfigured: false,
  }), { compact: true });
  assert.match(html, /Installed · OFF/);
  assert.match(html, /Not listening/);
  assert.match(html, /Not confirmed/);
  assert.match(html, /—/);
  assert.ok(!html.includes('Listener only · client route unverified'));
});

test('status JSON exposes paging safety without leaking user, profile or tokens', () => {
  const data = buildGatewayDashboardStatus({
    db: {
      cachePeek: () => ({ revision: 1, shows: {}, conflicts: [] }),
    },
    config: {
      jellyfinGatewayEnabled: true,
      jellyfinGatewayProfileId: PROFILE,
      jellyfinGatewayUserId: USER,
      jellyfinGatewayUpstreamUrl: 'http://aiostreams:3000/jellyfin',
      jellyfinGatewayFirstPageMode: 'short_first_page_only',
      jellyfinGatewayResumePassThrough: false,
    },
    profileId: PROFILE,
    gateway: {
      listening: true,
      gatewayMetricsSnapshot: () => ({
        nextUpRequests: 10, nextUpPagedPassThrough: 4,
      }),
    },
  });
  assert.equal(data.nextUpMode, 'short_first_page_only');
  assert.equal(data.telemetry.nextUpPagedPassThrough, 4);
  assert.equal(data.productionRoutingQualified, false);
  const json = JSON.stringify(data);
  assert.ok(!json.includes(USER));
  assert.ok(!json.includes(PROFILE));
  assert.ok(!json.includes('aiostreams:3000'));
});
