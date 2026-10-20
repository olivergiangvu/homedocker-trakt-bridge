/**
 * RC3/P2 prototype: backend-agnostic, bounded NextUp window assessment.
 *
 * This is an *offline/shadow-only* pure module. It is NOT imported by the
 * production Gateway, and it never serves or mutates a Jellyfin response.
 *
 * Core use case: a facade ignores StartIndex, caps Limit=50 and reports only
 * the fetched-prefix total. Two stable 50-card windows can still tell us whether
 * verified alias dedup would improve a client's first 20 *visible* cards.
 * They CANNOT prove whether a hidden 51st card exists, or supply globally
 * accurate Jellyfin pagination. Thus this module returns aggregate evidence,
 * not a rewritten Page/TotalRecordCount.
 */
import { isDeepStrictEqual } from 'node:util';
import { filterNextUpForVerifiedAliases } from './jellyfin-nextup-filter.mjs';

const MAX_WINDOW = 50;
const MAX_BYTES = 2 * 1024 * 1024;
const SAFE_LIMIT_MIN = 2;

function isInt(v) {
  return Number.isSafeInteger(v) && v >= 0;
}
function valueShape(value, max, allowEmpty = true) {
  if (!value || Array.isArray(value) || typeof value !== 'object'
      || !Array.isArray(value.Items)
      || value.Items.length > max
      || (!allowEmpty && !value.Items.length)
      || !isInt(value.TotalRecordCount)
      || (value.StartIndex !== undefined && !isInt(value.StartIndex))
      || (Object.hasOwn(value, 'Count') && value.Count !== value.Items.length)) {
    return false;
  }
  // Native Jellyfin may provide a genuine global total >= returned rows,
  // while AIO returns only a prefix total; never equate them.
  if (value.TotalRecordCount < value.Items.length) return false;
  try {
    if (Buffer.byteLength(JSON.stringify(value), 'utf8') > MAX_BYTES) return false;
  } catch { return false; }
  return value.Items.every(x => x && x.Type === 'Episode'
    && typeof x.Id === 'string' && x.Id.length > 0 && x.Id.length <= 512
    && typeof x.SeriesId === 'string'
    && x.SeriesId.length > 0 && x.SeriesId.length <= 512);
}

function strictPageEcho(value, requestedOffset) {
  if (!value || !Object.hasOwn(value, 'StartIndex')) return 'ABSENT';
  return value.StartIndex === requestedOffset ? 'MATCH' : 'MISMATCH';
}

/**
 * Observational classification from exactly two externally collected pages.
 * No HTTP performed here; a caller must use SAME Jellyfin user and request
 * options, and collect pages close together in time.
 *
 * A response with no StartIndex echo but different IDs is not confirmed
 * offset-honoring: only an observational hint.
 */
export function classifyNextUpPagination({ first, second, limit = 20 } = {}) {
  if (!Number.isSafeInteger(limit) || limit < SAFE_LIMIT_MIN || limit > MAX_WINDOW
      || !valueShape(first, limit)
      || !valueShape(second, limit)) {
    return { mode: 'UNQUALIFIED', reason: 'INVALID_PAGE_CONTRACT' };
  }
  const firstIds = first.Items.map(x => x.Id);
  const secondIds = second.Items.map(x => x.Id);
  if (new Set(firstIds).size !== firstIds.length
      || new Set(secondIds).size !== secondIds.length) {
    return { mode: 'UNQUALIFIED', reason: 'DUPLICATE_EPISODE_IDS' };
  }
  const overlap = secondIds.filter(id => firstIds.includes(id)).length;
  const firstEcho = strictPageEcho(first, 0);
  const secondEcho = strictPageEcho(second, limit);
  if (firstEcho === 'MISMATCH') {
    return { mode: 'UNQUALIFIED', reason: 'FIRST_PAGE_OFFSET_MISMATCH' };
  }
  if (firstIds.length === limit && secondIds.length === limit
      && isDeepStrictEqual(firstIds, secondIds)) {
    return { mode: 'OFFSET_IGNORED_OBSERVED', reason: 'REPEATED_FULL_FIRST_PAGE',
      echo: secondEcho, overlapCount: overlap };
  }
  if (secondEcho === 'MISMATCH') {
    return { mode: 'UNQUALIFIED', reason: 'SECOND_PAGE_OFFSET_MISMATCH',
      overlapCount: overlap };
  }
  if (overlap > 0) {
    return { mode: 'UNQUALIFIED', reason: 'CROSS_PAGE_OVERLAP',
      overlapCount: overlap };
  }
  if (firstIds.length < limit) {
    return { mode: 'UNQUALIFIED', reason: 'NO_SECOND_PAGE_NEED',
      overlapCount: overlap };
  }
  if (secondEcho === 'ABSENT') {
    return { mode: 'OFFSET_PLAUSIBLE_NO_ECHO',
      reason: 'DISJOINT_IDS_WITHOUT_OFFSET_ECHO', overlapCount: 0 };
  }
  return { mode: 'OFFSET_HONORED_OBSERVED',
    reason: 'DISJOINT_IDS_WITH_OFFSET_ECHO', overlapCount: 0 };
}

/**
 * Offline assessment of a client-visible *first page* from an independent,
 * bounded, double-observed upstream window. Returns metrics only.
 *
 * original: GET NextUp?StartIndex=0&Limit=clientLimit
 * windowA/B: two GETs of StartIndex=0&Limit=windowLimit (max 50)
 * identityVerified: caller independently proved Users/Me is exactly the
 * configured Gateway Jellyfin user for the same token used in ALL GETs.
 *
 * Metadata resolution must use the SAME authenticated user's Items/SeriesId.
 * If the upstream returns 50/50, this is at most a *coverage opportunity* for
 * first-page dedup, NOT a complete NextUp list or an exact total.
 */
export async function assessAdaptiveFirstPage({
  original, windowA, windowB, clientLimit = 20, windowLimit = 50,
  identityVerified = false, verifiedGraph, resolveSeriesImdb,
} = {}) {
  const fail = (reason) => Object.freeze({
    status: 'UNQUALIFIED', reason, candidateRemoved: 0,
    clientVisibleDifference: false, clientResponseChanged: false,
    globalPaginationQualified: false,
  });
  if (!Number.isSafeInteger(clientLimit)
      || clientLimit < SAFE_LIMIT_MIN || clientLimit > MAX_WINDOW
      || !Number.isSafeInteger(windowLimit)
      || windowLimit <= clientLimit || windowLimit > MAX_WINDOW) {
    return fail('INVALID_BOUNDS');
  }
  if (identityVerified !== true) return fail('USER_IDENTITY_UNVERIFIED');
  if (!valueShape(original, clientLimit) || !valueShape(windowA, windowLimit)
      || !valueShape(windowB, windowLimit)) return fail('SOURCE_SCHEMA_UNVERIFIED');
  if (!isDeepStrictEqual(windowA, windowB)) return fail('WINDOW_DRIFT');
  if (windowA.StartIndex !== undefined && windowA.StartIndex !== 0
      || original.StartIndex !== undefined && original.StartIndex !== 0) {
    return fail('FIRST_PAGE_OFFSET_UNEXPECTED');
  }
  if (windowA.Items.length < original.Items.length) return fail('WINDOW_TOO_SHORT');
  const prefix = windowA.Items.slice(0, original.Items.length);
  if (!isDeepStrictEqual(prefix, original.Items)) return fail('WINDOW_PREFIX_MISMATCH');
  const ids = windowA.Items.map(x => x.Id);
  if (new Set(ids).size !== ids.length) return fail('WINDOW_EPISODE_ID_OVERLAP');
  if (typeof resolveSeriesImdb !== 'function') return fail('METADATA_RESOLVER_UNAVAILABLE');
  if (!verifiedGraph?.shows || !Object.values(verifiedGraph.shows).some(
    x => Array.isArray(x?.aliases) && x.aliases.length > 0)) {
    return fail('NO_VERIFIED_ALIAS_GRAPH');
  }

  const complete = windowA.Items.length < windowLimit
    && windowA.TotalRecordCount === windowA.Items.length
    && (windowA.StartIndex === undefined || windowA.StartIndex === 0);
  try {
    const normalized = await filterNextUpForVerifiedAliases({
      Items: windowA.Items,
      TotalRecordCount: windowA.Items.length,
    }, { verifiedGraph, resolveSeriesImdb });
    const removed = windowA.Items.length - normalized.Items.length;
    if (removed <= 0) {
      return {
        status: 'NO_PROVEN_ALIAS_REMOVAL', reason: 'FILTER_FAIL_OPEN_OR_NO_PAIR',
        candidateRemoved: 0, clientVisibleDifference: false,
        observedWindowItems: windowA.Items.length, windowCompleteObserved: complete,
        clientResponseChanged: false, globalPaginationQualified: false,
      };
    }
    if (normalized.Items.length < clientLimit) return fail('INSUFFICIENT_WINDOW_AFTER_FILTER');
    const rawVisible = original.Items.slice(0, clientLimit).map(x => x.Id);
    const projectedVisible = normalized.Items.slice(0, clientLimit).map(x => x.Id);
    const visibleDiff = !isDeepStrictEqual(rawVisible, projectedVisible);
    return {
      status: visibleDiff ? 'SHADOW_VISIBLE_CANDIDATE' : 'SHADOW_NONVISIBLE_CANDIDATE',
      reason: complete ? 'BOUNDED_SHORT_WINDOW' : 'BOUNDED_PREFIX_ONLY',
      candidateRemoved: removed,
      clientVisibleDifference: visibleDiff,
      observedWindowItems: windowA.Items.length,
      projectedFirstPageItems: projectedVisible.length,
      windowCompleteObserved: complete,
      clientResponseChanged: false,
      // Not even a short window is a transactional snapshot across requests.
      globalPaginationQualified: false,
    };
  } catch {
    return fail('METADATA_OR_FILTER_UNAVAILABLE');
  }
}
