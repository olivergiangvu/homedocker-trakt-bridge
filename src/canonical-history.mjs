const SNAPSHOT_PREFIX = 'canonical-history:v1:';
const CACHE_TTL_SECONDS = 30 * 24 * 3600;

function snapshotKey(profileId) {
  return `${SNAPSHOT_PREFIX}${profileId}`;
}

function eventMetaCandidates(event = {}) {
  const values = [];
  const add = (value) => {
    const v = String(value || '').trim();
    if (v && !values.includes(v)) values.push(v);
  };

  add(event?.metaId);
  if (event?.scope === 'movie') add(event?.videoId);

  const ids = event?.ids || {};
  if (ids.imdb) add(String(ids.imdb).toLowerCase());
  if (ids.tmdb != null && String(ids.tmdb).trim()) add(`tmdb:${String(ids.tmdb).trim()}`);
  if (ids.tvdb != null && String(ids.tvdb).trim()) add(`tvdb:${String(ids.tvdb).trim()}`);

  return values;
}

function eventItemKeys(event = {}) {
  const scope = String(event?.scope || '').toLowerCase();

  if (scope === 'episode') {
    const out = [];
    const add = (value) => {
      const v = String(value || '').trim();
      if (v && !out.includes(v)) out.push(v);
    };

    const videoId = String(event?.videoId || '').trim();
    if (videoId) add(`e|${videoId}`);

    const season = Number(event?.season);
    const episode = Number(event?.episode);
    if (Number.isInteger(season) && Number.isInteger(episode)) {
      for (const metaId of eventMetaCandidates(event)) {
        add(`e|${metaId}:${season}:${episode}`);
      }
    }
    return out;
  }

  if (scope === 'movie') {
    return eventMetaCandidates(event).map((id) => `m|${id}`);
  }

  return [];
}

function eventItemKey(event = {}) {
  return eventItemKeys(event)[0] || null;
}

function watchedItems(watched = {}) {
  const out = {};

  for (const movie of watched?.movies || []) {
    const id = String(movie || '').trim();
    if (id) out[`m|${id}`] = 1;
  }

  for (const episode of watched?.episodes || []) {
    const id = String(episode || '').trim();
    if (id) out[`e|${id}`] = 1;
  }

  return out;
}

export function rememberCanonicalWatchedSnapshot(
  db,
  profileId,
  watched,
  {
    version = null,
    nowMs = Date.now(),
  } = {},
) {
  if (!db?.cacheSet) return null;
  if (!watched || typeof watched !== 'object') return null;

  const snapshot = {
    capturedAt: Number(nowMs),
    version: version || null,
    items: watchedItems(watched),
  };

  db.cacheSet(
    snapshotKey(profileId),
    snapshot,
    CACHE_TTL_SECONDS,
  );
  return snapshot;
}

export function canonicalHistoryState(
  db,
  profileId,
  event,
  {
    maxAgeSeconds = 900,
    nowMs = Date.now(),
  } = {},
) {
  const itemKey = eventItemKey(event);
  if (!itemKey) {
    return {
      known: false,
      watched: null,
      reason: 'item_key_unavailable',
      itemKey: null,
    };
  }

  if (!db?.cacheGet) {
    return {
      known: false,
      watched: null,
      reason: 'cache_unavailable',
      itemKey,
    };
  }

  const snapshot = db.cacheGet(snapshotKey(profileId));
  if (!snapshot || typeof snapshot !== 'object') {
    return {
      known: false,
      watched: null,
      reason: 'snapshot_missing',
      itemKey,
    };
  }

  const capturedAt = Number(snapshot.capturedAt || 0);
  const ageSeconds = Math.max(
    0,
    (Number(nowMs) - capturedAt) / 1000,
  );

  if (
    !Number.isFinite(capturedAt)
    || capturedAt <= 0
    || ageSeconds > Number(maxAgeSeconds)
  ) {
    return {
      known: false,
      watched: null,
      reason: 'snapshot_stale',
      itemKey,
      capturedAt,
      ageSeconds,
      version: snapshot.version || null,
    };
  }

  const candidates = eventItemKeys(event);
  const matchedItemKey = candidates.find((key) => snapshot?.items?.[key]);

  return {
    known: true,
    watched: Boolean(matchedItemKey),
    reason: 'authoritative_snapshot',
    itemKey: matchedItemKey || itemKey,
    candidates,
    capturedAt,
    ageSeconds,
    version: snapshot.version || null,
  };
}

export function mutateCanonicalHistoryState(
  db,
  profileId,
  event,
  watched,
  {
    nowMs = Date.now(),
  } = {},
) {
  const itemKeys = eventItemKeys(event);
  const itemKey = itemKeys[0] || null;
  if (!itemKey) return null;
  if (!db?.cacheGet || !db?.cacheSet) return null;

  const existing = db.cacheGet(snapshotKey(profileId));
  if (!existing || typeof existing !== 'object') return null;

  const items = {
    ...(existing.items && typeof existing.items === 'object'
      ? existing.items
      : {}),
  };

  for (const key of itemKeys) {
    if (watched) items[key] = 1;
    else delete items[key];
  }

  const next = {
    ...existing,
    items,
    lastLocalMutationAt: Number(nowMs),
  };

  db.cacheSet(
    snapshotKey(profileId),
    next,
    CACHE_TTL_SECONDS,
  );

  return {
    itemKey,
    watched: Boolean(watched),
    capturedAt: Number(next.capturedAt || 0),
  };
}

export function mutateCanonicalBulkHistoryState(
  db,
  profileId,
  event,
  watched,
  {
    nowMs = Date.now(),
  } = {},
) {
  if (!Array.isArray(event?.videos) || !event.videos.length) return 0;
  if (!db?.cacheGet || !db?.cacheSet) return 0;

  const existing = db.cacheGet(snapshotKey(profileId));
  if (!existing || typeof existing !== 'object') return 0;

  const items = {
    ...(existing.items && typeof existing.items === 'object'
      ? existing.items
      : {}),
  };
  const metaId = String(event?.metaId || '').trim();
  let changed = 0;

  for (const video of event.videos) {
    const videoId = String(video?.videoId || '').trim();
    const season = Number(video?.season);
    const episode = Number(video?.episode);
    const id = videoId
      || (
        metaId
        && Number.isInteger(season)
        && Number.isInteger(episode)
          ? `${metaId}:${season}:${episode}`
          : ''
      );
    if (!id) continue;

    const itemKey = `e|${id}`;
    if (watched) items[itemKey] = 1;
    else delete items[itemKey];
    changed += 1;
  }

  if (!changed) return 0;

  db.cacheSet(
    snapshotKey(profileId),
    {
      ...existing,
      items,
      lastLocalMutationAt: Number(nowMs),
    },
    CACHE_TTL_SECONDS,
  );

  return changed;
}

export const canonicalHistoryItemKey = eventItemKey;
export const canonicalHistoryItemKeys = eventItemKeys;
