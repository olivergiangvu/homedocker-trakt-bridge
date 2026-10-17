/**
 * G2-1 Odin route discovery contract only, NOT an ingress/service.
 * Observe mode always passes requests to AIO.
 * Intercept mode is a separately authorized future pilot specification.
 * Classify without logging or storing URL, query, auth, IDs or watch state.
 */
export const ODIN_MODE_OBSERVE = 'observe';
export const ODIN_MODE_INTERCEPT = 'intercept';
const userId = '[A-Za-z0-9_-]{8,128}';
const resumeLegacy = new RegExp('^/jellyfin/Users/' + userId + '/Items/Resume$');
const userItems = new RegExp('^/jellyfin/Users/' + userId + '/Items$');
const playback = /^\/jellyfin\/Sessions\/Playing(?:\/(?:Progress|Stopped))?$/;
const validMethods = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$/;
const asciiPath = /^\/[\x21-\x7e]*$/;
const categories = Object.freeze([
  'nextup', 'resume', 'userdata', 'authentication', 'playback_write',
  'other_jellyfin', 'other_aio', 'unknown',
]);

export function classifyOdinPilotRequest(method, target, { mode = ODIN_MODE_OBSERVE } = {}) {
  if (![ODIN_MODE_OBSERVE, ODIN_MODE_INTERCEPT].includes(mode)) {
    throw new TypeError('odin_pilot_mode_invalid');
  }
  if (typeof method !== 'string' || !validMethods.test(method)
      || typeof target !== 'string' || target.length > 4096
      || !asciiPath.test(target) || target.startsWith('//')
      || target.includes('#') || target.includes('\\')) {
    return { category: 'unknown', upstream: 'aio' };
  }
  // No URL parsing, percent-decoding, path or query normalization.
  const ix = target.indexOf('?');
  const path = ix < 0 ? target : target.slice(0, ix);
  if (method === 'GET' && path === '/jellyfin/Shows/NextUp') {
    return mode === ODIN_MODE_INTERCEPT
      ? { category: 'nextup', upstream: 'gateway', upstreamPath: '/Shows/NextUp' }
      : { category: 'nextup', upstream: 'aio' };
  }
  if (method === 'GET' && (
    path === '/jellyfin/UserItems/Resume' || resumeLegacy.test(path))) {
    return { category: 'resume', upstream: 'aio' };
  }
  if (method === 'GET' && (
    path === '/jellyfin/Users/Me' || userItems.test(path))) {
    return { category: 'userdata', upstream: 'aio' };
  }
  if (path === '/jellyfin/Users/AuthenticateByName') {
    return { category: 'authentication', upstream: 'aio' };
  }
  if (method !== 'GET' && playback.test(path)) {
    return { category: 'playback_write', upstream: 'aio' };
  }
  if (path === '/jellyfin' || path.startsWith('/jellyfin/')) {
    return { category: 'other_jellyfin', upstream: 'aio' };
  }
  if (path.startsWith('/')) {
    return { category: 'other_aio', upstream: 'aio' };
  }
  return { category: 'unknown', upstream: 'aio' };
}

export function createOdinPilotCounters() {
  const counts = Object.fromEntries(categories.map(x => [x, 0]));
  let total = 0;
  let gateway = 0;
  return {
    record(result) {
      if (!result || !categories.includes(result.category)
        || !['aio', 'gateway'].includes(result.upstream)) {
        throw new TypeError('invalid_pilot_route');
      }
      counts[result.category]++;
      total++;
      if (result.upstream === 'gateway') gateway++;
    },
    snapshot() {
      // Bounded anonymous counters only. No request metadata is stored.
      return {
        audit: 'G2_ODIN_ROUTE_DISCOVERY_COUNTERS_ONLY',
        requests: total,
        nextup: counts.nextup,
        resume: counts.resume,
        userdata: counts.userdata,
        authentication: counts.authentication,
        playbackWrite: counts.playback_write,
        otherJellyfin: counts.other_jellyfin,
        otherAio: counts.other_aio,
        unknown: counts.unknown,
        gatewayForwarded: gateway,
      };
    },
  };
}
