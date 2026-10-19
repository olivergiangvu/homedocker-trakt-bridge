/**
 * Per-profile Gateway *filtering* switch.
 * Listener provisioning and client routing are separate concerns: OFF means
 * authenticated Jellyfin NextUp returns the upstream response unchanged.
 * Uses the existing Bridge media_cache table, never watch-state/event tables.
 * Durable across Bridge restarts without schema migrations.
 */
const TTL_SECONDS = 10 * 365 * 24 * 3600;

export function gatewayFilterKey(profileId) {
  if (!/^[a-f0-9]{24}$/.test(profileId || '')) throw new Error('invalid_gateway_profile');
  return 'gateway-filter:v1:' + profileId;
}

export function gatewayFilteringEnabled({ db, config, profileId }) {
  if (config?.jellyfinGatewayEnabled !== true
      || config?.jellyfinGatewayProfileId !== profileId
      || typeof db?.cachePeek !== 'function') return false;
  return db.cachePeek(gatewayFilterKey(profileId))?.enabled === true;
}

export function gatewayFilterControlAvailable({ config, gateway, profileId }) {
  return config?.jellyfinGatewayEnabled === true
    && config?.jellyfinGatewayProfileId === profileId
    && /^[A-Za-z0-9_-]{8,128}$/.test(config?.jellyfinGatewayUserId || '')
    && Boolean(config?.jellyfinGatewayUpstreamUrl)
    && gateway?.listening === true;
}

export function setGatewayFilteringEnabled({ db, config, gateway, profileId, enabled }) {
  if (typeof enabled !== 'boolean') throw new Error('gateway_toggle_invalid');
  if (!gatewayFilterControlAvailable({ config, gateway, profileId })
      || typeof db?.cacheSet !== 'function') {
    throw new Error('gateway_listener_or_source_not_configured');
  }
  // Only this namespaced preference changes, not any playback or identity.
  db.cacheSet(gatewayFilterKey(profileId), { enabled }, TTL_SECONDS);
  return enabled;
}
