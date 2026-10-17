import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const runner = new URL('../scripts/replay-v140-g1b-candidate.mjs', import.meta.url);
function fixture() {
  return {
    profile: 'a'.repeat(24),
    user: 'g1b_realshape_user_1234',
    legacy: { shows: {
      '1111': { traktImdb: 'tt11111111', preferredMetaId: 'tt22222222',
        updatedAt: 1720000000000 },
      '2222': { traktImdb: 'tt33333333', preferredMetaId: 'tt33333333',
        updatedAt: 1720000000000 },
    }},
    nextup: {
      Items: [
        { Id: 'episode_other', SeriesId: 'other_series',
          Type: 'Episode', UserData: { PlaybackPositionTicks: 0 } },
        { Id: 'episode_unrelated', SeriesId: 'unrelated_series',
          Type: 'Episode', UserData: { PlaybackPositionTicks: 100 } },
      ],
      TotalRecordCount: 2, Count: 2, Opaque: 'SOURCE_VISIBLE_UNMODIFIED',
    },
    details: {
      other_series: { Id: 'other_series', Type: 'Series',
        ProviderIds: { Imdb: 'tt33333333' }},
      unrelated_series: { Id: 'unrelated_series', Type: 'Series',
        ProviderIds: { Imdb: 'tt44444444' }},
    },
  };
}

test('G1B actual v1.4 HTTP Gateway replays unchanged live-shaped sample, positive 2→1, ambiguity and cross-user', () => {
  const snapshot = fixture();
  const r = spawnSync(process.execPath, [runner.pathname], {
    input: JSON.stringify(snapshot), encoding: 'utf8', timeout: 15000,
  });
  assert.equal(r.error, undefined, r.error?.message);
  assert.equal(r.status, 0, r.stderr);
  const result = JSON.parse(r.stdout);
  assert.equal(result.status, 'OFFLINE_SNAPSHOT_GATEWAY_REPLAY_PASS');
  assert.equal(result.connected_bridge_legacy_shows, 2);
  assert.equal(result.imported_graph_alias_groups, 1);
  assert.equal(result.graph_revision_in_ram, 1);
  assert.equal(result.aio_snapshot_rows_replayed, 2);
  assert.equal(result.synthetic_positive_filter_pass, true);
  assert.equal(result.synthetic_alias_before, 2);
  assert.equal(result.synthetic_alias_after, 1);
  assert.equal(result.two_active_resumes_passthrough, true);
  assert.equal(result.mismatched_user_passthrough, true);
  assert.equal(result.live_snapshot_unchanged, true);
  assert.equal(result.g1_complete, false);
  assert.equal(result.production_db_writes, 0);
  assert.equal(result.ram_only_identity_graph_writes, 1);
  assert.equal(result.real_aio_or_trakt_calls_from_container, 0);
  assert.equal(result.gateway_http_network_real_aio, false);
  for (const secret of [snapshot.user, snapshot.profile, 'tt11111111', 'tt22222222',
    'episode_other', 'G1B_OFFLINE_SYNTHETIC_TOKEN_ONLY']) {
    assert.ok(!r.stdout.includes(secret), 'private identifiers cannot appear in output');
  }
});

test('G1B rejects malformed/missing real snapshot rather than guessing', () => {
  for (const payload of ['garbage', '{}', JSON.stringify({ ...fixture(), user: 'x' }),
    JSON.stringify({ ...fixture(), profile: 'not-a-profile' })]) {
    const r = spawnSync(process.execPath, [runner.pathname], {
      input: payload, encoding: 'utf8', timeout: 15000,
    });
    assert.equal(r.status, 5);
    assert.match(r.stderr, /G1B_STOP_ISOLATED_GATEWAY_REPLAY_FAILED/);
    assert.equal(r.stdout, '');
  }
});
