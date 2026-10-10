import { APP_VERSION } from './config.mjs';
import { BridgeError } from './errors.mjs';

export const PUSH_EVENTS = ['start', 'pause', 'stop', 'played', 'unplayed', 'watchlisted', 'unwatchlisted'];
const TRAKT_MIN_SCROBBLE_PROGRESS = 1;
// Empirical Trakt response for pause at 99.13% and 99.982%: HTTP 422,
// "Use stop to scrobble". Never turn a pause into watched without AIO proof.
const TRAKT_TERMINAL_PAUSE_MIN_PROGRESS = 99;

export function buildManifest(profileId, pullTtlSeconds = 300) {
  return {
    id: `homedocker.trakt.watchstate.${profileId}`,
    version: APP_VERSION,
    name: 'HomeDocker Trakt',
    description: 'Bidirectional AIOStreams watch_state v2 ↔ Trakt bridge',
    types: ['movie', 'series'],
    resources: [{ name: 'watch_state', types: ['movie', 'series'], idPrefixes: ['tt', 'tmdb:', 'tvdb:'] }],
    watchState: {
      version: 2,
      push: { events: PUSH_EVENTS, bulk: true },
      pull: { items: true, watched: true, watchlist: true, ttlSeconds: pullTtlSeconds },
    },
  };
}

function integer(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

function validateBulkMark(body) {
  if (!['played', 'unplayed'].includes(body.event)) throw new BridgeError('Bulk scope is only valid for played or unplayed', { status: 422, code: 'bulk_event_invalid' });
  if (typeof body.metaId !== 'string' || !body.metaId || body.metaId.length > 512) throw new BridgeError('Bulk mark requires a valid metaId', { status: 422, code: 'bulk_meta_invalid' });
  if (!Array.isArray(body.videos) || body.videos.length < 1 || body.videos.length > 500) throw new BridgeError('Bulk mark requires 1 to 500 videos', { status: 422, code: 'bulk_videos_invalid' });
  const part = integer(body.part);
  const parts = integer(body.parts);
  if (part == null || parts == null || part < 1 || parts < 1 || part > parts) throw new BridgeError('Bulk mark has invalid part metadata', { status: 422, code: 'bulk_part_invalid' });
  const seasonScope = body.scope === 'season' ? integer(body.season) : null;
  if (body.scope === 'season' && (seasonScope == null || seasonScope < 0)) throw new BridgeError('Season bulk mark requires a valid season', { status: 422, code: 'bulk_season_invalid' });
  for (const video of body.videos) {
    const season = integer(video?.season);
    const episode = integer(video?.episode);
    if (!video || typeof video.videoId !== 'string' || !video.videoId || video.videoId.length > 512 || season == null || episode == null || season < 0 || episode < 0) throw new BridgeError('Bulk mark contains an invalid video', { status: 422, code: 'bulk_video_invalid' });
    if (body.scope === 'season' && season !== seasonScope) throw new BridgeError('Bulk season contains a video from another season', { status: 422, code: 'bulk_season_mismatch' });
  }
}

export function validatePushEvent(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new BridgeError('JSON object required', { status: 400, code: 'invalid_body' });
  if (typeof body.id !== 'string' || body.id.length < 3 || body.id.length > 512) throw new BridgeError('Missing or invalid event id', { status: 400, code: 'invalid_event_id' });
  if (!PUSH_EVENTS.includes(body.event)) throw new BridgeError('Unsupported event', { status: 422, code: 'unsupported_event' });
  const isWatchlist = body.event === 'watchlisted' || body.event === 'unwatchlisted';
  if (isWatchlist) {
    if (!['movie', 'series'].includes(body.scope)) throw new BridgeError('Watchlist events require movie or series scope', { status: 422, code: 'watchlist_scope_invalid' });
    return body;
  }
  if (body.scope === 'season' || body.scope === 'series') {
    validateBulkMark(body);
    return body;
  }
  if (!['movie', 'episode', undefined, null].includes(body.scope)) throw new BridgeError('Unsupported scope', { status: 422, code: 'unsupported_scope' });
  return body;
}

export function progressPercent(event) {
  const pos = Number(event.positionMs);
  const dur = Number(event.durationMs);
  if (!Number.isFinite(pos) || !Number.isFinite(dur) || dur <= 0) return null;
  return Math.max(0, Math.min(100, (pos / dur) * 100));
}

function belowTraktScrobbleMinimum(progress) {
  return progress < TRAKT_MIN_SCROBBLE_PROGRESS;
}

export function planEvent(event) {
  const bulk = event.scope === 'season' || event.scope === 'series';
  switch (event.event) {
    case 'played': return { kind: bulk ? 'bulk-history-add' : 'history-add' };
    case 'unplayed': return { kind: bulk ? 'bulk-history-remove' : 'history-remove' };
    case 'watchlisted': return { kind: 'watchlist-add' };
    case 'unwatchlisted': return { kind: 'watchlist-remove' };
    case 'start': {
      const progress = progressPercent(event);
      if (progress == null) return { kind: 'ignore', reason: 'duration_unknown' };
      if (belowTraktScrobbleMinimum(progress)) return { kind: 'ignore', reason: 'progress_below_trakt_minimum' };
      return { kind: 'scrobble', action: 'start', progress };
    }
    case 'pause': {
      const progress = progressPercent(event);
      if (progress == null) return { kind: 'ignore', reason: 'duration_unknown' };
      if (belowTraktScrobbleMinimum(progress)) return { kind: 'ignore', reason: 'progress_below_trakt_minimum' };
      // Trakt rejects a near-complete pause with 422 and asks for stop.
      // Wait for an independently confirmed AIO played=true stop/mark; do not
      // manufacture a watched event merely from a pause's progress.
      if (progress >= TRAKT_TERMINAL_PAUSE_MIN_PROGRESS) {
        return { kind: 'ignore', reason: 'terminal_pause_requires_completion' };
      }
      return { kind: 'scrobble', action: 'pause', progress };
    }
    case 'stop': {
      const progress = progressPercent(event);
      if (progress == null) return event.played === true ? { kind: 'history-add' } : { kind: 'ignore', reason: 'duration_unknown' };
      if (belowTraktScrobbleMinimum(progress)) return event.played === true ? { kind: 'history-add' } : { kind: 'ignore', reason: 'progress_below_trakt_minimum' };

      // Preserve the client's stop semantic where Trakt will also classify it
      // as resumable. Trakt completes a /scrobble/stop at >=80%, while AIOStreams
      // defaults to a 90% played threshold. For the 80-89% gap, trust AIO's
      // played=false decision and send pause so we never mark an unfinished item
      // watched merely because Trakt uses a lower completion threshold.
      if (event.played !== true && progress >= 80) {
        // Same near-terminal 422 risk, but AIO explicitly did NOT confirm
        // watched. Suppress rather than crossing Trakt's 80% watched boundary.
        if (progress >= TRAKT_TERMINAL_PAUSE_MIN_PROGRESS) {
          return { kind: 'ignore', reason: 'terminal_stop_not_confirmed_played' };
        }
        return { kind: 'scrobble', action: 'pause', progress };
      }
      return { kind: 'scrobble', action: 'stop', progress };
    }
    default: throw new BridgeError('Unsupported event', { status: 422, code: 'unsupported_event' });
  }
}

export function isoFromUnixSeconds(value) {
  const seconds = Number(value);
  const date = Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000) : new Date();
  return date.toISOString();
}
