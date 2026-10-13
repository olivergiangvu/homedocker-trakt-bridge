import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repair-stale-aio-nextup.py"
spec = importlib.util.spec_from_file_location("rc7_owner_repair", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def packed(kind, imdb, season=None, episode=None):
    buf = bytearray(16)
    buf[0] = 0xA1
    buf[1] = ({"series": 2, "episode": 4}[kind] << 4) | 1
    buf[2] = 2
    buf[3:9] = int(imdb[2:]).to_bytes(6, "big")
    buf[9:11] = (65535 if season is None else season).to_bytes(2, "big")
    buf[11:13] = (65535 if episode is None else episode).to_bytes(2, "big")
    return buf.hex()


def row_for(alias, episode, ticks=0):
    return {
        "Id": packed("episode", alias, 1, episode),
        "SeriesId": packed("series", alias),
        "ParentIndexNumber": 1, "IndexNumber": episode,
        "UserData": {"PlaybackPositionTicks": ticks}
    }


class Rc7OwnerSideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "aio.db")
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE watch_sinks (
                uuid TEXT, persona TEXT, addon_name TEXT,
                addon_instance_id TEXT, retired_at INTEGER
            );
            CREATE TABLE watch_state (
                uuid TEXT, persona TEXT, item_key TEXT, base_id TEXT,
                series_key TEXT, video_id TEXT, season INTEGER,
                episode INTEGER, played INTEGER, position_ms INTEGER,
                origin TEXT, match_key TEXT, dropped INTEGER,
                rating INTEGER, likes INTEGER
            );
        """)
        self.db.execute(
            "INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)",
            ("hidden-private-uuid", "", mod.SINK_NAME, mod.SINK_INSTANCE)
        )
        for ep in range(1, 11):
            self.add(mod.TRAKT, ep, ep <= 9,
                     0 if ep <= 9 else 346626,
                     "import" if ep <= 9 else "local")
        for ep in range(1, 9):
            self.add(mod.AIO, ep, True, 0, "local")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, base, ep, played, position, origin):
        video = (base if base == mod.TRAKT else "tmdb:" + mod.TMDB) + f":1:{ep}"
        self.db.execute(
            """INSERT INTO watch_state VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("hidden-private-uuid", "", "e|" + video, base,
             "s|" + base, video, 1, ep, int(played), position,
             origin, None, 0, None, None),
        )

    def preflight(self):
        out = io.StringIO()
        with contextlib.closing(mod.open_readonly(self.path)) as db, contextlib.redirect_stdout(out):
            mod.inspect_local(db)
        return out.getvalue()

    def test_full_provenance_proven_and_sqlite_never_writable(self):
        text = self.preflight()
        self.assertIn("AIO_CONNECTED_SCOPE_VERIFIED=true", text)
        self.assertIn("HISTORY_ROWS_UNCHANGED=18", text)
        self.assertNotIn("hidden-private-uuid", text)
        with contextlib.closing(mod.open_readonly(self.path)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM watch_state")

    def test_no_mutation_when_aio_timeline_changes(self):
        self.db.execute(
            "UPDATE watch_state SET match_key='e|tt44051354:1:8' WHERE item_key=?",
            ("e|tmdb:276470:1:8",),
        )
        self.db.commit()
        with self.assertRaisesRegex(SystemExit, "match key"):
            self.preflight()

    def test_fail_closed_on_existing_series_drop(self):
        self.db.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hidden-private-uuid", "", "s|" + mod.AIO, mod.AIO,
             "s|" + mod.AIO, None, None, None, 0, 0, "local", None, 1, None, None),
        )
        self.db.commit()
        with self.assertRaises(SystemExit):
            self.preflight()

    def test_rollback_preflight_requires_drop_only_series_row(self):
        self.db.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hidden-private-uuid", "", "s|" + mod.AIO, mod.AIO,
             None, None, None, None, 0, 0, "local", None, 1, None, None),
        )
        self.db.commit()
        with contextlib.closing(mod.open_readonly(self.path)) as ro:
            mod.inspect_local(ro, expect_dropped=True)
        self.db.execute(
            "UPDATE watch_state SET rating=8 WHERE item_key=?", ("s|" + mod.AIO,)
        )
        self.db.commit()
        with contextlib.closing(mod.open_readonly(self.path)) as ro:
            with self.assertRaisesRegex(SystemExit, "drop-only"):
                mod.inspect_local(ro, expect_dropped=True)

    def test_generated_rollback_series_id_is_exact_aio_packed_series(self):
        expected = packed("series", mod.AIO)
        self.assertEqual(mod.packed_series_id(mod.AIO), expected)
        with self.assertRaises(SystemExit):
            mod.packed_series_id(mod.TRAKT)

    def test_fail_closed_if_canonical_resume_disappears(self):
        self.db.execute(
            "UPDATE watch_state SET position_ms=0 WHERE item_key='e|tt44051354:1:10'"
        )
        self.db.commit()
        with self.assertRaisesRegex(SystemExit, "resume is no longer"):
            self.preflight()

    def test_http_preflight_identifies_only_stale_series(self):
        r = row_for(mod.TRAKT, 10, ticks=3466260000)
        s = row_for(mod.AIO, 9)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            target = mod.api_preflight([r], [r, s])
        self.assertEqual(target, s["SeriesId"])
        self.assertIn("AIO_HTTP_NEXTUP_DOUBLE_CONFIRMED=true", out.getvalue())

    def test_http_preflight_rejects_multiple_or_false_alias(self):
        r = row_for(mod.TRAKT, 10, ticks=3466260000)
        s = row_for(mod.AIO, 9)
        with self.assertRaises(SystemExit):
            mod.api_preflight([r], [r, s, s])
        with self.assertRaises(SystemExit):
            mod.api_preflight([r], [r])
        with self.assertRaises(SystemExit):
            mod.api_preflight([r], [r, row_for("tt99999999", 9)])

    def test_live_aio_resume_on_stale_alias_is_never_suppressed(self):
        r = row_for(mod.TRAKT, 10, ticks=3466260000)
        s = row_for(mod.AIO, 9, ticks=1230000)
        with self.assertRaisesRegex(SystemExit, "resumable"):
            mod.api_preflight([r], [r, s])

    def test_post_verification_never_invents_success(self):
        r = row_for(mod.TRAKT, 10)
        s = row_for(mod.AIO, 9)
        sid = s["SeriesId"]
        class Fake:
            def __init__(self, response):
                self.response = response
            def open(self, req, timeout):
                class Ctx:
                    def __enter__(_): return _
                    def __exit__(_, *args): return False
                    def read(_, limit): return json.dumps(self.response).encode()
                return Ctx()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(mod.verify_nextup_after(Fake({"Items": [r]}),
                                                      "http://127.0.0.1:3240/jellyfin",
                                                      "opaque", sid))
            self.assertFalse(mod.verify_nextup_after(Fake({"Items": [r, s]}),
                                                       "http://127.0.0.1:3240/jellyfin",
                                                       "opaque", sid))

    def test_url_is_loopback_only(self):
        self.assertEqual(mod.loopback_url("http://127.0.0.1:3240/jellyfin"),
                         "http://127.0.0.1:3240/jellyfin")
        for url in ("https://example.com/jellyfin", "http://192.168.1.3:3240/jellyfin",
                    "http://user:pwd@127.0.0.1:3240/jellyfin",
                    "http://127.0.0.1:3240/jellyfin?secret=yes"):
            with self.assertRaises(SystemExit):
                mod.loopback_url(url)

    def test_packed_series_identifier_validates_provider(self):
        self.assertEqual(mod.packed_id(packed("series", mod.AIO))[:2],
                         ("series", mod.AIO))
        self.assertIsNone(mod.packed_id("b2" + "00" * 15))


if __name__ == "__main__":
    unittest.main()
