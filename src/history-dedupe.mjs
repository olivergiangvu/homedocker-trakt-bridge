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

function validMediaKey(value) {
  const key = typeof value === 'string' ? value.trim() : '';
  return key || null;
}

export function rememberHistoryStateByKey(db, profileId, mediaKey, event, {
  state,
  source,
  ttlSeconds,
}) {
  const ttl = Number(ttlSeconds);
  const key = validMediaKey(mediaKey);
  if (!key || !['played', 'unplayed'].includes(state) || !Number.isFinite(ttl) || ttl <= 0) return null;

  const marker = {
    state,
    source: String(source || 'history'),
    eventId: typeof event?.id === 'string' ? event.id : null,
    eventAt: eventAtSeconds(event),
    mediaKey: key,
  };
  db.cacheSet(markerKey(profileId, key), marker, ttl);
  return marker;
}

export function recentEquivalentHistoryStateByKey(
  db,
  profileId,
  mediaKey,
  event,
  state,
  ttlSeconds,
) {
  const ttl = Number(ttlSeconds);
  const key = validMediaKey(mediaKey);
  if (!key || !['played', 'unplayed'].includes(state) || !Number.isFinite(ttl) || ttl <= 0) return null;

  const marker = db.cacheGet(markerKey(profileId, key));
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

export function rememberHistoryState(db, profileId, media, event, options) {
  const mediaKey = canonicalMediaKey(media);
  return rememberHistoryStateByKey(db, profileId, mediaKey, event, options);
}

export function recentEquivalentHistoryState(db, profileId, media, event, state, ttlSeconds) {
  const mediaKey = canonicalMediaKey(media);
  return recentEquivalentHistoryStateByKey(
    db,
    profileId,
    mediaKey,
    event,
    state,
    ttlSeconds,
  );
}
