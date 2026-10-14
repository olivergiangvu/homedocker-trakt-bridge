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
  // The version/revision travels independently of Trakt watched-state version:
  // a new alias must be usable even when Trakt last_activities did not change.
  const relations = proposal.identities.slice(0, 1000).map((row) => ({
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
