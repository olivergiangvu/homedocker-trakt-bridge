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

function choose(a, b) {
  const ar = resume(a.item), br = resume(b.item);
  // Two distinct active resumes are independent playback state. Never
  // suppress either one, even if the last-played timestamps differ.
  if (ar && br) return null;
  if (ar !== br) return ar ? a : b;
  const at = timestamp(a.item), bt = timestamp(b.item);
  // With neither item actively resumed, require actual recency evidence;
  // a canonical IMDb spelling alone does not prove which episode is current.
  if (at !== null && bt !== null && Math.abs(at - bt) > 2000) {
    return at > bt ? a : b;
  }
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

  // Work on complete verified groups, not pairwise encounters. A group
  // containing two cards with the SAME series IMDb, or >2 entries, is
  // ambiguous and must remain visible exactly as AIO returned it.
  const groups = new Map();
  for (let index = 0; index < reply.Items.length; index++) {
    const item = reply.Items[index];
    const spelling = mapped.get(item.SeriesId);
    const verified = IMDB.test(spelling ?? '')
      ? verifiedCanonicalForImdb(verifiedGraph, spelling) : null;
    if (!verified) continue;
    const key = String(verified.traktShowId);
    const entries = groups.get(key) ?? [];
    entries.push({ item, imdb: spelling, index });
    groups.set(key, entries);
  }

  const remove = new Set();
  for (const entries of groups.values()) {
    if (entries.length !== 2) continue;
    const [a, b] = entries;
    // Only two DISTINCT verified IMDb spellings for the same Trakt show
    // qualify. Two ordinary episodes of one spelling are not alias twins.
    if (a.imdb === b.imdb || a.item.SeriesId === b.item.SeriesId) continue;
    const winner = choose(a, b);
    if (!winner) continue; // Competing active resumes / uncertain recency.
    remove.add(winner === a ? b.index : a.index);
  }
  if (!remove.size) return reply;
  const items = reply.Items.filter((_, index) => !remove.has(index));
  return {
    ...reply,
    Items: items,
    TotalRecordCount: items.length,
    ...(Object.hasOwn(reply, 'Count') ? { Count: items.length } : {}),
  };
}
