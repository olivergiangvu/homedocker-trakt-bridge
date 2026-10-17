/**
 * G2-1B Odin Preview Shadow Contract (NOT A RUNNING PROXY).
 *
 * The initial client pilot must receive unchanged AIO responses. Any possible
 * shadow copy to the real Gateway is best-effort, GET-only, and only after
 * operator approval. This module cannot launch a socket, process, container,
 * or change production data. Strictly separate route evidence from live
 * filtering and distinguish a transient absence of 5xx from root-cause fix.
 */
import {
  classifyOdinPilotRequest,
} from './odin-pilot-route.mjs';

const CATEGORIES = ['nextup', 'resume', 'userdata', 'auth', 'other'];
const CLASSES = ['2xx', '3xx', '4xx', '5xx'];

function finiteCount(n) {
  return Number.isSafeInteger(n) && n >= 0;
}
function countsAreValid(counts) {
  return counts && typeof counts === 'object'
    && CLASSES.every(k => finiteCount(counts[k]));
}

export function assessOdinShadowReadiness(sample) {
  // The input is the aggregate-only observer output, never raw URLs, cookies,
  // session identifiers, access tokens, series IDs or media titles.
  if (!sample || typeof sample !== 'object'
    || sample.audit !== 'G2_ODIN_MACOS_REAL_ROUTE_OBSERVE_ONLY'
    || sample.status !== 'ODIN_OBSERVER_FINISHED_NO_GATEWAY_INTERCEPT'
    || !sample.response_class_by_route
    || !CATEGORIES.every(x => countsAreValid(sample.response_class_by_route[x]))
    || !countsAreValid(sample.response_classes)
    || !finiteCount(sample.observed_requests)
    || !finiteCount(sample.observed_nextup)
    || !finiteCount(sample.observed_resume)
    || !finiteCount(sample.nextup_http_200_count)
    || !finiteCount(sample.gateway_forwarded_requests)
    || !finiteCount(sample.playback_writes_forwarded_to_aio)
    || !finiteCount(sample.production_sqlite_writes_requested)
    || !Number.isInteger(sample.response_class_by_route.nextup['2xx'])
  ) return { status: 'SHADOW_STOP_INVALID_EVIDENCE' };

  const hasCounts = CATEGORIES.reduce((n, category) =>
    n + CLASSES.reduce((sum, clazz) =>
      sum + sample.response_class_by_route[category][clazz], 0), 0
  ) === sample.observed_requests;
  const allClassTotalsMatch = CLASSES.every(clazz =>
    CATEGORIES.reduce((sum, category) =>
      sum + sample.response_class_by_route[category][clazz], 0
    ) === sample.response_classes[clazz]
  );
  const observedMatch = sample.observed_nextup
    === CLASSES.reduce((sum, clazz) =>
      sum + sample.response_class_by_route.nextup[clazz], 0);
  const resumeMatch = sample.observed_resume
    === CLASSES.reduce((sum, clazz) =>
      sum + sample.response_class_by_route.resume[clazz], 0);
  if (!hasCounts || !allClassTotalsMatch || !observedMatch || !resumeMatch
    || sample.nextup_http_200_count > sample.response_class_by_route.nextup['2xx']
    || sample.client_nextup_path_observed !== (sample.observed_nextup > 0)
    || sample.nextup_http_200_observed !== (sample.nextup_http_200_count > 0)
  ) return { status: 'SHADOW_STOP_EVIDENCE_INCONSISTENT' };

  if (sample.gateway_forwarded_requests !== 0
    || sample.playback_writes_forwarded_to_aio !== 0
    || sample.production_sqlite_writes_requested !== 0
    || sample.production_nginx_reloaded !== false
    || sample.temporary_observer_removed !== true
    || sample.real_gateway_client_qualified !== false) {
    return { status: 'SHADOW_STOP_OBSERVER_STATE_UNSAFE' };
  }
  const nextUp = sample.response_class_by_route.nextup;
  const resume = sample.response_class_by_route.resume;
  const other = sample.response_class_by_route.other;
  if (sample.nextup_http_200_count < 1 || sample.observed_resume < 1
    || resume['2xx'] < 1 || nextUp['4xx'] > 0 || nextUp['5xx'] > 0
    || resume['4xx'] > 0 || resume['5xx'] > 0 || other['5xx'] > 0) {
    return { status: 'SHADOW_STOP_UNQUALIFIED_CLIENT_API' };
  }

  return {
    status: 'SHADOW_DESIGN_GATES_MET_NOT_DEPLOY_AUTHORIZED',
    real_odin_nextup_http_200: true,
    real_odin_resume_2xx: true,
    last_sample_other_5xx: 0,
    // Other 4xx not attributed to endpoint; require safety review and
    // explicit operator consent before even the shadow canary.
    other_4xx_still_unexplained: other['4xx'] > 0,
    historical_items_5xx_resolved: false,
    shadow_pilot_started: false,
    actual_gateway_client_requests: 0,
    actual_gateway_client_qualified: false,
    client_response_interception_approved: false,
    production_writes_permitted: false,
    stable_release_hold: true,
  };
}

/**
 * A proposed shadow router MUST NEVER direct an Odin client response into the
 * Gateway; only duplicate a bounded GET read to it, after explicit opt-in.
 * Return static flags only; never capture/return original URI or query data.
 * The runtime implementation must preserve exact client headers/queries on
 * the AIO-bound request without persisting them.
 */
export function planOdinShadowRoute(method, requestTarget, {
  approved = false,
} = {}) {
  const classified = classifyOdinPilotRequest(method, requestTarget);
  const isNextup = classified.category === 'nextup'
    && method === 'GET';
  const blockedWrite = !['GET', 'HEAD', 'OPTIONS'].includes(method)
    && !(method === 'POST'
      && classified.category === 'authentication');
  return {
    clientDestination: blockedWrite ? 'local_405' : 'aio',
    shadowGatewayGet: approved === true && isNextup && !blockedWrite,
    // Until a later, separately approved real interception, never send
    // Odin's client response from any API to the Gateway.
    clientResponseFromGateway: false,
    category: classified.category,
    noWatchStateWritesPermitted: true,
  };
}
