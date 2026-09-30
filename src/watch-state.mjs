import { BridgeError } from './errors.mjs';

export const PUSH_EVENTS = ['start', 'pause', 'stop', 'played', 'unplayed'];

export function buildManifest(profileId, pullTtlSeconds = 300) {
  return {
    id: `homedocker.trakt.watchstate.${profileId}`,
    version: '0.2.0',
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
        bulk: false,
      },
      pull: {
        items: true,
        watched: true,
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
  if (body.scope === 'season' || body.scope === 'series') {
    throw new BridgeError('Bulk marks are not supported by v0.2', { status: 422, code: 'bulk_not_supported' });
  }
  if (!['movie', 'episode', undefined, null].includes(body.scope)) {
    throw new BridgeError('Unsupported scope', { status: 422, code: 'unsupported_scope' });
  }
  return body;
}

export function progressPercent(event) {
  const pos = Number(event.positionMs);
  const dur = Number(event.durationMs);
  if (!Number.isFinite(pos) || !Number.isFinite(dur) || dur <= 0) return null;
  return Math.max(0, Math.min(100, (pos / dur) * 100));
}

export function planEvent(event) {
  switch (event.event) {
    case 'played':
      return { kind: 'history-add' };
    case 'unplayed':
      return { kind: 'history-remove' };
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
