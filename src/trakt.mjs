import { BridgeError } from './errors.mjs';

const AUTH_BASE = 'https://auth.trakt.tv';
const API_BASE = 'https://api.trakt.tv';

function toInt(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
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
      });
    }
    this.db.setTokens(profileId, body);
    return this.db.getTokens(profileId).accessToken;
  }

  async request(profileId, path, { method = 'GET', body = null, accept409 = false } = {}) {
    let token = await this.refresh(profileId, false);
    let response = await this.#fetchApi(path, method, body, token);
    if (response.status === 401) {
      token = await this.refresh(profileId, true);
      response = await this.#fetchApi(path, method, body, token);
    }
    return this.#handleResponse(response, { accept409 });
  }

  async publicRequest(path) {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: this.apiHeaders(),
      signal: AbortSignal.timeout(3500),
    });
    return this.#handleResponse(response, {});
  }

  async #fetchApi(path, method, body, token) {
    return fetch(`${API_BASE}${path}`, {
      method,
      headers: this.apiHeaders(token),
      body: body == null ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(3500),
    });
  }

  async #handleResponse(response, { accept409 = false }) {
    const parsed = await parseJsonSafe(response);
    if (response.ok || (accept409 && response.status === 409)) return parsed;

    let status = 502;
    if (response.status === 401 || response.status === 403) status = response.status;
    else if (response.status === 429) status = 429;
    else if (response.status >= 500) status = response.status;
    else if (response.status === 404 || response.status === 422) status = 422;

    throw new BridgeError(`Trakt API ${response.status}`, {
      status,
      retryAfter: response.headers.get('retry-after'),
      code: `trakt_${response.status}`,
    });
  }

  async resolveMedia(event) {
    if (event.scope === 'episode' || Number.isInteger(Number(event.season)) || Number.isInteger(Number(event.episode))) {
      return this.resolveEpisode(event);
    }
    return this.resolveMovie(event);
  }

  async resolveMovie(event) {
    const key = `movie:${event.metaId || ''}:${JSON.stringify(event.ids || {})}`;
    const cached = this.db.cacheGet(key);
    if (cached) return { kind: 'movie', movie: cached };

    const hit = await this.lookupExternal(event.ids || {}, 'movie');
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

  async resolveEpisode(event) {
    const videoId = String(event.videoId || '');
    if (/^(kitsu|mal|anilist|anidb):/i.test(videoId)) {
      throw new BridgeError('Anime/absolute episode numbering is not mapped safely in v0.1', {
        status: 422,
        code: 'anime_numbering_unsupported',
      });
    }
    const season = toInt(event.season);
    const episode = toInt(event.episode);
    if (season == null || episode == null) {
      throw new BridgeError('Episode event lacks season/episode', { status: 422, code: 'episode_number_missing' });
    }
    const key = `episode:${event.metaId || ''}:${season}:${episode}:${JSON.stringify(event.ids || {})}`;
    const cached = this.db.cacheGet(key);
    if (cached) return { kind: 'episode', episode: cached };

    const hit = await this.lookupExternal(event.ids || {}, 'show');
    const show = hit?.show;
    const showTraktId = toInt(show?.ids?.trakt);
    if (showTraktId == null) {
      throw new BridgeError('Could not resolve show to Trakt', { status: 422, code: 'show_unresolved' });
    }

    const ep = await this.publicRequest(`/shows/${showTraktId}/seasons/${season}/episodes/${episode}`);
    const traktEpisodeId = toInt(ep?.ids?.trakt);
    if (traktEpisodeId == null) {
      throw new BridgeError('Could not resolve episode to Trakt', { status: 422, code: 'episode_unresolved' });
    }
    const normalized = { ids: { trakt: traktEpisodeId } };
    if (toInt(ep?.ids?.tvdb) != null) normalized.ids.tvdb = toInt(ep.ids.tvdb);
    this.db.cacheSet(key, normalized);
    return { kind: 'episode', episode: normalized };
  }

  async lookupExternal(ids, type) {
    const candidates = [];
    if (ids.imdb) candidates.push(['imdb', String(ids.imdb)]);
    if (ids.tmdb != null) candidates.push(['tmdb', String(ids.tmdb)]);
    if (type === 'show' && ids.tvdb != null) candidates.push(['tvdb', String(ids.tvdb)]);

    for (const [provider, value] of candidates) {
      const rows = await this.publicRequest(`/search/${provider}/${encodeURIComponent(value)}?type=${type}`);
      if (Array.isArray(rows)) {
        const row = rows.find((x) => x?.type === type && x?.[type]?.ids?.trakt) || rows.find((x) => x?.[type]?.ids?.trakt);
        if (row) return row;
      }
    }
    throw new BridgeError(`No usable ${type} provider ID`, { status: 422, code: `${type}_id_missing` });
  }

  async applyEvent(profileId, event, plan) {
    const media = await this.resolveMedia(event);
    if (plan.kind === 'ignore') return { ignored: plan.reason };

    if (plan.kind === 'scrobble') {
      const payload = { progress: Number(plan.progress.toFixed(3)) };
      if (media.kind === 'movie') payload.movie = media.movie;
      else payload.episode = media.episode;
      await this.request(profileId, `/scrobble/${plan.action}`, {
        method: 'POST',
        body: payload,
        accept409: plan.action === 'stop',
      });
      return { action: `scrobble:${plan.action}`, progress: payload.progress };
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

async function parseJsonSafe(response) {
  const text = await response.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch { return { raw: text.slice(0, 1000) }; }
}
