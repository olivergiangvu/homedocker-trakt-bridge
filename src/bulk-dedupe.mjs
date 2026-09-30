import { sha256 } from './crypto.mjs';

const PREFIX = 'bulk-single:v1:';

function integer(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

function singleVideoId(event) {
  if (typeof event?.videoId === 'string' && event.videoId) return event.videoId;
  const season = integer(event?.season);
  const episode = integer(event?.episode);
  if (typeof event?.metaId === 'string' && event.metaId && season != null && episode != null) {
    return `${event.metaId}:${season}:${episode}`;
  }
  return null;
}

function markerKey(profileId, kind, videoId) {
  return `${PREFIX}${profileId}:${kind}:${sha256(videoId)}`;
}

export function rememberBulkCoverage(db, profileId, event, ttlSeconds) {
  if (!['played', 'unplayed'].includes(event?.event) || !Array.isArray(event?.videos)) return 0;
  const bulkAt = Number(event.at);
  let stored = 0;
  for (const video of event.videos) {
    const videoId = typeof video?.videoId === 'string' && video.videoId ? video.videoId : null;
    if (!videoId) continue;
    db.cacheSet(markerKey(profileId, event.event, videoId), {
      bulkEventId: event.id,
      bulkAt: Number.isFinite(bulkAt) ? bulkAt : null,
      videoId,
      scope: event.scope,
      part: event.part,
      parts: event.parts,
    }, ttlSeconds);
    stored += 1;
  }
  return stored;
}

export function coveredByRecentBulk(db, profileId, event, ttlSeconds) {
  if (!['played', 'unplayed'].includes(event?.event)) return null;
  if (event?.scope === 'season' || event?.scope === 'series' || Array.isArray(event?.videos)) return null;
  const season = integer(event?.season);
  const episode = integer(event?.episode);
  if (event?.scope !== 'episode' && (season == null || episode == null)) return null;

  const videoId = singleVideoId(event);
  if (!videoId) return null;
  const marker = db.cacheGet(markerKey(profileId, event.event, videoId));
  if (!marker) return null;

  const singleAt = Number(event.at);
  const bulkAt = Number(marker.bulkAt);
  if (!Number.isFinite(singleAt) || !Number.isFinite(bulkAt)) return null;
  const deltaSeconds = singleAt - bulkAt;
  if (deltaSeconds < 0 || deltaSeconds > ttlSeconds) return null;
  return { ...marker, videoId, deltaSeconds };
}
