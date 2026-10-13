import contextlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parents[1] / 'scripts' / 'audit-dual-imdb.py'
spec = importlib.util.spec_from_file_location('identity_audit', AUDIT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IdentityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.aio_path = str(Path(self.tmp.name) / 'aio.db')
        self.bridge_path = str(Path(self.tmp.name) / 'bridge.db')
        self.aio = sqlite3.connect(self.aio_path)
        self.aio.executescript("""
            CREATE TABLE watch_sinks (
                uuid TEXT, persona TEXT, addon_name TEXT,
                addon_instance_id TEXT, retired_at INTEGER
            );
            CREATE TABLE watch_state (
                uuid TEXT, persona TEXT, item_key TEXT, played INTEGER,
                position_ms INTEGER, origin TEXT, updated_at INTEGER
            );
        """)
        self.aio.execute(
            'INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)',
            ('user-a', '', 'homedocker-trakt-bridge', 'e3fe3b0')
        )
        self.bridge = sqlite3.connect(self.bridge_path)
        self.bridge.executescript("""
            CREATE TABLE profiles(id TEXT, access_token_enc TEXT);
            CREATE TABLE media_cache(cache_key TEXT, payload_json TEXT, expires_at INTEGER);
        """)
        self.bridge.execute('INSERT INTO profiles VALUES (?,?)', ('profile-a', 'opaque'))
        self.bridge.execute('INSERT INTO media_cache VALUES (?,?,?)', (
            'identity-alias:v1:profile-a',
            json.dumps({'revision': 1, 'shows': {
                '263102': {'preferredMetaId': 'tt44094505', 'traktImdb': 'tt44051354'}
            }}),
            4102444800
        ))
        self.bridge.commit()
        self.aio.commit()

    def tearDown(self):
        self.aio.close()
        self.bridge.close()
        self.tmp.cleanup()

    def add_resume(self, scope, imdb, episode=7, played=0, pos=12000):
        self.aio.execute('INSERT INTO watch_state VALUES (?,?,?,?,?,?,?)', (
            scope, '', f'e|{imdb}:1:{episode}', played, pos, 'local', 1791800000000
        ))
        self.aio.commit()

    def run_audit(self):
        out = io.StringIO()
        with module.read_only(self.aio_path) as aio, module.read_only(self.bridge_path) as bridge:
            with contextlib.redirect_stdout(out):
                module.audit(aio, bridge, 'homedocker-trakt-bridge', 'e3fe3b0')
        return out.getvalue()

    def test_same_trakt_show_two_active_imdb_spellings(self):
        self.add_resume('user-a', 'tt44051354')
        self.add_resume('user-a', 'tt44094505')
        report = self.run_audit()
        self.assertIn('DUAL_IDENTITY_RESUME_CANDIDATES=1', report)
        self.assertIn('EXACT_EPISODE_OVERLAP=S01E07', report)
        self.assertIn('SOURCE=learned-bridge', report)
        self.assertNotIn('user-a', report)
        with module.read_only(self.aio_path) as ro:
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute('DELETE FROM watch_state')

    def test_no_collision_if_only_one_imdb_resume_active(self):
        self.add_resume('user-a', 'tt44051354')
        self.add_resume('user-a', 'tt44094505', played=1, pos=0)
        report = self.run_audit()
        self.assertIn('DUAL_IDENTITY_RESUME_CANDIDATES=0', report)

    def test_other_user_does_not_create_cross_scope_false_positive(self):
        self.add_resume('user-a', 'tt44051354')
        self.add_resume('user-b', 'tt44094505')
        self.assertIn('DUAL_IDENTITY_RESUME_CANDIDATES=0', self.run_audit())

    def test_ambiguous_sink_fails_closed(self):
        self.aio.execute(
            'INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)',
            ('user-b', '', 'homedocker-trakt-bridge', 'e3fe3b0')
        )
        self.aio.commit()
        with self.assertRaisesRegex(SystemExit, 'exactly one active'):
            self.run_audit()


if __name__ == '__main__':
    unittest.main()
