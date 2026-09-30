import { APP_VERSION } from './config.mjs';
import { BridgeError } from './errors.mjs';

export const PUSH_EVENTS = ['start', 'pause', 'stop', 'played', 'unplayed', 'watchlisted', 'unwatchlisted'];

export function buildManifest(profileId, pullTtlSeconds = 300) {
  return {
    id: `homedocker.trakt.watchstate.${profileId}`,
    version: APP_VERSION,
    name: 'HomeDocker Trakt',
    description: 'Bidirectional AIOStreams watch_state v2 ↔ Trakt bridge',
    types: ['movie', 'series'],
    resources: [
      {
        name: 'watch_state',
        types: ['movie', 'series'],
        idPrefixes: ['tt', 'tmdb:', 'tvdb:'],
      },
    ],
    watchState: {
      version: 2,
      push: {
        events: PUSH_EVENTS,
        bulk: true,
      },
      pull: {
        items: true,
        watched: true,
        watchlist: true,
        ttlSeconds: pullTtlSeconds,
      },
    },
  };
}

export function validatePushEvent(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new BridgeError('JSON object required', { status: 400, code: 'invalid_body' });
  }
  if (typeof body.id !== 'string' || body.id.length < 3 || body.id.length > 512) {
    throw new BridgeError('Missing or invalid event id', { status: 400, code: 'invalid_event_id' });
  }
  if (!PUSH_EVENTS.includes(body.event)) {
    throw new BridgeError('Unsupported event', { status: 422, code: 'unsupported_event' });
  }

  const isWatchlist = body.event === 'watchlisted' || body.event === 'unwatchlisted';
  if (isWatchlist) {
    if (!['movie', 'series'].includes(body.scope)) {
      throw new BridgeError('Watchlist events require movie or series scope', { status: 422, code: 'watchlist_scope_invalid' });
    }
    return body;
  }

  const isBulk = body.scope === 'season' || body.scope === 'series';
  if (isBulk) {
    validateBulkMark(body);
    return body;
  }

  if (!['movie', 'episode', undefined, null].includes(body.scope)) {
    throw new BridgeError('Unsupported scope', { status: 422, code: 'unsupported_scope' });
  }
  return body;
}

function validateBulkMark(body) {
  if (body.event !== 'played' && body.event !== 'unplayed') {
    throw new BridgeError('Bulk marks only support played or unplayed', { status: 422, code: 'bulk_event_invalid' });
  }
  if (typeof body.metaId !== 'string' || body.metaId.length < 2 || body.metaId.length > 512) {
    throw new BridgeError('Bulk mark requires metaId', { status: 422, code: 'bulk_meta_invalid' });
  }
  if (!Array.isArray(body.videos) || body.videos.length < 1 || body.videos.length > 500) {
    throw new BridgeError('Bulk mark videos must contain 1 to 500 entries', { status: 422, code: 'bulk_videos_invalid' });
  }

  const season = body.scope === 'season' ? nonNegativeInt(body.season) : null;
  if (body.scope === 'season' && season == null) {
    throw new BridgeError('Season bulk mark requires a valid season number', { status: 422, code: 'bulk_season_invalid' });
  }

  for (const video of body.videos) {
    if (!video || typeof video !== 'object' || Array.isArray(video)) {
      throw new BridgeError('Bulk mark contains an invalid video', { status: 422, code: 'bulk_video_invalid' });
    }
    if (typeof video.videoId !== 'string' || video.videoId.length < 3 || video.videoId.length > 512) {
      throw new BridgeError('Bulk mark videoId is invalid', { status: 422, code: 'bulk_video_id_invalid' });
    }
    const videoSeason = nonNegativeInt(video.season);
    const videoEpisode = nonNegativeInt(video.episode);
    if (videoSeason == null || videoEpisode == null) {
      throw new BridgeError('Bulk mark video lacks season/episode', { status: 422, code: 'bulk_video_number_invalid' });
    }
    if (season != null && videoSeason !== season) {
      throw new BridgeError('Bulk season mark contains a video from another season', { status: 422, code: 'bulk_video_season_mismatch' });
    }
  }

  const part = body.part == null ? 1 : positiveInt(body.part);
  const parts = body.parts == null ? 1 : positiveInt(body.parts);
  if (part == null || parts == null || part > parts) {
    throw new BridgeError('Bulk mark part metadata is invalid', { status: 422, code: 'bulk_parts_invalid' });
  }
}

function nonNegativeInt(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 0 ? n : null;
}

function positiveInt(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 1 ? n : null;
}

export function progressPercent(event) {
  const pos = Number(event.positionMs);
  const dur = Number(event.durationMs);
  if (!Number.isFinite(pos) || !Number.isFinite(dur) || dur <= 0) return null;
  return Math.max(0, Math.min(100, (pos / dur) * 100));
}

export function planEvent(event) {
  if ((event.scope === 'season' || event.scope === 'series') && event.event === 'played') {
    return { kind: 'bulk-history-add' };
  }
  if ((event.scope === 'season' || event.scope === 'series') && event.event === 'unplayed') {
    return { kind: 'bulk-history-remove' };
  }

  switch (event.event) {
    case 'played':
      return { kind: 'history-add' };
    case 'unplayed':
      return { kind: 'history-remove' };
    case 'watchlisted':
      return { kind: 'watchlist-add' };
    case 'unwatchlisted':
      return { kind: 'watchlist-remove' };
    case 'start': {
      const progress = progressPercent(event);
      if (progress == null) return { kind: 'ignore', reason: 'duration_unknown' };
      return { kind: 'scrobble', action: 'start', progress };
    }
    case 'pause': {
      const progress = progressPercent(event);
      if (progress == null) return { kind: 'ignore', reason: 'duration_unknown' };
      return { kind: 'scrobble', action: 'pause', progress };
    }
    case 'stop': {
      const progress = progressPercent(event);
      if (progress == null) {
        return event.played === true
          ? { kind: 'history-add' }
          : { kind: 'ignore', reason: 'duration_unknown' };
      }
      if (event.played !== true) return { kind: 'scrobble', action: 'pause', progress };
      return { kind: 'scrobble', action: 'stop', progress };
    }
    default:
      throw new BridgeError('Unsupported event', { status: 422, code: 'unsupported_event' });
  }
}

export function isoFromUnixSeconds(value) {
  const seconds = Number(value);
  const date = Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000) : new Date();
  return date.toISOString();
}
