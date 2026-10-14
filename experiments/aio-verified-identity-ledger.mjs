import { createHash } from 'node:crypto';

/**
 * AIOStreams upstream-side proof of concept. NOT part of Bridge runtime.
 * An in-memory model of a future persistent, scoped equivalence table.
 * Caller MUST authenticate source sink/capability against AIO watch-scope;
 * no assertion from an untrusted third-party addon is accepted.
 */
const IMDB = /^tt\d{7,12}$/;
const ROW_SERIES = /^s\|(tt\d{7,12})$/;
const MAX_RELATIONS = 1000;
const MAX_ALIASES = 12;
const safeInt = (v) => Number.isSafeInteger(v) && v > 0;
const scopeKey = (s) => JSON.stringify([s.uuid, s.persona]);

function normalizedRelation(raw) {
  if (!raw || raw.kind !== 'series' || !safeInt(raw.traktShowId)
    || !IMDB.test(raw.canonicalImdb || '')
    || !Array.isArray(raw.imdbAliases) || !raw.imdbAliases.length
    || raw.imdbAliases.length > MAX_ALIASES
    || raw.proof !== 'successful_trakt_scrobble_response') return null;
  if (raw.imdbAliases.some((id) => !IMDB.test(id))) return null;
  return {
    kind: 'series',
    traktShowId: raw.traktShowId,
    canonicalImdb: raw.canonicalImdb,
    imdbAliases: [...new Set(raw.imdbAliases.filter(
      (id) => id !== raw.canonicalImdb
    ))].sort(),
    proof: 'successful_trakt_scrobble_response',
  };
}

export class ProposedAioIdentityLedger {
  constructor({ scope, allowedSinkId, restoredState = null }) {
    if (typeof scope?.uuid !== 'string' || !scope.uuid
        || typeof scope.persona !== 'string'
        || typeof allowedSinkId !== 'string' || !allowedSinkId) {
      throw new TypeError('explicit AIO scope and authorized tracker sink required');
    }
    this.scope = { uuid: scope.uuid, persona: scope.persona };
    this.allowedSinkId = allowedSinkId;
    this.revision = 0;
    this.lastPullFingerprint = null;
    this.relations = new Map();
    this.quarantined = new Set();
    if (restoredState) this.restore(restoredState);
  }

  /**
   * Called only after AIO verified the exact sink belongs to the same uuid
   * and persona. On pull, old evidence remains until deliberate admin removal.
   * Version is monotonic and disallows replay of a stale snapshot.
   */
  applyVerifiedPull({
    scope, sinkId, revision, relations, negotiatedCapability,
    quarantinedImdbs = [], quarantinedTraktShows = [],
  }) {
    if (scopeKey(scope ?? {}) !== scopeKey(this.scope)
        || sinkId !== this.allowedSinkId
        || negotiatedCapability !== 'identity-equivalences-v1') {
      return { accepted: false, reason: 'UNAUTHORIZED_SCOPE_OR_CAPABILITY' };
    }
    if (!Number.isSafeInteger(revision) || revision <= 0
        || !Array.isArray(relations) || relations.length > MAX_RELATIONS) {
      return { accepted: false, reason: 'INVALID_OR_UNBOUNDED_PAYLOAD' };
    }
    if (revision < this.revision) {
      return { accepted: false, reason: 'STALE_REVISION' };
    }
    if (!Array.isArray(quarantinedImdbs) || quarantinedImdbs.length > 100
        || quarantinedImdbs.some((id) => !IMDB.test(id))
        || !Array.isArray(quarantinedTraktShows) || quarantinedTraktShows.length > 100
        || quarantinedTraktShows.some((id) => !safeInt(id))) {
      return { accepted: false, reason: 'INVALID_QUARANTINE' };
    }

    // Validate all entries first; never commit a malformed partial snapshot.
    const incoming = [];
    const claim = new Map();
    for (const relation of relations) {
      const clean = normalizedRelation(relation);
      if (!clean || !clean.imdbAliases.length) {
        return { accepted: false, reason: 'INVALID_RELATION' };
      }
      incoming.push(clean);
      for (const id of [clean.canonicalImdb, ...clean.imdbAliases]) {
        if (claim.has(id) && claim.get(id) !== clean.traktShowId) {
          return { accepted: false, reason: 'CROSS_SHOW_ALIAS_CONFLICT' };
        }
        claim.set(id, clean.traktShowId);
      }
    }

    // AIO polls identical state versions repeatedly. Same revision with
    // exactly identical *canonicalized* payload is a successful no-op, not
    // an upstream HTTP/pull error; changed data with same revision fails closed.
    const fingerprint = createHash('sha256')
      .update(JSON.stringify({
        relations: [...incoming].sort((a, b) => a.traktShowId - b.traktShowId),
        quarantinedImdbs: [...new Set(quarantinedImdbs)].sort(),
        quarantinedTraktShows: [...new Set(quarantinedTraktShows)].sort((a, b) => a - b),
      }))
      .digest('hex');
    if (revision === this.revision) {
      return fingerprint === this.lastPullFingerprint
        ? { accepted: true, unchanged: true, revision }
        : { accepted: false, reason: 'REVISION_REUSED_WITH_DIFFERENT_CONTENT' };
    }

    const tentative = new Map([...this.relations].map(([id, r]) =>
      [id, { ...r, imdbAliases: [...r.imdbAliases] }]));
    const blocked = new Set(this.quarantined);
    const owners = new Map();
    for (const relation of tentative.values()) {
      for (const id of [relation.canonicalImdb, ...relation.imdbAliases]) {
        if (!owners.has(id)) owners.set(id, relation.traktShowId);
      }
    }
    // Pull-time revocation/quarantine always takes priority over previous
    // learned evidence. This blocks old alias mapping after Bridge discovers
    // a later Trakt cross-show or canonical-ID conflict.
    for (const id of quarantinedImdbs) {
      const owner = owners.get(id);
      if (owner) blocked.add(owner);
    }
    for (const show of quarantinedTraktShows) {
      if (tentative.has(show)) blocked.add(show);
    }
    let conflicts = 0;
    for (const relation of incoming) {
      const previous = tentative.get(relation.traktShowId);
      const ids = [relation.canonicalImdb, ...relation.imdbAliases];
      if (previous && previous.canonicalImdb !== relation.canonicalImdb) {
        blocked.add(relation.traktShowId);
        conflicts++;
        continue;
      }
      if (ids.some((id) =>
        owners.has(id) && owners.get(id) !== relation.traktShowId)) {
        // A rejected new Trakt show has no stored relation; quarantine only
        // previously trusted owners, to keep snapshots restart-safe.
        if (previous) blocked.add(relation.traktShowId);
        for (const id of ids) {
          const owner = owners.get(id);
          if (owner) blocked.add(owner);
        }
        conflicts++;
        continue;
      }
      const aliases = [...new Set([
        ...(previous?.imdbAliases || []), ...relation.imdbAliases,
      ])].sort();
      if (aliases.length > MAX_ALIASES) {
        blocked.add(relation.traktShowId);
        conflicts++;
        continue;
      }
      const next = { ...relation, imdbAliases: aliases };
      tentative.set(relation.traktShowId, next);
      for (const id of [next.canonicalImdb, ...next.imdbAliases]) {
        owners.set(id, relation.traktShowId);
      }
    }
    this.relations = tentative;
    this.quarantined = blocked;
    this.revision = revision;
    this.lastPullFingerprint = fingerprint;
    return {
      accepted: true,
      revision,
      relationsKnown: tentative.size,
      quarantinedShows: blocked.size,
      conflicts,
    };
  }

  /** Scoped exact alias lookup. Never merge by title/year. */
  canonicalSeriesFor(scope, seriesKey) {
    if (scopeKey(scope ?? {}) !== scopeKey(this.scope)) return null;
    const id = ROW_SERIES.exec(seriesKey || '')?.[1];
    if (!id) return null;
    const matching = [...this.relations.values()].filter((r) =>
      !this.quarantined.has(r.traktShowId)
      && (r.canonicalImdb === id || r.imdbAliases.includes(id)));
    if (matching.length !== 1) return null;
    return {
      traktShowId: matching[0].traktShowId,
      canonicalImdb: matching[0].canonicalImdb,
      logicalSeriesKey: 'trakt|' + matching[0].traktShowId,
    };
  }

  /** Virtual per-episode match key, read-only; skip anime/absolute numbering. */
  virtualEpisodeMatchKey(scope, row) {
    if (scopeKey(scope ?? {}) !== scopeKey(this.scope)) return null;
    const canonical = this.canonicalSeriesFor(scope, row?.seriesKey);
    if (!canonical || !Number.isSafeInteger(row.season) || row.season < 0
        || !safeInt(row.episode) || row.kind !== 'episode') return null;
    const original = ROW_SERIES.exec(row.seriesKey)?.[1];
    if (row.videoId !== original + ':' + row.season + ':' + row.episode) return null;
    return 'e|' + canonical.canonicalImdb + ':' + row.season + ':' + row.episode;
  }

  snapshot() {
    return {
      contract: 'aio-identity-ledger-snapshot-v1',
      scope: { ...this.scope },
      allowedSinkId: this.allowedSinkId,
      revision: this.revision,
      lastPullFingerprint: this.lastPullFingerprint,
      relations: [...this.relations.values()].map((x) => ({
        ...x, imdbAliases: [...x.imdbAliases],
      })),
      quarantined: [...this.quarantined],
    };
  }

  restore(snapshot) {
    if (snapshot?.contract !== 'aio-identity-ledger-snapshot-v1'
        || scopeKey(snapshot.scope ?? {}) !== scopeKey(this.scope)
        || snapshot.allowedSinkId !== this.allowedSinkId
        || !Number.isSafeInteger(snapshot.revision)
        || snapshot.revision < 0
        || (snapshot.revision > 0
          && !/^[0-9a-f]{64}$/.test(snapshot.lastPullFingerprint || ''))
        || !Array.isArray(snapshot.relations)
        || snapshot.relations.length > MAX_RELATIONS
        || !Array.isArray(snapshot.quarantined)) {
      throw new TypeError('invalid or wrong-owner identity ledger snapshot');
    }
    const candidate = new Map();
    for (const raw of snapshot.relations) {
      const clean = normalizedRelation(raw);
      if (!clean || candidate.has(clean.traktShowId)) throw new TypeError('invalid saved relation');
      candidate.set(clean.traktShowId, clean);
    }
    const owners = new Map();
    for (const relation of candidate.values()) {
      for (const id of [relation.canonicalImdb, ...relation.imdbAliases]) {
        if (owners.has(id) && owners.get(id) !== relation.traktShowId) {
          throw new TypeError('ambiguous saved aliases');
        }
        owners.set(id, relation.traktShowId);
      }
    }
    if (snapshot.quarantined.some((n) => !safeInt(n) || !candidate.has(n))) {
      throw new TypeError('invalid saved conflict');
    }
    this.revision = snapshot.revision;
    this.lastPullFingerprint = snapshot.lastPullFingerprint ?? null;
    this.relations = candidate;
    this.quarantined = new Set(snapshot.quarantined);
  }
}
