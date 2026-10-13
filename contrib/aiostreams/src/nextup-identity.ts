/**
 * AIOStreams NextUp: opt-in, evidence-backed series-identity collision guard.
 *
 * Upstream integration candidate (not used by Trakt Bridge runtime).
 * Collapse a verified alias group's stale NextUp item ONLY when exactly one
 * resumable candidate wins and at least two distinct IMDb aliases are present.
 * Never title-match; never touch persisted watched / resume state.
 */
export type NextUpCandidate = {
  Id: string;
  UserData?: {
    Played?: boolean;
    PlaybackPositionTicks?: number;
  };
};

const IMDB = /^tt\d{7,12}$/;

export function parseVerifiedAliasGroups(
  raw: string
): Map<string, string> | null {
  if (!raw.trim()) return new Map();
  if (raw.length > 4096) return null;
  const groups = raw.split(';');
  if (groups.length > 24) return null;
  const mapping = new Map<string, string>();
  for (const [index, source] of groups.entries()) {
    const ids = source.split(',').map((v) => v.trim().toLowerCase());
    if (ids.length < 2 || ids.length > 8 || new Set(ids).size !== ids.length)
      return null;
    for (const id of ids) {
      if (!IMDB.test(id) || mapping.has(id)) return null;
      mapping.set(id, String(index));
    }
  }
  return mapping;
}

function activeResume(item: NextUpCandidate): boolean {
  return (
    item.UserData?.Played !== true &&
    typeof item.UserData?.PlaybackPositionTicks === 'number' &&
    Number.isFinite(item.UserData.PlaybackPositionTicks) &&
    item.UserData.PlaybackPositionTicks > 0
  );
}

/**
 * Stable / safe: invalid or missing aliases, two competing resume points,
 * same-alias duplication, and non-resume-only cases all remain unchanged.
 *
 * Explicit example alias config: "tt44051354,tt44094505".
 * Identity is supplied by the AIO NextUp producer's source base_id, not
 * guessed from a title or the encoded Jellyfin item id.
 */
export function reconcileNextUpCandidates<T extends NextUpCandidate>(
  items: readonly T[],
  sourceBaseIdByItemId: ReadonlyMap<string, string>,
  explicitAliasGroups: string
): T[] {
  const aliases = parseVerifiedAliasGroups(explicitAliasGroups);
  if (!aliases?.size || items.length < 2) return [...items];

  const groups = new Map<
    string,
    Array<{ index: number; imdb: string; resumable: boolean }>
  >();

  for (const [index, item] of items.entries()) {
    const baseId = sourceBaseIdByItemId.get(item.Id)?.toLowerCase();
    if (!baseId) continue;
    const group = aliases.get(baseId);
    if (group === undefined) continue;
    const list = groups.get(group) ?? [];
    list.push({ index, imdb: baseId, resumable: activeResume(item) });
    groups.set(group, list);
  }

  const suppressed = new Set<number>();
  for (const group of groups.values()) {
    // A group must contain both provider spellings on the current API page.
    if (new Set(group.map((x) => x.imdb)).size < 2) continue;
    const active = group.filter((x) => x.resumable);
    // Competing resume points / rewatch ambiguity: do not silently hide one.
    if (active.length !== 1) continue;
    const winner = active[0].index;
    for (const candidate of group) {
      if (candidate.index !== winner) suppressed.add(candidate.index);
    }
  }
  return items.filter((_, index) => !suppressed.has(index));
}
