/**
 * Per-profile Gateway *filtering* switch.
 * Validly provisioned sources filter NextUp by default (AUTO ON), but only
 * exact same-user / safely complete responses can actually be normalized.
 * An explicit OFF remains a durable, source-scoped operator kill switch.
 * Listener provisioning and client routing are separate concerns.
 * Uses Bridge media_cache only, never watch-state/event tables.
 */
import { createHash } from 'node:crypto';

const TTL_SECONDS = 10 * 365 * 24 * 3600;

export function gatewayFilterKey(profileId, config) {
  if (!/^[a-f0-9]{24}$/.test(profileId || '')) throw new Error('invalid_gateway_profile');
  // Source URL/user/type identity is NOT disclosed in the DB key, and switching
  // AIO ↔ Remux ↔ native Jellyfin never inherits a prior ON state.
  const scope = [
    profileId,
    config?.jellyfinGatewaySourceKind || 'aiostreams',
    config?.jellyfinGatewayUpstreamUrl || '',
    config?.jellyfinGatewayUserId || '',
  ].join('\\x1f');
  const suffix = createHash('sha256').update(scope).digest('hex').slice(0, 24);
  return 'gateway-filter:v1:' + profileId + ':' + suffix;
}

function provisionedSourceScope(config, profileId) {
  return config?.jellyfinGatewayEnabled === true
    && /^[a-f0-9]{24}$/.test(profileId || '')
    && config?.jellyfinGatewayProfileId === profileId
    && /^[A-Za-z0-9_-]{8,128}$/.test(config?.jellyfinGatewayUserId || '')
    && Boolean(config?.jellyfinGatewayUpstreamUrl);
}

export function gatewayFilteringEnabled({ db, config, profileId }) {
  if (!provisionedSourceScope(config, profileId)
      || typeof db?.cachePeek !== 'function') return false;
  // No preference yet = AUTO ON. A persisted manual OFF always wins.
  // Malformed preferences fail OFF; no implicit media_cache writes are made.
  const preference = db.cachePeek(gatewayFilterKey(profileId, config));
  return preference == null || preference.enabled === true;
}

export function gatewayFilterControlAvailable({ config, gateway, profileId }) {
  return provisionedSourceScope(config, profileId)
    && gateway?.listening === true;
}

export function setGatewayFilteringEnabled({ db, config, gateway, profileId, enabled }) {
  if (typeof enabled !== 'boolean') throw new Error('gateway_toggle_invalid');
  if (!gatewayFilterControlAvailable({ config, gateway, profileId })
      || typeof db?.cacheSet !== 'function') {
    throw new Error('gateway_listener_or_source_not_configured');
  }
  // Only this namespaced preference changes, not any playback or identity.
  db.cacheSet(gatewayFilterKey(profileId, config), { enabled }, TTL_SECONDS);
  return enabled;
}
