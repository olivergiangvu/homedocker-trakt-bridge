import { BridgeError } from './errors.mjs';
import { providerIdsForEvent } from './media-ids.mjs';
import {
  identityAliasVersion,
  learnShowAlias,
  loadIdentityAliases,
  rowsForPullIdentity,
} from './identity-alias.mjs';
import {
  buildPlaybackItems,
  buildWatchedState,
  buildWatchlistState,
  includeChangedStateForSince,
  stateVersionFromActivities,
} from './pull-state.mjs';

const AUTH_BASE = 'https://auth.trakt.tv';
const API_BASE = 'https://api.trakt.tv';

function toInt(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

function withQuery(path, values) {
  const url = new URL(path, API_BASE);
  for (const [key, value] of Object.entries(values)) {
    if (value != null) url.searchParams.set(key, String(value));
  }
  return `${url.pathname}${url.search}`;
}

export class TraktClient {
  constructor(config, db) {
    this.config = config;
    this.db = db;
    this.refreshing = new Map();
  }

  apiHeaders(accessToken = null) {
    const headers = {
      'Content-Type': 'application/json',
      'trakt-api-key': this.config.traktClientId,
      'trakt-api-version': '2',
      'User-Agent': this.config.userAgent,
      'Accept': 'application/json',
    };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return headers;
  }

  async exchangeCode(code) {
    const response = await fetch(`${AUTH_BASE}/oauth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'User-Agent': this.config.userAgent },
      body: JSON.stringify({
        code,
        client_id: this.config.traktClientId,
        client_secret: this.config.traktClientSecret,
        redirect_uri: this.config.redirectUri,
        grant_type: 'authorization_code',
      }),
      signal: AbortSignal.timeout(10000),
    });
    const body = await parseJsonSafe(response);
    if (!response.ok || !body?.access_token || !body?.refresh_token) {
      throw new BridgeError(`Trakt OAuth exchange failed (${response.status})`, {
        status: response.status === 429 ? 429 : 502,
        retryAfter: response.headers.get('retry-after'),
        code: 'oauth_exchange_failed',
        upstreamPath: '/oauth/token',
      });
    }
    return body;
  }

  async refresh(profileId, force = false) {
    if (this.refreshing.has(profileId)) return this.refreshing.get(profileId);
    const promise = this.#refreshInner(profileId, force).finally(() => this.refreshing.delete(profileId));
    this.refreshing.set(profileId, promise);
    return promise;
  }

  async #refreshInner(profileId, force) {
    const tokens = this.db.getTokens(profileId);
    if (!tokens?.refreshToken) throw new BridgeError('Trakt account is not connected', { status: 401, code: 'not_connected' });
    const now = Math.floor(Date.now() / 1000);
    if (!force && tokens.expiresAt > now + 300) return tokens.accessToken;

    const response = await fetch(`${AUTH_BASE}/oauth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'User-Agent': this.config.userAgent },
      body: JSON.stringify({
        refresh_token: tokens.refreshToken,
        client_id: this.config.traktClientId,
        client_secret: this.config.traktClientSecret,
        redirect_uri: this.config.redirectUri,
        grant_type: 'refresh_token',
      }),
      signal: AbortSignal.timeout(3500),
    });
    const body = await parseJsonSafe(response);
    if (!response.ok || !body?.access_token || !body?.refresh_token) {
      const invalidGrant = body?.error === 'invalid_grant';
      throw new BridgeError(invalidGrant ? 'Trakt authorization must be reconnected' : `Trakt token refresh failed (${response.status})`, {
        status: invalidGrant ? 401 : (response.status === 429 ? 429 : 502),
        retryAfter: response.headers.get('retry-after'),
        code: invalidGrant ? 'reconnect_required' : 'token_refresh_failed',
        upstreamPath: '/oauth/token',
      });
    }
    this.db.setTokens(profileId, body);
    return this.db.getTokens(profileId).accessToken;
  }

  async request(profileId, path, { method = 'GET', body = null, accept409 = false } = {}) {
    const { data } = await this.requestDetailed(profileId, path, { method, body, accept409 });
    return data;
  }

  async requestDetailed(profileId, path, { method = 'GET', body = null, accept409 = false } = {}) {
    let token = await this.refresh(profileId, false);
    let response = await this.#fetchApi(path, method, body, token);
    if (response.status === 401) {
      token = await this.refresh(profileId, true);
      response = await this.#fetchApi(path, method, body, token);
    }
    const data = await this.#handleResponse(response, { accept409, upstreamPath: path });
    return { data, headers: response.headers };
  }

  async requestAllPages(profileId, path, { limit = 100, maxPages = this.config.pullMaxPages } = {}) {
    const out = [];
    let page = 1;
    let pageCount = 1;

    do {
      if (page > maxPages) {
        throw new BridgeError('Trakt pagination exceeded configured safety cap', {
          status: 502,
          code: 'trakt_pagination_limit',
          upstreamPath: path,
        });
      }
      const pagePath = withQuery(path, { page, limit });
      const { data, headers } = await this.requestDetailed(profileId, pagePath);
      if (!Array.isArray(data)) {
        throw new BridgeError('Trakt paginated endpoint returned a non-array response', {
          status: 502,
          code: 'trakt_invalid_page',
          upstreamPath: pagePath,
        });
      }
      out.push(...data);

      const headerPageCount = Number(headers.get('x-pagination-page-count'));
      if (Number.isInteger(headerPageCount) && headerPageCount > 0) {
        if (headerPageCount > maxPages) {
          throw new BridgeError('Trakt pagination exceeded configured safety cap', {
            status: 502,
            code: 'trakt_pagination_limit',
            upstreamPath: pagePath,
          });
        }
        pageCount = headerPageCount;
      } else {
        pageCount = page;
      }
      page += 1;
    } while (page <= pageCount);

    return out;
  }

  async publicRequest(path) {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: this.apiHeaders(),
      signal: AbortSignal.timeout(3500),
    });
    return this.#handleResponse(response, { upstreamPath: path });
  }

  async #fetchApi(path, method, body, token) {
    return fetch(`${API_BASE}${path}`, {
      method,
      headers: this.apiHeaders(token),
      body: body == null ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(3500),
    });
  }

  async #handleResponse(response, { accept409 = false, upstreamPath = null }) {
    const parsed = await parseJsonSafe(response);
    if (response.ok || (accept409 && response.status === 409)) return parsed;

    let status = 502;
    if (response.status === 401 || response.status === 403) status = response.status;
    else if (response.status === 429) status = 429;
    else if (response.status >= 500) status = response.status;
    else if (response.status === 404 || response.status === 420 || response.status === 422) status = 422;

    throw new BridgeError(`Trakt API ${response.status}`, {
      status,
      retryAfter: response.headers.get('retry-after'),
      code: `trakt_${response.status}`,
      upstreamPath,
    });
  }

  async pullState(profileId, since = null) {
    const aliases = loadIdentityAliases(this.db, profileId);
    const pullIdentityMode = this.config.pullIdentityMode || 'trakt';
    const [activities, moviePlayback, episodePlayback] = await Promise.all([
      this.request(profileId, '/sync/last_activities'),
      this.requestAllPages(profileId, '/sync/playback/movies?extended=full', { limit: 100 }),
      this.requestAllPages(profileId, '/sync/playback/episodes?extended=full', { limit: 100 }),
    ]);

    const identityVersion = pullIdentityMode === 'aiostreams'
      ? `mode:aiostreams:${identityAliasVersion(aliases)}`
      : 'mode:trakt';
    const version = stateVersionFromActivities(
      activities || {},
      identityVersion,
    );
    const payload = {
      version,
      items: buildPlaybackItems(
        moviePlayback,
        rowsForPullIdentity(episodePlayback, aliases, pullIdentityMode),
      ),
    };

    if (!includeChangedStateForSince(since, version)) return payload;

    try {
      const [movieWatched, showWatched, movieWatchlist, showWatchlist] = await Promise.all([
        this.requestAllPages(profileId, '/sync/watched/movies', { limit: 250 }),
        this.requestAllPages(profileId, '/sync/watched/shows?extended=progress', { limit: 100 }),
        this.requestAllPages(profileId, '/sync/watchlist/movies/added/desc', { limit: 100 }),
        this.requestAllPages(profileId, '/sync/watchlist/shows/added/desc', { limit: 100 }),
      ]);
      payload.watched = buildWatchedState(
        movieWatched,
        rowsForPullIdentity(showWatched, aliases, pullIdentityMode),
      );
      payload.watchlist = buildWatchlistState(
        movieWatchlist,
        rowsForPullIdentity(showWatchlist, aliases, pullIdentityMode),
      );
    } catch (err) {
      if (err instanceof BridgeError) throw err;
      throw new BridgeError('Trakt authoritative state was incomplete', {
        status: 502,
        code: 'trakt_state_incomplete',
        cause: err,
        upstreamPath: '/sync/watched/* or /sync/watchlist/*',
      });
    }

    return payload;
  }

  async resolveMedia(event) {
    if (event.event === 'watchlisted' || event.event === 'unwatchlisted') {
      if (event.scope === 'series') return this.resolveShow(event);
      return this.resolveMovie(event);
    }
    if (event.scope === 'episode' || Number.isInteger(Number(event.season)) || Number.isInteger(Number(event.episode))) {
      return this.resolveEpisode(event);
    }
    return this.resolveMovie(event);
  }

  async resolveMovie(event) {
    const ids = providerIdsForEvent(event);
    const key = `movie:${event.metaId || ''}:${JSON.stringify(ids)}`;
    const cached = this.db.cacheGet(key);
    if (cached) return { kind: 'movie', movie: cached };

    const hit = await this.lookupExternal(ids, 'movie');
    const movie = hit?.movie;
    if (!movie?.ids?.trakt || !movie?.title || !Number.isInteger(Number(movie.year))) {
      throw new BridgeError('Could not resolve movie to Trakt', { status: 422, code: 'movie_unresolved' });
    }
    const normalized = {
      title: movie.title,
      year: Number(movie.year),
      ids: normalizeMovieIds(movie.ids),
    };
    this.db.cacheSet(key, normalized);
    return { kind: 'movie', movie: normalized };
  }

  async resolveShow(event) {
    const ids = providerIdsForEvent(event);
    const key = `show:${event.metaId || ''}:${JSON.stringify(ids)}`;
    const cached = this.db.cacheGet(key);
    if (cached) return { kind: 'show', show: cached };

    const hit = await this.lookupExternal(ids, 'show');
    const show = hit?.show;
    if (!show?.ids?.trakt) {
      throw new BridgeError('Could not resolve show to Trakt', { status: 422, code: 'show_unresolved' });
    }
    const normalized = { ids: normalizeShowIds(show.ids) };
    this.db.cacheSet(key, normalized);
    return { kind: 'show', show: normalized };
  }

  async resolveEpisode(event) {
    const videoId = String(event.videoId || '');
    if (/^(kitsu|mal|anilist|anidb):/i.test(videoId)) {
      throw new BridgeError('Anime/absolute episode numbering is not mapped safely in v0.3.x', {
        status: 422,
        code: 'anime_numbering_unsupported',
      });
    }
    const season = toInt(event.season);
    const episode = toInt(event.episode);
    if (season == null || episode == null) {
      throw new BridgeError('Episode event lacks season/episode', { status: 422, code: 'episode_number_missing' });
    }

    // Resolve the parent even when the episode itself is cached. The stable
    // Trakt show id lets a successful AIOStreams stop teach which IMDb spelling
    // should be used on future pulls when pull identity mode opts into it.
    const resolvedShow = await this.resolveShow(event);
    const showTraktId = toInt(resolvedShow.show?.ids?.trakt);
    if (showTraktId == null) {
      throw new BridgeError('Could not resolve show to Trakt', { status: 422, code: 'show_unresolved' });
    }

    const ids = providerIdsForEvent(event);
    const key = `episode:${event.metaId || ''}:${season}:${episode}:${JSON.stringify(ids)}`;
    const cached = this.db.cacheGet(key);
    if (cached) {
      return { kind: 'episode', episode: cached, show: resolvedShow.show };
    }

    const ep = await this.publicRequest(`/shows/${showTraktId}/seasons/${season}/episodes/${episode}`);
    const traktEpisodeId = toInt(ep?.ids?.trakt);
    if (traktEpisodeId == null) {
      throw new BridgeError('Could not resolve episode to Trakt', { status: 422, code: 'episode_unresolved' });
    }
    const normalized = { ids: { trakt: traktEpisodeId } };
    if (toInt(ep?.ids?.tvdb) != null) normalized.ids.tvdb = toInt(ep.ids.tvdb);
    this.db.cacheSet(key, normalized);
    return { kind: 'episode', episode: normalized, show: resolvedShow.show };
  }

  async lookupExternal(ids, type) {
    const candidates = [];
    if (ids.imdb) candidates.push(['imdb', String(ids.imdb)]);
    if (ids.tmdb != null) candidates.push(['tmdb', String(ids.tmdb)]);
    if (type === 'show' && ids.tvdb != null) candidates.push(['tvdb', String(ids.tvdb)]);
    if (!candidates.length) {
      throw new BridgeError(`No usable ${type} provider ID`, { status: 422, code: `${type}_id_missing` });
    }

    for (const [provider, value] of candidates) {
      let rows;
      try {
        rows = await this.publicRequest(`/search/${provider}/${encodeURIComponent(value)}?type=${type}`);
      } catch (err) {
        // A stale provider spelling must not prevent trying another known alias.
        if (err instanceof BridgeError && err.code === 'trakt_404') continue;
        throw err;
      }
      if (Array.isArray(rows)) {
        const row = rows.find((x) => x?.type === type && x?.[type]?.ids?.trakt) || rows.find((x) => x?.[type]?.ids?.trakt);
        if (row) return row;
      }
    }
    throw new BridgeError(`Could not resolve ${type} from known provider IDs`, { status: 422, code: `${type}_unresolved` });
  }

  async applyBulkHistory(profileId, event, add) {
    for (const video of event.videos) {
      if (/^(kitsu|mal|anilist|anidb):/i.test(String(video.videoId || ''))) {
        throw new BridgeError('Anime/absolute episode numbering is not mapped safely in v0.3.x', {
          status: 422,
          code: 'anime_numbering_unsupported',
        });
      }
    }

    const resolved = await this.resolveShow(event);
    const watchedAt = new Date((Number(event.at) || Math.floor(Date.now() / 1000)) * 1000).toISOString();
    const grouped = new Map();
    for (const video of event.videos) {
      const season = toInt(video.season);
      const episode = toInt(video.episode);
      if (!grouped.has(season)) grouped.set(season, new Set());
      grouped.get(season).add(episode);
    }

    const seasons = [...grouped.entries()]
      .sort(([a], [b]) => a - b)
      .map(([number, episodes]) => ({
        number,
        episodes: [...episodes]
          .sort((a, b) => a - b)
          .map((episode) => add ? { number: episode, watched_at: watchedAt } : { number: episode }),
      }));

    const body = { shows: [{ ids: resolved.show.ids, seasons }] };
    const path = add ? '/sync/history' : '/sync/history/remove';
    await this.request(profileId, path, { method: 'POST', body });
    return {
      action: add ? 'history:bulk-add' : 'history:bulk-remove',
      scope: event.scope,
      videos: event.videos.length,
      part: Number(event.part),
      parts: Number(event.parts),
    };
  }

  async applyEvent(profileId, event, plan) {
    if (plan.kind === 'ignore') return { ignored: plan.reason };
    if (plan.kind === 'bulk-history-add') return this.applyBulkHistory(profileId, event, true);
    if (plan.kind === 'bulk-history-remove') return this.applyBulkHistory(profileId, event, false);

    const media = await this.resolveMedia(event);

    if (plan.kind === 'scrobble') {
      const payload = { progress: Number(plan.progress.toFixed(3)) };
      if (media.kind === 'movie') payload.movie = media.movie;
      else payload.episode = media.episode;
      await this.request(profileId, `/scrobble/${plan.action}`, {
        method: 'POST',
        body: payload,
        accept409: plan.action === 'stop',
      });

      let identityAlias = null;
      // AIOStreams can emit an unfinished `stop` that the bridge safely maps to
      // Trakt `/scrobble/pause`. The identity evidence is the successful
      // AIOStreams stop event, not the Trakt action name.
      if (event.event === 'stop' && media.kind === 'episode') {
        identityAlias = learnShowAlias(
          this.db,
          profileId,
          media.show?.ids,
          event.metaId,
        );
      }
      return {
        action: `scrobble:${plan.action}`,
        progress: payload.progress,
        ...(identityAlias?.changed ? {
          identityAlias: {
            traktShowId: identityAlias.traktShowId,
            preferredMetaId: identityAlias.preferredMetaId,
            traktImdb: identityAlias.traktImdb,
            previousMetaId: identityAlias.previousMetaId,
            revision: identityAlias.revision,
          },
        } : {}),
      };
    }

    if (plan.kind === 'watchlist-add' || plan.kind === 'watchlist-remove') {
      const item = media.kind === 'movie'
        ? { ids: media.movie.ids }
        : { ids: media.show.ids };
      const body = media.kind === 'movie' ? { movies: [item] } : { shows: [item] };
      const path = plan.kind === 'watchlist-add' ? '/sync/watchlist' : '/sync/watchlist/remove';
      await this.request(profileId, path, { method: 'POST', body });
      return { action: plan.kind === 'watchlist-add' ? 'watchlist:add' : 'watchlist:remove' };
    }

    const watchedAt = new Date((Number(event.at) || Math.floor(Date.now() / 1000)) * 1000).toISOString();
    const item = media.kind === 'movie'
      ? { ids: media.movie.ids, watched_at: watchedAt }
      : { ids: media.episode.ids, watched_at: watchedAt };
    const body = media.kind === 'movie' ? { movies: [item] } : { episodes: [item] };

    if (plan.kind === 'history-add') {
      await this.request(profileId, '/sync/history', { method: 'POST', body });
      return { action: 'history:add' };
    }
    if (plan.kind === 'history-remove') {
      if (body.movies) body.movies = body.movies.map(({ ids }) => ({ ids }));
      if (body.episodes) body.episodes = body.episodes.map(({ ids }) => ({ ids }));
      await this.request(profileId, '/sync/history/remove', { method: 'POST', body });
      return { action: 'history:remove' };
    }
    throw new BridgeError('Unknown event plan', { status: 500, code: 'invalid_plan' });
  }
}

function normalizeMovieIds(ids) {
  const out = { trakt: Number(ids.trakt) };
  if (ids.imdb) out.imdb = String(ids.imdb);
  if (toInt(ids.tmdb) != null) out.tmdb = toInt(ids.tmdb);
  return out;
}

function normalizeShowIds(ids) {
  const out = { trakt: Number(ids.trakt) };
  if (ids.imdb) out.imdb = String(ids.imdb);
  if (toInt(ids.tmdb) != null) out.tmdb = toInt(ids.tmdb);
  if (toInt(ids.tvdb) != null) out.tvdb = toInt(ids.tvdb);
  return out;
}

async function parseJsonSafe(response) {
  const text = await response.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch { return { raw: text.slice(0, 1000) }; }
}
