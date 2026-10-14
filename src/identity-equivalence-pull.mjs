/**
 * Optional forward-compatible AIOStreams identity-equivalence pull response.
 * Disabled by default and emitted only when the existing authenticated
 * addon-key URL is called with exact experimental capability request header.
 *
 * This is a producer only. Current upstream AIOStreams does NOT recognize
 * this contract; do not turn on in production before upstream adoption.
 * It never rewrites pulled watch rows, adds Trakt calls or writes AIO SQLite.
 */
import {
  identityEquivalenceProposal,
  loadVerifiedIdentityGraph,
} from './identity-continuity.mjs';

export const IDENTITY_EQUIVALENCE_REQUEST_HEADER = 'x-aiostreams-identity-equivalences';

export function identityEquivalencePullExtension(
  db, profileId, requestedHeader, enabled = false,
) {
  if (enabled !== true || requestedHeader !== 'v1') return null;
  const graph = loadVerifiedIdentityGraph(db, profileId);
  const proposal = identityEquivalenceProposal(graph);
  if (graph.revision < 1 || proposal.identities.length > 1000) return null;
  // The version/revision travels independently of Trakt watched-state version:
  // a new alias must be usable even when Trakt last_activities did not change.
  const relations = proposal.identities.map((row) => ({
    kind: 'series',
    traktShowId: row.traktShowId,
    canonicalImdb: row.canonicalImdb,
    imdbAliases: [...row.imdbAliases],
    proof: row.proof,
  }));
  return {
    capability: 'identity-equivalences-v1',
    revision: graph.revision,
    relations,
    // AIO must quarantine these after a *newer* Bridge revision, including
    // any previously imported trusted alias. Silently omitting an identity
    // would otherwise leave the old AIO mapping active forever.
    quarantinedImdbs: [...new Set(
      graph.conflicts.map((c) => c.id).filter((x) => /^tt\d{7,12}$/.test(x)),
    )].sort(),
    quarantinedTraktShows: [...new Set(
      graph.conflicts.map((c) => c.observedShow)
        .filter((n) => Number.isSafeInteger(n) && n > 0),
    )].sort((a, b) => a - b),
    conflictCount: proposal.conflictCount,
  };
}

export function attachIdentityEquivalenceToPull(
  rawPayload, db, profileId, requestedHeader, enabled = false,
) {
  const extension = identityEquivalencePullExtension(
    db, profileId, requestedHeader, enabled,
  );
  if (!extension) return rawPayload;
  // Never mutate a cached reference shared with non-negotiating clients.
  return {
    ...rawPayload,
    identityEquivalences: extension,
  };
}
