import { BridgeError } from './errors.mjs';
import { providerIdsForEvent } from './media-ids.mjs';
import {
  recentEquivalentHistoryStateByKey,
  rememberHistoryState,
  rememberHistoryStateByKey,
} from './history-dedupe.mjs';
import { observePlaybackWatermark, observeSourcePlaybackWatermark } from './playback-watermark.mjs';
import {
  directBulkHistoryPayload,
  directHistoryPayload,
  directScrobblePayload,
  directWatchlistPayload,
  mediaFromScrobbleResponse,
  sourceSemanticMediaKey,
} from './direct-media.mjs';
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
import {
  rememberCanonicalWatchedSnapshot,
  verifyCanonicalWatchedSnapshot,
  mutateCanonicalHistoryState,
  mutateCanonicalBulkHistoryState,
} from './canonical-history.mjs';

const AUTH_BASE = 'https://auth.trakt.tv';
const API_BASE = 'https://api.trakt.tv';
const PUBLIC_RATE_LIMIT_CACHE_KEY = 'public-rate-limit:v1';
// Trakt's authenticated limit applies to a user, not only an API key.
// A profile-specific persisted cooldown avoids repeating upstream 429 storms.
// It does NOT regulate native Android TV clients outside this Bridge.
const AUTH_RATE_LIMIT_CACHE_PREFIX = 'auth-rate-limit:v1:';
const DEFAULT_PUBLIC_RATE_COOLDOWN_MS = 60_000;

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

export function parseRateLimitHeader(headers) {
  const raw = headers?.get?.('x-ratelimit');
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return {
      name: parsed?.name || null,
      period: Number.isFinite(Number(parsed?.period)) ? Number(parsed.period) : null,
      limit: Number.isFinite(Number(parsed?.limit)) ? Number(parsed.limit) : null,
      remaining: Number.isFinite(Number(parsed?.remaining)) ? Number(parsed.remaining) : null,
      until: parsed?.until || null,
    };
  } catch {
    return null;
  }
}

function safeUpstreamDetail(parsed) {
  if (!parsed || typeof parsed !== 'object') return null;
  const value = parsed.error_description ?? parsed.error ?? parsed.message ?? parsed.raw ?? null;
  if (value == null) return null;
  return String(value).replace(/\s+/g, ' ').slice(0, 240);
}

function retryAfterMs(value) {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 0) {
    if (numeric > 1e12) return Math.max(0, numeric - Date.now());
    if (numeric > 1e9) return Math.max(0, numeric * 1000 - Date.now());
    return numeric * 1000;
  }
  const date = Date.parse(String(value));
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : null;
}

function retryAfterSeconds(until) {
  return String(Math.max(1, Math.ceil((until - Date.now()) / 1000)));
}

function stalePlaybackResult(watermark, source = 'canonical') {
  return {
    action: 'playback:stale-deduped',
    ignored: 'stale_playback_event',
    watermarkSource: source,
    incomingAt: watermark.incomingAt,
    newestAt: watermark.newestAt,
    newestEventId: watermark.newestEventId,
    newestEvent: watermark.newestEvent,
    deltaSeconds: watermark.deltaSeconds,
  };
}

function resolverAliasKeys(type, ids = {}) {
  const keys = [];
  const prefix = `resolver:v2:${type}`;
  if (ids.imdb) keys.push(`${prefix}:imdb:${String(ids.imdb).toLowerCase()}`);
  if (toInt(ids.tmdb) != null) keys.push(`${prefix}:tmdb:${toInt(ids.tmdb)}`);
  if (toInt(ids.tvdb) != null) keys.push(`${prefix}:tvdb:${toInt(ids.tvdb)}`);
  if (toInt(ids.trakt) != null) keys.push(`${prefix}:trakt:${toInt(ids.trakt)}`);
  return [...new Set(keys)];
}

function resolverCacheGet(db, type, ids = {}) {
  for (const key of resolverAliasKeys(type, ids)) {
    const cached = db.cacheGet(key);
    if (cached) return cached;
  }
  return null;
}

function resolverCacheSet(db, type, ids, payload) {
  for (const key of resolverAliasKeys(type, ids)) db.cacheSet(key, payload);
}

export class TraktClient {
  constructor(config, db) {
    this.config = config;
    this.db = db;
    this.refreshing = new Map();
  }

  #authenticatedCooldownUntil(profileId) {
    const stored = this.db.cacheGet?.(`${AUTH_RATE_LIMIT_CACHE_PREFIX}${profileId}`);
    const until = Number(stored?.until || 0);
    return until > Date.now() ? until : 0;
  }

  #armAuthenticatedCooldown(profileId, retryAfter) {
    const fallbackMs = (this.config.traktAuthCooldownSeconds ?? 30) * 1000;
    const wait = retryAfterMs(retryAfter) ?? fallbackMs;
    const until = Date.now() + Math.min(3_600_000, Math.max(1_000, wait));
    const ttlSeconds = Math.ceil((until - Date.now()) / 1000) + 5;
    this.db.cacheSet?.(
      `${AUTH_RATE_LIMIT_CACHE_PREFIX}${profileId}`,
      { until },
      ttlSeconds,
    );
    return until;
  }

  #throwIfAuthenticatedCooling(profileId, path) {
    const until = this.#authenticatedCooldownUntil(profileId);
    if (!until) return;
    throw new BridgeError('Trakt authenticated API cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(until),
      code: 'trakt_rate_cooldown',
      upstreamPath: path,
      rateLimit: { name: 'AUTHENTICATED_COOLDOWN' },
    });
  }

  #publicCooldownUntil() {
    const stored = this.db.cacheGet?.(PUBLIC_RATE_LIMIT_CACHE_KEY);
    const until = Number(stored?.until || 0);
    return until > Date.now() ? until : 0;
  }

  #armPublicCooldown(retryAfter) {
    const wait = retryAfterMs(retryAfter) ?? DEFAULT_PUBLIC_RATE_COOLDOWN_MS;
    const until = Date.now() + Math.max(1000, wait);
    const ttlSeconds = Math.max(1, Math.ceil((until - Date.now()) / 1000) + 5);
    this.db.cacheSet?.(PUBLIC_RATE_LIMIT_CACHE_KEY, { until }, ttlSeconds);
    return until;
  }

  #throwIfPublicCooling(path) {
    const until = this.#publicCooldownUntil();
    if (!until) return;
    throw new BridgeError('Trakt public metadata rate limit cooldown is active', {
      status: 429,
      retryAfter: retryAfterSeconds(until),
      code: 'trakt_public_rate_cooldown',
      upstreamPath: path,
      rateLimit: { name: 'PUBLIC_METADATA_COOLDOWN' },
    });
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
    this.#throwIfAuthenticatedCooling(profileId, path);
    let token = await this.refresh(profileId, false);
    let response = await this.#fetchApi(path, method, body, token);
    if (response.status === 401) {
      token = await this.refresh(profileId, true);
      response = await this.#fetchApi(path, method, body, token);
    }
    try {
      const data = await this.#handleResponse(response, { accept409, upstreamPath: path });
      return { data, headers: response.headers, status: response.status };
    } catch (err) {
      if (err?.status === 429) {
        this.#armAuthenticatedCooldown(profileId, err.retryAfter);
      }
      throw err;
    }
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
    this.#throwIfPublicCooling(path);

    const response = await fetch(`${API_BASE}${path}`, {
      headers: this.apiHeaders(),
      signal: AbortSignal.timeout(3500),
    });

    try {
      return await this.#handleResponse(response, { upstreamPath: path });
    } catch (err) {
      if (err?.status === 429) this.#armPublicCooldown(err.retryAfter);
      throw err;
    }
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
      upstreamStatus: response.status,
      upstreamDetail: safeUpstreamDetail(parsed),
      rateLimit: parseRateLimitHeader(response.headers),
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

    if (!includeChangedStateForSince(since, version)) {
      verifyCanonicalWatchedSnapshot(
        this.db,
        profileId,
        version,
      );
      return payload;
    }

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
      rememberCanonicalWatchedSnapshot(
        this.db,
        profileId,
        payload.watched,
        { version },
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

    const aliasCached = resolverCacheGet(this.db, 'movie', ids);
    if (aliasCached) return { kind: 'movie', movie: aliasCached };

    // Backward-compatible one-time migration from the pre-v1.2 resolver key.
    const legacyKey = `movie:${event.metaId || ''}:${JSON.stringify(ids)}`;
    const legacyCached = this.db.cacheGet(legacyKey);
    if (legacyCached) {
      resolverCacheSet(this.db, 'movie', { ...ids, ...(legacyCached.ids || {}) }, legacyCached);
      return { kind: 'movie', movie: legacyCached };
    }

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
    resolverCacheSet(this.db, 'movie', { ...ids, ...normalized.ids }, normalized);
    return { kind: 'movie', movie: normalized };
  }

  async resolveShow(event) {
    const ids = providerIdsForEvent(event);

    const aliasCached = resolverCacheGet(this.db, 'show', ids);
    if (aliasCached) return { kind: 'show', show: aliasCached };

    // Backward-compatible one-time migration from the pre-v1.2 resolver key.
    const legacyKey = `show:${event.metaId || ''}:${JSON.stringify(ids)}`;
    const legacyCached = this.db.cacheGet(legacyKey);
    if (legacyCached) {
      resolverCacheSet(this.db, 'show', { ...ids, ...(legacyCached.ids || {}) }, legacyCached);
      return { kind: 'show', show: legacyCached };
    }

    const hit = await this.lookupExternal(ids, 'show');
    const show = hit?.show;
    if (!show?.ids?.trakt) {
      throw new BridgeError('Could not resolve show to Trakt', { status: 422, code: 'show_unresolved' });
    }
    const normalized = { ids: normalizeShowIds(show.ids) };
    resolverCacheSet(this.db, 'show', { ...ids, ...normalized.ids }, normalized);
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

    const canonicalKey = `episode:v2:trakt-show:${showTraktId}:${season}:${episode}`;
    const canonicalCached = this.db.cacheGet(canonicalKey);
    if (canonicalCached) {
      return { kind: 'episode', episode: canonicalCached, show: resolvedShow.show };
    }

    const ids = providerIdsForEvent(event);
    const legacyKey = `episode:${event.metaId || ''}:${season}:${episode}:${JSON.stringify(ids)}`;
    const legacyCached = this.db.cacheGet(legacyKey);
    if (legacyCached) {
      this.db.cacheSet(canonicalKey, legacyCached);
      return { kind: 'episode', episode: legacyCached, show: resolvedShow.show };
    }

    const ep = await this.publicRequest(`/shows/${showTraktId}/seasons/${season}/episodes/${episode}`);
    const traktEpisodeId = toInt(ep?.ids?.trakt);
    if (traktEpisodeId == null) {
      throw new BridgeError('Could not resolve episode to Trakt', { status: 422, code: 'episode_unresolved' });
    }
    const normalized = { ids: { trakt: traktEpisodeId } };
    if (toInt(ep?.ids?.tvdb) != null) normalized.ids.tvdb = toInt(ep.ids.tvdb);
    this.db.cacheSet(canonicalKey, normalized);
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
    const { body } = directBulkHistoryPayload(event, add);
    const path = add ? '/sync/history' : '/sync/history/remove';
    await this.request(profileId, path, { method: 'POST', body });
    mutateCanonicalBulkHistoryState(
      this.db,
      profileId,
      event,
      add,
    );
    return {
      action: add ? 'history:bulk-add' : 'history:bulk-remove',
      scope: event.scope,
      videos: event.videos.length,
      part: Number(event.part),
      parts: Number(event.parts),
      transport: 'direct-provider-ids',
    };
  }

  async applyEvent(profileId, event, plan) {
    if (plan.kind === 'ignore') return { ignored: plan.reason };
    if (plan.kind === 'bulk-history-add') return this.applyBulkHistory(profileId, event, true);
    if (plan.kind === 'bulk-history-remove') return this.applyBulkHistory(profileId, event, false);

    if (plan.kind === 'scrobble') {
      const sourceWatermark = observeSourcePlaybackWatermark(this.db, profileId, event);
      if (sourceWatermark?.stale) return stalePlaybackResult(sourceWatermark, 'source');

      const progress = Number(plan.progress.toFixed(3));
      const { target, body } = directScrobblePayload(event, progress);
      const response = await this.requestDetailed(
        profileId,
        `/scrobble/${plan.action}`,
        {
          method: 'POST',
          body,
          accept409: plan.action === 'stop',
        },
      );
      const scrobbleResult = response.data;
      const canonicalMedia = mediaFromScrobbleResponse(target, scrobbleResult);

      // Preserve the canonical watermark when Trakt gives us canonical ids back,
      // but never block the hot path on a metadata resolver.
      if (canonicalMedia) {
        observePlaybackWatermark(this.db, profileId, canonicalMedia, event);
      }

      const semanticKey = sourceSemanticMediaKey(event);
      const stopConfirmedPlayed = response.status === 409
        || scrobbleResult?.action === 'stop'
        || scrobbleResult?.action === 'scrobble';
      if (
        plan.action === 'stop'
        && stopConfirmedPlayed
      ) {
        rememberHistoryStateByKey(this.db, profileId, semanticKey, event, {
          state: 'played',
          source: response.status === 409 ? 'scrobble-stop-duplicate' : 'scrobble-stop',
          ttlSeconds: this.config.historyDedupeSeconds,
        });
        if (canonicalMedia) {
          rememberHistoryState(this.db, profileId, canonicalMedia, event, {
            state: 'played',
            source: response.status === 409 ? 'scrobble-stop-duplicate' : 'scrobble-stop',
            ttlSeconds: this.config.historyDedupeSeconds,
          });
        }
        mutateCanonicalHistoryState(
          this.db,
          profileId,
          event,
          true,
        );
      }

      let identityAlias = null;
      // Learn the AIOStreams spelling from the canonical show returned by the
      // successful Trakt scrobble instead of resolving it before the write.
      if (
        event.event === 'stop'
        && target.kind === 'episode'
        && canonicalMedia?.show?.ids
      ) {
        identityAlias = learnShowAlias(
          this.db,
          profileId,
          canonicalMedia.show.ids,
          event.metaId,
        );
      }

      return {
        action: `scrobble:${plan.action}`,
        progress,
        transport: 'direct-provider-ids',
        upstreamStatus: response.status,
        upstreamAction: scrobbleResult?.action || null,
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
      const { body } = directWatchlistPayload(event);
      const path = plan.kind === 'watchlist-add'
        ? '/sync/watchlist'
        : '/sync/watchlist/remove';
      await this.request(profileId, path, { method: 'POST', body });
      return {
        action: plan.kind === 'watchlist-add' ? 'watchlist:add' : 'watchlist:remove',
        transport: 'direct-provider-ids',
      };
    }

    const historyState = plan.kind === 'history-add'
      ? 'played'
      : plan.kind === 'history-remove'
        ? 'unplayed'
        : null;
    if (!historyState) {
      throw new BridgeError('Unknown event plan', {
        status: 500,
        code: 'invalid_plan',
      });
    }

    const semanticKey = sourceSemanticMediaKey(event);
    const duplicate = recentEquivalentHistoryStateByKey(
      this.db,
      profileId,
      semanticKey,
      event,
      historyState,
      this.config.historyDedupeSeconds,
    );
    if (duplicate) {
      return {
        action: 'history:deduped',
        ignored: 'recent_history_equivalent',
        state: historyState,
        duplicateOf: duplicate.eventId,
        duplicateSource: duplicate.source,
        deltaSeconds: duplicate.signedDeltaSeconds,
      };
    }

    const { body } = directHistoryPayload(
      event,
      plan.kind === 'history-remove',
    );

    if (plan.kind === 'history-add') {
      await this.request(profileId, '/sync/history', {
        method: 'POST',
        body,
      });
      rememberHistoryStateByKey(this.db, profileId, semanticKey, event, {
        state: 'played',
        source: 'history',
        ttlSeconds: this.config.historyDedupeSeconds,
      });
      mutateCanonicalHistoryState(
        this.db,
        profileId,
        event,
        true,
      );
      return {
        action: 'history:add',
        transport: 'direct-provider-ids',
      };
    }

    await this.request(profileId, '/sync/history/remove', {
      method: 'POST',
      body,
    });
    rememberHistoryStateByKey(this.db, profileId, semanticKey, event, {
      state: 'unplayed',
      source: 'history-remove',
      ttlSeconds: this.config.historyDedupeSeconds,
    });
    mutateCanonicalHistoryState(
      this.db,
      profileId,
      event,
      false,
    );
    return {
      action: 'history:remove',
      transport: 'direct-provider-ids',
    };
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
