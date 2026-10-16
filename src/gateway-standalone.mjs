/**
 * Isolated Gateway-only canary process.
 *
 * Deliberately does NOT import BridgeDb, loadConfig, ManagedTraktClient,
 * AIO reconciler or Trakt OAuth. The only file it reads is a restricted,
 * sanitized identity graph exported read-only from RC6's trusted legacy cache.
 * No shared SQLite, credentials or Bridge profile secrets are mounted.
 *
 * The ordinary production Bridge entrypoint stays src/index.mjs unchanged.
 */
import { readFileSync, lstatSync } from 'node:fs';
import { createJellyfinGateway } from './jellyfin-gateway.mjs';

const PROFILE = /^[a-f0-9]{24}$/;
const IMDB = /^tt\d{7,12}$/;

export function parseCanaryIdentityProof(text) {
  const source = JSON.parse(text);
  if (!source || source.contract !== 'homedocker-canary-identity-proof-v1'
      || !PROFILE.test(source.profileId || '')) {
    throw new Error('Invalid sanitized canary identity proof');
  }
  const g = source.graph;
  if (!g || g.revision !== 1 || !g.shows || Array.isArray(g.shows)
      || typeof g.shows !== 'object'
      || !Array.isArray(g.conflicts) || g.conflicts.length
      || Object.keys(g.shows).length !== 1
      || !Object.hasOwn(g.shows, '263102')) {
    throw new Error('Canary proof has unexpected shows or conflicts');
  }
  const show = g.shows['263102'];
  if (show.canonicalImdb !== 'tt44051354'
      || !Array.isArray(show.aliases)
      || show.aliases.length !== 1
      || show.aliases[0] !== 'tt44094505'
      || show.tmdb != null || show.tvdb != null
      || !IMDB.test(show.canonicalImdb)) {
    throw new Error('Canary proof identity pair mismatch');
  }
  return {
    profileId: source.profileId,
    graph: {
      revision: 1, conflicts: [],
      shows: {
        263102: {
          canonicalImdb: show.canonicalImdb,
          aliases: [...show.aliases],
          tmdb: null,
          tvdb: null,
        },
      },
    },
  };
}

export function canaryGatewayConfig(proof, env) {
  if (!/^[A-Za-z0-9_-]{8,128}$/.test(env.JELLYFIN_GATEWAY_AIO_USER_ID || '')) {
    throw new Error('AIO Jellyfin user id required');
  }
  const port = Number(env.JELLYFIN_GATEWAY_PORT || 7101);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) {
    throw new Error('Invalid gateway canary port');
  }
  const nextUpTimeoutMs = env.JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS == null
    ? 8000 : Number(env.JELLYFIN_GATEWAY_NEXTUP_TIMEOUT_MS);
  if (!Number.isSafeInteger(nextUpTimeoutMs) || nextUpTimeoutMs < 8000
      || nextUpTimeoutMs > 20000) {
    throw new Error('Invalid NextUp canary timeout');
  }
  return {
    jellyfinGatewayEnabled: true,
    jellyfinGatewayNextUpTimeoutMs: nextUpTimeoutMs,
    jellyfinGatewayPort: port,
    jellyfinGatewayUpstreamUrl: env.JELLYFIN_GATEWAY_UPSTREAM_URL,
    jellyfinGatewayUserId: env.JELLYFIN_GATEWAY_AIO_USER_ID,
    jellyfinGatewayProfileId: proof.profileId,
    // Resume is disabled by default, even for authenticated canaries.
    // It is raw upstream passthrough only; never a deduplication switch.
    jellyfinGatewayResumePassThrough: env.JELLYFIN_GATEWAY_RESUME_PASSTHROUGH === 'true',
  };
}

export function launchCanaryGateway({
  env = process.env,
  fetchImpl = fetch,
} = {}) {
  const file = env.GATEWAY_PROOF_FILE;
  if (!file || !file.startsWith('/') || file.length > 256) {
    throw new Error('Absolute gateway proof path is required');
  }
  const stat = lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()
      || (stat.mode & 0o077) !== 0 || stat.size > 8192) {
    throw new Error('Gateway proof must be private, small regular file');
  }
  const proof = parseCanaryIdentityProof(readFileSync(file, 'utf8'));
  const config = canaryGatewayConfig(proof, env);
  // read-only BridgeDb-shaped facade, scoped to one sanitized proof key.
  const db = {
    cacheGet: (key) => key === 'identity-graph:v1:' + proof.profileId
      ? structuredClone(proof.graph) : null,
  };
  const server = createJellyfinGateway({ config, db, fetchImpl });
  const shutdown = () => server.close();
  server.on('error', () => { process.exitCode = 1; });
  return {
    server, config,
    start() {
      server.listen(config.jellyfinGatewayPort, '0.0.0.0');
      return server;
    },
    shutdown,
  };
}
