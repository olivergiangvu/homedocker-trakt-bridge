import { BridgeError } from './errors.mjs';
import { providerIdsForEvent } from './media-ids.mjs';

function int(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

function hasIds(ids) {
  return Boolean(
    ids?.imdb
    || (Number.isSafeInteger(Number(ids?.tmdb)) && Number(ids.tmdb) > 0)
    || (Number.isSafeInteger(Number(ids?.tvdb)) && Number(ids.tvdb) > 0)
    || (Number.isSafeInteger(Number(ids?.trakt)) && Number(ids.trakt) > 0)
  );
}

function assertDirectIds(event) {
  const ids = providerIdsForEvent(event);
  if (!hasIds(ids)) {
    throw new BridgeError('No Trakt-compatible provider ID is available', {
      status: 422,
      code: 'media_id_missing',
    });
  }
  return ids;
}

function assertEpisodeNumbering(event) {
  const videoId = String(event?.videoId || '');
  if (/^(kitsu|mal|anilist|anidb):/i.test(videoId)) {
    throw new BridgeError('Anime/absolute episode numbering is not mapped safely in v1.2', {
      status: 422,
      code: 'anime_numbering_unsupported',
    });
  }

  const season = int(event?.season);
  const episode = int(event?.episode);
  if (season == null || season < 0 || episode == null || episode <= 0) {
    throw new BridgeError('Episode event lacks season/episode', {
      status: 422,
      code: 'episode_number_missing',
    });
  }
  return { season, episode };
}

export function directMediaTarget(event = {}) {
  const ids = assertDirectIds(event);
  const episodeLike = event.scope === 'episode'
    || Number.isInteger(Number(event.season))
    || Number.isInteger(Number(event.episode));

  if (episodeLike) {
    const { season, episode } = assertEpisodeNumbering(event);
    return {
      kind: 'episode',
      ids,
      season,
      episode,
      scrobbleItem: {
        show: { ids },
        episode: { season, number: episode },
      },
    };
  }

  return {
    kind: 'movie',
    ids,
    scrobbleItem: { movie: { ids } },
  };
}

export function directScrobblePayload(event, progress) {
  const target = directMediaTarget(event);
  return {
    target,
    body: {
      ...target.scrobbleItem,
      progress: Number(progress),
    },
  };
}

export function directHistoryPayload(event, remove = false) {
  const target = directMediaTarget(event);
  const watchedAt = new Date(
    (Number(event?.at) || Math.floor(Date.now() / 1000)) * 1000,
  ).toISOString();

  if (target.kind === 'movie') {
    return {
      target,
      body: {
        movies: [{
          ids: target.ids,
          ...(remove ? {} : { watched_at: watchedAt }),
        }],
      },
    };
  }

  return {
    target,
    body: {
      shows: [{
        ids: target.ids,
        seasons: [{
          number: target.season,
          episodes: [{
            number: target.episode,
            ...(remove ? {} : { watched_at: watchedAt }),
          }],
        }],
      }],
    },
  };
}

export function directWatchlistPayload(event) {
  const ids = assertDirectIds(event);
  if (event?.scope === 'series') {
    return { kind: 'show', body: { shows: [{ ids }] }, ids };
  }
  return { kind: 'movie', body: { movies: [{ ids }] }, ids };
}

export function directBulkHistoryPayload(event, add) {
  const ids = assertDirectIds(event);
  const watchedAt = new Date(
    (Number(event?.at) || Math.floor(Date.now() / 1000)) * 1000,
  ).toISOString();
  const grouped = new Map();

  for (const video of event?.videos || []) {
    const videoId = String(video?.videoId || '');
    if (/^(kitsu|mal|anilist|anidb):/i.test(videoId)) {
      throw new BridgeError('Anime/absolute episode numbering is not mapped safely in v1.2', {
        status: 422,
        code: 'anime_numbering_unsupported',
      });
    }
    const season = int(video?.season);
    const episode = int(video?.episode);
    if (season == null || season < 0 || episode == null || episode <= 0) {
      throw new BridgeError('Bulk history contains invalid season/episode numbering', {
        status: 422,
        code: 'bulk_video_invalid',
      });
    }
    if (!grouped.has(season)) grouped.set(season, new Set());
    grouped.get(season).add(episode);
  }

  const seasons = [...grouped.entries()]
    .sort(([a], [b]) => a - b)
    .map(([number, episodes]) => ({
      number,
      episodes: [...episodes]
        .sort((a, b) => a - b)
        .map((episode) => add
          ? { number: episode, watched_at: watchedAt }
          : { number: episode }),
    }));

  return {
    body: { shows: [{ ids, seasons }] },
    ids,
  };
}

/**
 * Stable AIOStreams-side semantic identity. This deliberately does not require
 * a Trakt metadata lookup: every retry/echo for the same AIO item carries the
 * same videoId in the normal Jellyfin watch-state path.
 */
export function sourceSemanticMediaKey(event = {}) {
  const scope = String(event.scope || '').toLowerCase();
  const episodeLike = scope === 'episode'
    || Number.isInteger(Number(event.season))
    || Number.isInteger(Number(event.episode));
  const videoId = String(event.videoId || '').trim();
  if (videoId) {
    return `${episodeLike ? 'episode' : 'movie'}:video:${videoId}`;
  }

  const metaId = String(event.metaId || '').trim();
  if (!metaId) return null;

  if (episodeLike) {
    const season = int(event.season);
    const episode = int(event.episode);
    if (season == null || episode == null) return null;
    return `episode:meta:${metaId}:${season}:${episode}`;
  }

  return `movie:meta:${metaId}`;
}

export function mediaFromScrobbleResponse(target, response) {
  if (target?.kind === 'movie' && response?.movie?.ids?.trakt) {
    return {
      kind: 'movie',
      movie: response.movie,
    };
  }

  if (
    target?.kind === 'episode'
    && response?.episode?.ids?.trakt
  ) {
    return {
      kind: 'episode',
      episode: response.episode,
      show: response.show || { ids: {} },
    };
  }

  return null;
}
