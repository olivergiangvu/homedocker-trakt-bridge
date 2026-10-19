/**
 * v1.4 target: one Trakt Bridge dashboard for the optional AIO Identity Gateway.
 *
 * Read-only projection of the Bridge-owned profile-scoped verified identity graph.
 * Never query AIO/Trakt here, write watch state, expose user/profile/token IDs,
 * or infer that a listening socket proves real-client routing.
 */
import { loadVerifiedIdentityGraph } from './identity-continuity.mjs';

export function buildGatewayDashboardStatus({ db, config, profileId, gateway = null }) {
  const mapped = typeof profileId === 'string' && profileId.length > 0
    && config.jellyfinGatewayProfileId === profileId;
  // cacheGet() can delete stale rows. Dashboard reads MUST use cachePeek().
  const canPeek = typeof db?.cachePeek === 'function';
  const graph = mapped && canPeek
    ? loadVerifiedIdentityGraph({ cacheGet: (key) => db.cachePeek(key) }, profileId)
    : { revision: 0, shows: {}, conflicts: [] };
  const shows = Object.values(graph.shows);
  const aliased = shows.filter((entry) => entry.aliases.length > 0);
  const configured = config.jellyfinGatewayEnabled === true;
  const listening = mapped && configured && gateway?.listening === true;
  const mappingReady = mapped
    && /^[A-Za-z0-9_-]{8,128}$/.test(config.jellyfinGatewayUserId || '');
  const telemetry = mapped && listening
    && typeof gateway?.gatewayMetricsSnapshot === 'function'
    ? gateway.gatewayMetricsSnapshot() : null;
  const safeCount = (name) => Number.isSafeInteger(telemetry?.[name])
    && telemetry[name] >= 0 ? telemetry[name] : 0;
  const averageMs = (countKey, totalKey) => {
    const count = safeCount(countKey), total = safeCount(totalKey);
    return count ? Math.round(total / count) : null;
  };
  const metrics = telemetry ? {
    scope: 'listener_wide_since_restart',
    nextUpRequests: safeCount('nextUpRequests'),
    resumePassthroughRequests: safeCount('resumePassthroughRequests'),
    nextUpNormalizedResponses: safeCount('nextUpNormalizedResponses'),
    nextUpUnchangedResponses: safeCount('nextUpUnchangedResponses'),
    nextUpRemovedItems: safeCount('nextUpRemovedItems'),
    nextUpPagedPassThrough: safeCount('nextUpPagedPassThrough'),
    failOpenNormalizationErrors: safeCount('failOpenNormalizationErrors'),
    upstreamTransportFailures: safeCount('upstreamTransportFailures'),
    upstreamNon200Responses: safeCount('upstreamNon200Responses'),
    responseFailures: safeCount('responseFailures'),
    nextUpAverageMs: averageMs('nextUpCompletedResponses', 'nextUpResponseTotalMs'),
    aioUpstreamAverageMs: averageMs('nextUpUpstreamSamples', 'nextUpUpstreamTotalMs'),
    metadataActive: safeCount('metadataActive'),
    metadataQueued: safeCount('metadataQueued'),
  } : null;
  const state = !configured ? 'disabled'
    : !mapped ? 'not_mapped_to_this_profile'
      : !mappingReady ? 'mapping_incomplete'
        : listening ? 'listener_active_not_client_qualified'
          : 'configured_not_listening';

  return {
    module: 'aio-identity-gateway',
    targetRelease: '1.4.0',
    state,
    enabled: configured,
    listening: Boolean(listening),
    // These are configuration indicators, NOT upstream connectivity checks.
    upstreamConfigured: Boolean(config.jellyfinGatewayUpstreamUrl),
    userMappingConfigured: Boolean(mappingReady),
    nextUpMode: config.jellyfinGatewayFirstPageMode ?? 'short_first_page_only',
    resumeMode: configured && config.jellyfinGatewayResumePassThrough === true
      ? 'raw_passthrough' : 'off',
    identity: {
      available: mapped && canPeek,
      revision: graph.revision,
      verifiedShows: shows.length,
      aliasGroups: aliased.length,
      verifiedAliases: aliased.reduce((n, row) => n + row.aliases.length, 0),
      quarantinedConflicts: graph.conflicts.length,
    },
    productionRoutingQualified: false,
    telemetry: metrics,
    viewOnly: true,
    dashboardMutatesWatchHistory: false,
  };
}
