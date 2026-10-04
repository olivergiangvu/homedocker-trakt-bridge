import { sha256 } from './crypto.mjs';

const PREFIX = 'history-semantic:v1:';

function canonicalMediaKey(media) {
  const kind = media?.kind;
  const traktId = Number(kind === 'movie' ? media?.movie?.ids?.trakt : media?.episode?.ids?.trakt);
  if (!['movie', 'episode'].includes(kind) || !Number.isSafeInteger(traktId) || traktId <= 0) return null;
  return `${kind}:${traktId}`;
}

function eventAtSeconds(event) {
  const at = Number(event?.at);
  return Number.isFinite(at) && at > 0 ? Math.floor(at) : Math.floor(Date.now() / 1000);
}

function markerKey(profileId, mediaKey) {
  return `${PREFIX}${profileId}:${sha256(mediaKey)}`;
}

export function rememberHistoryState(db, profileId, media, event, {
  state,
  source,
  ttlSeconds,
}) {
  const ttl = Number(ttlSeconds);
  if (!['played', 'unplayed'].includes(state) || !Number.isFinite(ttl) || ttl <= 0) return null;
  const mediaKey = canonicalMediaKey(media);
  if (!mediaKey) return null;

  const marker = {
    state,
    source: String(source || 'history'),
    eventId: typeof event?.id === 'string' ? event.id : null,
    eventAt: eventAtSeconds(event),
    mediaKey,
  };
  db.cacheSet(markerKey(profileId, mediaKey), marker, ttl);
  return marker;
}

export function recentEquivalentHistoryState(db, profileId, media, event, state, ttlSeconds) {
  const ttl = Number(ttlSeconds);
  if (!['played', 'unplayed'].includes(state) || !Number.isFinite(ttl) || ttl <= 0) return null;
  const mediaKey = canonicalMediaKey(media);
  if (!mediaKey) return null;

  const marker = db.cacheGet(markerKey(profileId, mediaKey));
  if (!marker || marker.state !== state) return null;

  const incomingAt = eventAtSeconds(event);
  const previousAt = Number(marker.eventAt);
  if (!Number.isFinite(previousAt)) return null;

  const signedDeltaSeconds = incomingAt - previousAt;
  const distanceSeconds = Math.abs(signedDeltaSeconds);
  if (distanceSeconds > ttl) return null;

  return {
    ...marker,
    incomingAt,
    signedDeltaSeconds,
    distanceSeconds,
  };
}
