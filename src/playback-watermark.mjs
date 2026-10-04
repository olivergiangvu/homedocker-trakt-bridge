const PREFIX = 'playback-watermark:v1:';
const DEFAULT_TTL_SECONDS = 24 * 60 * 60;

function canonicalTraktId(media) {
  if (media?.kind === 'movie') return Number(media.movie?.ids?.trakt) || null;
  if (media?.kind === 'episode') return Number(media.episode?.ids?.trakt) || null;
  return null;
}

function eventAtSeconds(event) {
  const raw = Number(event?.at);
  if (!Number.isFinite(raw) || raw <= 0) return null;
  return raw > 1e11 ? Math.floor(raw / 1000) : Math.floor(raw);
}

function keyFor(profileId, media) {
  const traktId = canonicalTraktId(media);
  if (!traktId) return null;
  return `${PREFIX}${profileId}:${media.kind}:${traktId}`;
}

export function observePlaybackWatermark(
  db,
  profileId,
  media,
  event,
  ttlSeconds = DEFAULT_TTL_SECONDS,
) {
  if (!['start', 'pause', 'stop'].includes(String(event?.event || ''))) return null;

  const key = keyFor(profileId, media);
  const incomingAt = eventAtSeconds(event);
  if (!key || !incomingAt) return null;

  const existing = db.cacheGet(key);
  const newestAt = Number(existing?.at || 0);

  if (newestAt > incomingAt) {
    return {
      stale: true,
      incomingAt,
      newestAt,
      newestEventId: existing?.eventId || null,
      newestEvent: existing?.event || null,
      deltaSeconds: newestAt - incomingAt,
    };
  }

  // Equal timestamps are intentionally allowed. AIOStreams' protocol timestamp
  // resolution is one second and legitimate start/stop edges can share a value.
  if (incomingAt > newestAt) {
    db.cacheSet(key, {
      at: incomingAt,
      eventId: String(event.id || ''),
      event: String(event.event || ''),
    }, ttlSeconds);
  }

  return {
    stale: false,
    incomingAt,
    newestAt: Math.max(newestAt, incomingAt),
  };
}
