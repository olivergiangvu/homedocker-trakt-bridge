/**
 * Release Recovery: one-core, two-client read-only canary policy.
 * Pure helpers, NOT imported into live RC2 Gateway or any HTTP route.
 *
 * AIOStreams Desktop upstream issues GET /Shows/NextUp with userId,
 * Limit=24 and EnableResumable=false (its packages/web/src/lib/queries.ts).
 * It uses Jellyfin-compatible auth/QuickConnect, not a separate NextUp API.
 * Odin exact request shape is NOT captured. Do not infer from User-Agent.
 * Auth/QuickConnect, Resume, playback, POST are always passthrough.
 */
const NEXTUP = '/Shows/NextUp';
export const CANARY_CLIENT = Object.freeze({
  ODIN: 'odin',
  AIO_DESKTOP: 'aio-desktop',
});
function exactQuery(params, expected) {
  const values = [...params.entries()];
  if (values.length !== Object.keys(expected).length) return false;
  for (const [key, value] of Object.entries(expected)) {
    if (params.getAll(key).length !== 1 || params.get(key) !== value) return false;
  }
  return true;
}
export function classifyCanaryRead({
  method, path, query, client,
  authenticatedUserId, expectedMappedUserId,
} = {}) {
  if (method !== 'GET' || path !== NEXTUP) return 'UPSTREAM_PASSTHROUGH';
  if (typeof authenticatedUserId !== 'string' || !authenticatedUserId ||
      authenticatedUserId !== expectedMappedUserId) {
    return 'USER_NOT_QUALIFIED';
  }
  if (typeof query !== 'string') return 'REQUEST_NOT_QUALIFIED';
  const params = new URLSearchParams(query.startsWith('?') ? query.slice(1) : query);
  if (client === CANARY_CLIENT.AIO_DESKTOP) {
    // Query userId never substitutes for server-authenticated /Users/Me.
    return exactQuery(params, {
      userId: authenticatedUserId,
      Limit: '24',
      EnableResumable: 'false',
    }) ? 'AIO_DESKTOP_NEXTUP_READ' : 'REQUEST_NOT_QUALIFIED';
  }
  // Odin query, ordering and paging semantics are not yet qualified.
  return 'ODIN_CONTRACT_PENDING';
}
/**
 * Evaluate a hypothetical display opportunity on an EXACT short NextUp
 * page. No HTTP response is created or modified here. A full page must
 * not silently lose its last item or exceed its 24-card request Limit.
 */
export function previewAppendOpportunity({ upstream, episode, proof, limit = 24 } = {}) {
  const items = upstream?.Items;
  if (!Array.isArray(items) || !Number.isSafeInteger(limit) ||
      limit < 1 || limit > 50 || items.length > limit ||
      upstream?.StartIndex !== 0 ||
      !Number.isSafeInteger(upstream?.TotalRecordCount) ||
      upstream.TotalRecordCount !== items.length ||
      (Object.hasOwn(upstream, 'Count') && upstream.Count !== items.length)) {
    return 'UNSAFE_OR_UNKNOWN_PAGE';
  }
  if (items.length === limit) return 'FULL_PAGE_NO_INSERTION';
  if (!proof || proof.mappedUser !== true ||
      proof.offWindow !== true ||
      proof.seriesValidatedByBackend !== true ||
      proof.episodeInRealCatalog !== true ||
      proof.episodeDetailValidatedByBackend !== true) {
    return 'OFFWINDOW_EVIDENCE_INCOMPLETE';
  }
  if (!episode || episode.Type !== 'Episode' ||
      typeof episode.Id !== 'string' || !/^[0-9a-f]{32}$/.test(episode.Id) ||
      typeof episode.SeriesId !== 'string' ||
      !/^[0-9a-f]{32}$/.test(episode.SeriesId) ||
      episode.UserData?.Played !== false) {
    return 'EPISODE_SHAPE_UNQUALIFIED';
  }
  if (items.some(item => item?.Id === episode.Id ||
      item?.SeriesId === episode.SeriesId)) {
    return 'ALREADY_PRESENT';
  }
  // The result is an evaluation, not a new TotalRecordCount. AIO's
  // fetched-prefix total is not a proven global total.
  return 'SHADOW_CANDIDATE_INSERTABLE_ON_SHORT_PAGE_ONLY';
}
