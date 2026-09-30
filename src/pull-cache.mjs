export function makePullCacheEntry(payload, fetchedAt = Date.now()) {
  if (!payload || typeof payload.version !== 'string' || !Array.isArray(payload.items)) return null;
  return {
    version: payload.version,
    items: payload.items,
    fetchedAt,
  };
}

export function cachedPullPayload(entry, since, now, ttlSeconds) {
  if (!entry || !since || since !== entry.version) return null;
  if (!Number.isFinite(entry.fetchedAt) || now - entry.fetchedAt >= ttlSeconds * 1000) return null;
  return { version: entry.version, items: entry.items };
}

export function stalePullPayload(entry, since, now, staleSeconds) {
  if (!entry || !since || since !== entry.version) return null;
  if (!Number.isFinite(entry.fetchedAt) || now - entry.fetchedAt >= staleSeconds * 1000) return null;
  return { version: entry.version, items: entry.items };
}
