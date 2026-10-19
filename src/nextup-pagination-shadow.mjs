/**
 * RC3 research only: bounded NextUp pagination shadow auditor.
 *
 * NEVER imported by the production Gateway. No HTTP endpoints, token handling,
 * SQLite, Trakt activity, live filtering, cache persistence or client routing.
 *
 * AIO may report fetched-prefix totals, so TotalRecordCount alone cannot prove
 * completeness. This checks consecutive offsets and a second identical sweep,
 * ending on a positive short page. This is evidence, NOT a transactionally
 * frozen upstream snapshot. The output MUST NOT be served as a client page.
 */
import { filterNextUpForVerifiedAliases } from './jellyfin-nextup-filter.mjs';

const MAX_PAGE_SIZE = 50;
const MAX_ITEMS = 100; // RC2 filter itself supports at most 100 items.
const MAX_PAGES = 4;
const MAX_PAGE_BYTES = 2 * 1024 * 1024;
const MAX_AUDIT_MS = 20000;
const MAX_SINGLE_REQUEST_MS = 8000;

function requireBounds(limit, maxPages, maxItems, timeoutMs) {
  if (!Number.isSafeInteger(limit) || limit < 2 || limit > MAX_PAGE_SIZE
      || !Number.isSafeInteger(maxPages) || maxPages < 1 || maxPages > MAX_PAGES
      || !Number.isSafeInteger(maxItems) || maxItems < limit || maxItems > MAX_ITEMS
      || !Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > MAX_AUDIT_MS) {
    throw new TypeError('invalid_rc3_shadow_bounds');
  }
}

function checkPage(page, offset, limit) {
  if (!page || Array.isArray(page) || typeof page !== 'object'
      || !Array.isArray(page.Items) || page.Items.length > limit
      || page.StartIndex !== offset
      || !Number.isSafeInteger(page.TotalRecordCount)
      || page.TotalRecordCount < offset + page.Items.length
      || (Object.hasOwn(page, 'Count') && page.Count !== page.Items.length)) {
    throw new Error('PAGE_CONTRACT');
  }
  for (const item of page.Items) {
    if (!item || item.Type !== 'Episode'
        || typeof item.Id !== 'string' || item.Id.length < 1 || item.Id.length > 512
        || typeof item.SeriesId !== 'string'
        || item.SeriesId.length < 1 || item.SeriesId.length > 512) {
      throw new Error('PAGE_ITEM_CONTRACT');
    }
  }
  const serialized = JSON.stringify(page);
  if (Buffer.byteLength(serialized, 'utf8') > MAX_PAGE_BYTES) {
    throw new Error('PAGE_TOO_LARGE');
  }
  return serialized;
}

async function boundedCall(fetchPage, startIndex, limit, deadlineAt) {
  const remaining = deadlineAt - Date.now();
  if (remaining <= 0) throw new Error('TIME_BUDGET_EXHAUSTED');
  const controller = new AbortController();
  const delay = Math.min(remaining, MAX_SINGLE_REQUEST_MS);
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('PAGE_TIMEOUT'));
    }, delay);
  });
  try {
    return await Promise.race([
      Promise.resolve().then(() => fetchPage({
        startIndex, limit, signal: controller.signal,
      })),
      timeout,
    ]);
  } catch {
    throw new Error('PAGE_UNAVAILABLE');
  } finally {
    clearTimeout(timer);
  }
}

/**
 * fetchPage({startIndex, limit, signal}) must fetch JUST ONE authenticated
 * upstream NextUp page and return parsed JSON. No URL, key, token or user ID
 * is passed to / retained by the audit module. Caller owns authorization.
 *
 * The qualified result has raw items IN MEMORY so offline shadow comparison
 * can run. NEVER log/serialize those items; expose only numeric summaries.
 */
export async function auditNextUpShadow({
  fetchPage, limit = 20, maxPages = 4, maxItems = 100, timeoutMs = 12000,
} = {}) {
  requireBounds(limit, maxPages, maxItems, timeoutMs);
  if (typeof fetchPage !== 'function') throw new TypeError('fetchPage_required');
  const deadlineAt = Date.now() + timeoutMs;
  let requests = 0, pages = 0;
  const reject = reason => ({
    qualified: false, reason, requests, pages,
    firstPageLimit: limit, items: null,
  });
  const samples = [];
  const seen = new Set();
  let count = 0;
  let endedShort = false;

  try {
    // First sweep. Never infer end-of-list from an upstream TotalRecordCount
    // that might simply equal the fetched prefix or a full Limit.
    for (let pageIndex = 0; pageIndex < maxPages; pageIndex++) {
      const startIndex = pageIndex * limit;
      if (count + limit > maxItems && count >= maxItems) {
        return reject('ITEM_BUDGET');
      }
      requests++;
      const page = await boundedCall(fetchPage, startIndex, limit, deadlineAt);
      const serialized = checkPage(page, startIndex, limit);
      pages++;
      if (count + page.Items.length > maxItems) return reject('ITEM_BUDGET');
      for (const item of page.Items) {
        if (seen.has(item.Id)) return reject('OVERLAPPING_PAGES');
        seen.add(item.Id);
      }
      samples.push({ startIndex, page, serialized });
      count += page.Items.length;
      if (page.Items.length < limit) {
        // An empty page after a full page is inconclusive for facades with
        // a broken StartIndex; require a short NONEMPTY final page.
        if (!page.Items.length) return reject('EMPTY_TAIL_UNPROVEN');
        endedShort = true;
        break;
      }
    }
    if (!endedShort) return reject('FULL_PAGE_BUDGET_EXHAUSTED');

    // Either an ordinary global count or the known fetched-prefix count is
    // permitted; an inconsistent mix is NOT proof of continuity.
    const globalCount = samples.every(({page}) => page.TotalRecordCount === count);
    const prefixCount = samples.every(({page, startIndex}) =>
      page.TotalRecordCount === startIndex + page.Items.length);
    if (!globalCount && !prefixCount) return reject('INCOHERENT_TOTALS');

    // Second sweep checks the complete bounded window, not just the first
    // page. AIO pagination is NOT snapshot transactional, so mismatches
    // invalidate the audit. It still does not qualify production routing.
    for (const sample of samples) {
      requests++;
      const replay = await boundedCall(
        fetchPage, sample.startIndex, limit, deadlineAt);
      if (checkPage(replay, sample.startIndex, limit) !== sample.serialized) {
        return reject('SNAPSHOT_CHANGED');
      }
    }
    return {
      qualified: true,
      reason: 'DOUBLE_SWEEP_SHORT_END_OBSERVED',
      requests, pages, firstPageLimit: limit,
      items: samples.flatMap(({page}) => page.Items),
      totalItems: count,
      proofType: 'SHADOW_ONLY_NONTRANSACTIONAL',
    };
  } catch (err) {
    // Deliberately never return upstream errors, paths, headers or tokens.
    const allowed = ['PAGE_CONTRACT', 'PAGE_ITEM_CONTRACT',
      'PAGE_TOO_LARGE', 'TIME_BUDGET_EXHAUSTED', 'PAGE_UNAVAILABLE'];
    return reject(allowed.includes(err?.message) ? err.message : 'PAGE_UNAVAILABLE');
  }
}

/** Offline-only candidate count, never emit an alternative paginated response. */
export async function summarizeShadowAliasCandidate(audit, {
  verifiedGraph, resolveSeriesImdb,
} = {}) {
  if (!audit?.qualified || !Array.isArray(audit.items)) {
    return { decision: 'NOT_QUALIFIED', removed: 0 };
  }
  const raw = { Items: audit.items, TotalRecordCount: audit.items.length };
  try {
    const projected = await filterNextUpForVerifiedAliases(raw, {
      verifiedGraph, resolveSeriesImdb,
    });
    const removed = raw.Items.length - projected.Items.length;
    return {
      decision: removed > 0 ? 'SHADOW_CANDIDATE_ONLY' : 'NO_VERIFIED_REMOVAL',
      removed,
      observedItems: raw.Items.length,
      clientResponseChanged: false,
    };
  } catch {
    return { decision: 'METADATA_UNVERIFIED', removed: 0,
      clientResponseChanged: false };
  }
}
