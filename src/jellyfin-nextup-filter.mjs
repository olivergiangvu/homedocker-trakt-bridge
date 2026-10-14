/**
 * HomeDocker-owned NextUp Jellyfin gateway response filter.
 * Does NOT modify AIOStreams, call its internal databases or write playback.
 * Upstream SeriesId is opaque; resolve via authenticated series-detail API
 * in a separate gateway. No title/episode-name matching.
 */
import { verifiedCanonicalForImdb } from './identity-continuity.mjs';

const LIMIT = 100;
const IMDB = /^tt\d{7,12}$/;
const timestamp = x => {
  const t = Date.parse(x?.UserData?.LastPlayedDate ?? '');
  return Number.isFinite(t) ? t : null;
};
const resume = x => {
  const n = Number(x?.UserData?.PlaybackPositionTicks);
  return Number.isSafeInteger(n) && n > 0;
};

function choose(a, b, canonical) {
  const ar = resume(a.item), br = resume(b.item);
  if (ar !== br) return ar ? a : b;
  const at = timestamp(a.item), bt = timestamp(b.item);
  if (at !== null && bt !== null && Math.abs(at - bt) > 2000) {
    return at > bt ? a : b;
  }
  if (ar && br) return null;
  if (a.imdb === canonical && b.imdb !== canonical) return a;
  if (b.imdb === canonical && a.imdb !== canonical) return b;
  return null;
}

/**
 * Opt-in pure API view projection, preserving untouched upstream item IDs.
 * resolveSeriesImdb is injected by the caller and MUST be scoped to the exact
 * Jellyfin user and upstream authorization. On any mismatch, fail open
 * (return the untouched response) rather than hiding history.
 */
export async function filterNextUpForVerifiedAliases(reply, {
  verifiedGraph, resolveSeriesImdb,
} = {}) {
  if (!reply || Array.isArray(reply) || !Array.isArray(reply.Items)
      || reply.Items.length > LIMIT || typeof resolveSeriesImdb !== 'function') {
    return reply;
  }
  const ids = new Set();
  for (const item of reply.Items) {
    if (!item || item.Type !== 'Episode'
        || typeof item.SeriesId !== 'string' || !item.SeriesId) return reply;
    ids.add(item.SeriesId);
  }

  const mapped = new Map();
  try {
    for (const id of ids) {
      const imdb = await resolveSeriesImdb(id);
      mapped.set(id, typeof imdb === 'string' ? imdb.toLowerCase() : null);
    }
  } catch {
    return reply;
  }

  const items = [];
  const seen = new Map();
  let deduped = 0;
  for (const item of reply.Items) {
    const imdb = mapped.get(item.SeriesId);
    const verified = IMDB.test(imdb ?? '')
      ? verifiedCanonicalForImdb(verifiedGraph, imdb) : null;
    if (!verified) {
      items.push(item);
      continue;
    }
    const key = String(verified.traktShowId);
    const last = seen.get(key);
    const candidate = { item, imdb };
    if (!last) {
      seen.set(key, { candidate, ambiguous: false });
      items.push(item);
      continue;
    }
    if (last.ambiguous) {
      items.push(item);
      continue;
    }
    const winner = choose(last.candidate, candidate, verified.canonicalImdb);
    if (!winner) {
      seen.set(key, { ...last, ambiguous: true });
      items.push(item);
      continue;
    }
    deduped++;
    if (winner.item === last.candidate.item) continue;
    const index = items.indexOf(last.candidate.item);
    if (index < 0) return reply;
    items[index] = item;
    seen.set(key, { candidate, ambiguous: false });
  }
  if (!deduped) return reply;
  return {
    ...reply,
    Items: items,
    TotalRecordCount: items.length,
    ...(Object.hasOwn(reply, 'Count') ? { Count: items.length } : {}),
  };
}
