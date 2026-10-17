import test from 'node:test';
import assert from 'node:assert/strict';
import {
  assessOdinShadowReadiness,
  planOdinShadowRoute,
} from '../src/odin-g2-shadow-policy.mjs';

function sample() {
  // Synthetic counters matching the operator's 2026-10-17 G2-1A PR128
  // aggregate evidence. No login tokens or media/user identifiers.
  return {
    audit: 'G2_ODIN_MACOS_REAL_ROUTE_OBSERVE_ONLY',
    status: 'ODIN_OBSERVER_FINISHED_NO_GATEWAY_INTERCEPT',
    client_nextup_path_observed: true,
    nextup_http_200_count: 1,
    nextup_http_200_observed: true,
    observed_requests: 9, observed_nextup: 1, observed_resume: 1,
    gateway_forwarded_requests: 0, playback_writes_forwarded_to_aio: 0,
    production_sqlite_writes_requested: 0, production_nginx_reloaded: false,
    temporary_observer_removed: true, real_gateway_client_qualified: false,
    response_classes: { '2xx': 7, '3xx': 0, '4xx': 2, '5xx': 0 },
    response_class_by_route: {
      nextup: { '2xx': 1, '3xx': 0, '4xx': 0, '5xx': 0 },
      resume: { '2xx': 1, '3xx': 0, '4xx': 0, '5xx': 0 },
      userdata: { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0 },
      auth: { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0 },
      other: { '2xx': 5, '3xx': 0, '4xx': 2, '5xx': 0 },
    },
  };
}

test('operator aggregate qualifies shadow DESIGN only, no Gateway or release', () => {
  const out = assessOdinShadowReadiness(sample());
  assert.equal(out.status, 'SHADOW_DESIGN_GATES_MET_NOT_DEPLOY_AUTHORIZED');
  assert.equal(out.real_odin_nextup_http_200, true);
  assert.equal(out.real_odin_resume_2xx, true);
  assert.equal(out.other_4xx_still_unexplained, true);
  assert.equal(out.historical_items_5xx_resolved, false);
  assert.equal(out.shadow_pilot_started, false);
  assert.equal(out.actual_gateway_client_requests, 0);
  assert.equal(out.actual_gateway_client_qualified, false);
  assert.equal(out.client_response_interception_approved, false);
  assert.equal(out.production_writes_permitted, false);
  assert.equal(out.stable_release_hold, true);
});

test('a NextUp or Resume 4xx/5xx or any Other 5xx forbids shadow', () => {
  for (const [key, cls] of [
    ['nextup', '4xx'], ['nextup', '5xx'],
    ['resume', '4xx'], ['resume', '5xx'], ['other', '5xx'],
  ]) {
    const data = sample();
    data.response_class_by_route[key][cls]++;
    data.response_classes[cls]++;
    data.observed_requests++;
    if (key === 'nextup') data.observed_nextup++;
    if (key === 'resume') data.observed_resume++;
    assert.equal(assessOdinShadowReadiness(data).status,
      'SHADOW_STOP_UNQUALIFIED_CLIENT_API');
  }
});

test('no NextUp success, no Resume, partial evidence or corrupted totals fail', () => {
  const data = sample();
  data.nextup_http_200_count = 0;
  data.nextup_http_200_observed = false;
  assert.equal(assessOdinShadowReadiness(data).status,
    'SHADOW_STOP_UNQUALIFIED_CLIENT_API');

  const broken = sample();
  broken.observed_requests = 999;
  assert.equal(assessOdinShadowReadiness(broken).status,
    'SHADOW_STOP_EVIDENCE_INCONSISTENT');
  assert.equal(assessOdinShadowReadiness({}).status,
    'SHADOW_STOP_INVALID_EVIDENCE');
});

test('only a clean, completed read-only observer result is accepted', () => {
  for (const [field, value] of [
    ['gateway_forwarded_requests', 1],
    ['playback_writes_forwarded_to_aio', 1],
    ['production_sqlite_writes_requested', 1],
    ['production_nginx_reloaded', true],
    ['temporary_observer_removed', false],
    ['real_gateway_client_qualified', true],
  ]) {
    const data = sample();
    data[field] = value;
    assert.equal(assessOdinShadowReadiness(data).status,
      'SHADOW_STOP_OBSERVER_STATE_UNSAFE');
  }
});

test('even approved shadow NEVER serves any Odin response from Gateway', () => {
  const cases = [
    ['GET', '/jellyfin/Shows/NextUp?Limit=20', 'nextup', true, 'aio'],
    ['GET', '/jellyfin/UserItems/Resume?Limit=20', 'resume', false, 'aio'],
    ['GET', '/jellyfin/Items/series_one', 'other_jellyfin', false, 'aio'],
    ['GET', '/jellyfin/Users/Me', 'userdata', false, 'aio'],
    ['POST', '/jellyfin/Users/AuthenticateByName', 'authentication', false, 'aio'],
    ['POST', '/jellyfin/Sessions/Playing', 'playback_write', false, 'local_405'],
    ['POST', '/jellyfin/Sessions/Playing/Progress', 'playback_write', false, 'local_405'],
    ['PUT', '/jellyfin/Users/user_12345678/PlayedItems', 'other_jellyfin', false, 'local_405'],
  ];
  for (const [method, target, category, shadow, destination] of cases) {
    const approved = planOdinShadowRoute(method, target, { approved: true });
    assert.equal(approved.category, category);
    assert.equal(approved.clientDestination, destination);
    assert.equal(approved.shadowGatewayGet, shadow);
    assert.equal(approved.clientResponseFromGateway, false);
    assert.equal(approved.noWatchStateWritesPermitted, true);
    const defaultPlan = planOdinShadowRoute(method, target);
    assert.equal(defaultPlan.shadowGatewayGet, false);
    assert.equal(defaultPlan.clientResponseFromGateway, false);
  }
});

test('ambiguous and credential-bearing paths never shadow', () => {
  for (const uri of [
    '/Shows/NextUp?Limit=20',
    '/jellyfin/u/some_user/Shows/NextUp',
    '/jellyfin/Shows%2fNextUp',
    'http://127.0.0.1/jellyfin/Shows/NextUp',
    '//evil.invalid/jellyfin/Shows/NextUp',
    '/jellyfin/Shows/NextUp\n',
  ]) {
    const r = planOdinShadowRoute('GET', uri, { approved: true });
    assert.equal(r.shadowGatewayGet, false);
    assert.equal(r.clientResponseFromGateway, false);
  }
});
