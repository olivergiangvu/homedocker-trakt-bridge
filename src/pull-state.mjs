import { createHash } from 'node:crypto';
import { preferredMetaId, representableMetaIds } from './media-ids.mjs';

export { preferredMetaId } from './media-ids.mjs';

function int(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

export function unixSeconds(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e11 ? Math.floor(value / 1000) : Math.floor(value);
  }
  const ms = Date.parse(String(value || ''));
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : undefined;
}

function runtimeMs(media) {
  const runtime = Number(media?.runtime);
  return Number.isFinite(runtime) && runtime > 0 ? Math.round(runtime * 60_000) : undefined;
}

function progress(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, n));
}

function playbackItem({ type, metaId, videoId, season, episode, progressPercent, pausedAt, durationMs }) {
  if (!metaId || !videoId || progressPercent == null || progressPercent <= 0 || progressPercent >= 100) return null;
  const out = {
    type,
    metaId,
    videoId,
    progressPercent: Number(progressPercent.toFixed(3)),
    played: false,
  };
  const at = unixSeconds(pausedAt);
  if (at != null) out.at = at;
  if (durationMs && durationMs > 0) {
    out.durationMs = durationMs;
    out.positionMs = Math.round((durationMs * progressPercent) / 100);
  }
  if (season != null) out.season = season;
  if (episode != null) out.episode = episode;
  return out;
}

export function buildPlaybackItems(movieRows = [], episodeRows = []) {
  const byVideo = new Map();

  for (const row of movieRows) {
    const movie = row?.movie || {};
    const metaId = preferredMetaId(movie.ids);
    const p = progress(row?.progress);
    const item = playbackItem({
      type: 'movie',
      metaId,
      videoId: metaId,
      progressPercent: p,
      pausedAt: row?.paused_at,
      durationMs: runtimeMs(movie),
    });
    if (item) byVideo.set(item.videoId, item);
  }

  for (const row of episodeRows) {
    const show = row?.show || {};
    const ep = row?.episode || {};
    const metaId = preferredMetaId(show.ids);
    const season = int(ep.season);
    const episode = int(ep.number);
    const p = progress(row?.progress);
    const videoId = metaId && season != null && episode != null ? `${metaId}:${season}:${episode}` : null;
    const item = playbackItem({
      type: 'series',
      metaId,
      videoId,
      season,
      episode,
      progressPercent: p,
      pausedAt: row?.paused_at,
      durationMs: runtimeMs(ep),
    });
    if (item) byVideo.set(item.videoId, item);
  }

  return [...byVideo.values()].sort((a, b) => (b.at || 0) - (a.at || 0));
}

export function stateVersionFromActivities(activities = {}, identityVersion = '') {
  const basis = JSON.stringify({
    schema: 'watch-state-v0.3.6',
    identityVersion: identityVersion || '',
    watchedMovies: activities?.movies?.watched_at || null,
    watchedEpisodes: activities?.episodes?.watched_at || null,
    watchlistMovies: activities?.movies?.watchlisted_at || null,
    watchlistShows: activities?.shows?.watchlisted_at || null,
  });
  return createHash('sha256').update(basis).digest('hex').slice(0, 16);
}

// Kept as a compatibility export for older local tests/tools; v0.3 uses the
// broader state version so one AIOStreams `since` cursor covers watched + watchlist.
export function watchedVersionFromActivities(activities = {}) {
  return stateVersionFromActivities(activities);
}

/**
 * Some Trakt progress-shaped rows include next_episode. Use it when present,
 * but never synthesize a guessed next episode or fan out to one API call/show.
 */
function nextUpFromWatchedRow(row, metaId) {
  const next = row?.next_episode;
  const season = int(next?.season);
  const episode = int(next?.number);
  if (!metaId || season == null || episode == null) return null;
  const out = {
    type: 'series',
    metaId,
    videoId: `${metaId}:${season}:${episode}`,
    season,
    episode,
  };
  const at = unixSeconds(row?.last_watched_at);
  if (at != null) out.at = at;
  return out;
}

export function buildWatchedState(movieRows = [], showRows = []) {
  const movies = new Set();
  const episodes = new Set();
  const counts = {};
  const nextUp = [];

  for (const row of movieRows) {
    const metaId = preferredMetaId(row?.movie?.ids);
    if (metaId) movies.add(metaId);
  }

  for (const row of showRows) {
    const show = row?.show || {};
    const aliases = representableMetaIds(show.ids);
    const metaId = aliases[0] || null;
    if (!metaId) continue;
    if (!Array.isArray(row?.seasons)) {
      throw new Error(`Incomplete Trakt watched payload for show ${metaId}: seasons missing`);
    }

    let watched = 0;
    for (const seasonRow of row.seasons) {
      const season = int(seasonRow?.number);
      if (season == null || !Array.isArray(seasonRow?.episodes)) continue;
      for (const ep of seasonRow.episodes) {
        const episode = int(ep?.number);
        if (episode == null) continue;
        if (Number(ep?.plays || 0) <= 0 && !ep?.last_watched_at) continue;
        episodes.add(`${metaId}:${season}:${episode}`);
        watched += 1;
      }
    }

    const count = {
      watched,
      total: int(show?.aired_episodes) ?? 0,
    };
    const at = unixSeconds(row?.last_watched_at);
    if (at != null) count.at = at;

    // AIOStreams explicitly accepts counts under every spelling a show answers
    // to. This lets imported history join whichever ID space metadata uses.
    for (const alias of aliases) counts[alias] = { ...count };

    const next = nextUpFromWatchedRow(row, metaId);
    if (next) nextUp.push(next);
  }

  const out = {
    movies: [...movies],
    episodes: [...episodes],
    counts,
  };
  if (nextUp.length) out.nextUp = nextUp.sort((a, b) => (b.at || 0) - (a.at || 0));
  return out;
}

export function buildWatchlistState(movieRows = [], showRows = []) {
  const byItem = new Map();

  for (const row of movieRows) {
    const metaId = preferredMetaId(row?.movie?.ids);
    if (!metaId) continue;
    const item = { type: 'movie', metaId };
    const at = unixSeconds(row?.listed_at ?? row?.watchlisted_at);
    if (at != null) item.at = at;
    byItem.set(`movie:${metaId}`, item);
  }

  for (const row of showRows) {
    const metaId = preferredMetaId(row?.show?.ids);
    if (!metaId) continue;
    const item = { type: 'series', metaId };
    const at = unixSeconds(row?.listed_at ?? row?.watchlisted_at);
    if (at != null) item.at = at;
    byItem.set(`series:${metaId}`, item);
  }

  return [...byItem.values()].sort((a, b) => (b.at || 0) - (a.at || 0));
}

export function includeChangedStateForSince(since, version) {
  return !since || since !== version;
}

export function includeWatchedForSince(since, version) {
  return includeChangedStateForSince(since, version);
}
