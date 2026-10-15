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
    viewOnly: true,
    upstreamWatchHistoryWrites: 0,
  };
}
