from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "aio_rolling_contract", DIR / "scripts" / "audit-rolling-aio-contract.py"
)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def packed_series(imdb):
    b = bytearray(16)
    b[0], b[1], b[2] = 0xA1, 0x21, 0x02
    b[3:9] = int(imdb[2:]).to_bytes(6, "big")
    b[9:13] = b"\xff\xff\xff\xff"
    return b.hex()


def item(alias):
    return {"Id": "some-jellyfin-item-id", "SeriesId": packed_series(alias)}


class FakeResponse:
    status = 200
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.data[:count]


class FakeOpener:
    def __init__(self, api_rows=None):
        self.api_rows = api_rows if api_rows is not None else [item("tt44051354")]
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url,
                           req.get_header("X-emby-token")))
        if req.full_url.endswith("/health"):
            return FakeResponse({"status": "ok", "version": "1.3.0-rc.6"})
        if req.full_url.endswith("/readiness"):
            return FakeResponse({
                "status": "ready", "ready": True, "database": "ok",
                "schemaVersion": 1, "schemaExpected": 1,
                "connectedProfiles": 1
            })
        if "/UserItems/Resume?" in req.full_url:
            return FakeResponse({"Items": [item("tt44051354")]})
        if "/Shows/NextUp?" in req.full_url:
            return FakeResponse({"Items": self.api_rows})
        raise AssertionError("unexpected outbound request: " + req.full_url)


class RollingContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.aio = root / "aio.db"
        self.bridge = root / "bridge.db"
        self.token = root / "token"
        self.token.write_text("this-is-a-dummy-local-key")
        self.token.chmod(0o600)
        self.aio_conn = sqlite3.connect(self.aio)
        self.bridge_conn = sqlite3.connect(self.bridge)
        self.aio_conn.executescript("""
            CREATE TABLE watch_sinks (
                uuid TEXT, persona TEXT, addon_name TEXT,
                addon_instance_id TEXT, retired_at INTEGER
            );
            CREATE TABLE watch_state (
                uuid TEXT, persona TEXT, item_key TEXT, kind TEXT,
                series_key TEXT, played INTEGER, position_ms INTEGER,
                match_key TEXT, sort_at INTEGER, dropped INTEGER,
                dropped_at INTEGER
            );
        """)
        self.bridge_conn.executescript("""
            CREATE TABLE profiles (id TEXT, access_token_enc TEXT);
            CREATE TABLE media_cache (
                cache_key TEXT, payload_json TEXT, expires_at INTEGER
            );
        """)
        self.aio_conn.execute(
            "INSERT INTO watch_sinks VALUES (?,?,?,?,NULL)",
            ("private-uuid", "", "homedocker-trakt-bridge", "e3fe3b0"),
        )
        self.bridge_conn.execute(
            "INSERT INTO profiles VALUES (?,?)",
            ("private-profile", "dummy-private-credential"),
        )
        for ep in range(1, 11):
            self.aio_conn.execute(
                "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("private-uuid", "", f"e|tt44051354:1:{ep}", "episode",
                 "s|tt44051354", int(ep < 10), 346626 if ep == 10 else 0,
                 None, 1000 + ep, 0, None)
            )
        for ep in range(1, 9):
            self.aio_conn.execute(
                "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("private-uuid", "", f"e|tmdb:276470:1:{ep}", "episode",
                 "s|tt44094505", 1, 0, None, 900 + ep, 0, None)
            )
        self.aio_conn.execute(
            "INSERT INTO watch_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("private-uuid", "", "s|tt44094505", "series",
             "s|tt44094505", 0, 0, None, 0, 1, 1200)
        )
        self.aio_conn.commit()
        self.bridge_conn.commit()

    def tearDown(self):
        self.aio_conn.close()
        self.bridge_conn.close()
        self.temp.cleanup()

    def run_contract(self, opener=None):
        opener = opener or FakeOpener()
        result = contract.check_contract(
            str(self.aio), str(self.bridge),
            str(DIR / "scripts" / "monitor-identity-drift.py"),
            "http://127.0.0.1:7000", "http://127.0.0.1:3240/jellyfin",
            self.token, opener=opener
        )
        return result, opener

    def test_running_latest_with_correct_readmodel_passes(self):
        result, opener = self.run_contract()
        self.assertEqual(result["contract"], "PASS_CAPTURED_CHECKS")
        self.assertEqual(result["policy"], "AIOSTREAMS_LATEST_BRIDGE_INDEPENDENT")
        self.assertEqual(result["identity"]["attention"], 0)
        self.assertEqual(result["identity"]["observed_mitigations"], 1)
        self.assertEqual(result["jellyfin"]["alias_double_nextup_groups"], 0)
        self.assertTrue(result["jellyfin"]["known_fixture_in_nextup"])
        self.assertEqual(len(opener.calls), 4)
        self.assertTrue(all(m == "GET" for m, _, _ in opener.calls))
        self.assertTrue(all(
            secret == "this-is-a-dummy-local-key"
            for _, url, secret in opener.calls if "/jellyfin/" in url
        ))
        serialized = json.dumps(result)
        self.assertNotIn("this-is-a-dummy-local-key", serialized)
        self.assertNotIn("private-uuid", serialized)
        self.assertNotIn("private-profile", serialized)

    def test_two_distinct_series_in_nextup_is_failing_contract(self):
        op = FakeOpener([item("tt44051354"), item("tt44094505")])
        result, _ = self.run_contract(op)
        self.assertEqual(result["contract"], "REVIEW_REQUIRED")
        self.assertEqual(result["jellyfin"]["alias_double_nextup_groups"], 1)
        self.assertTrue(result["jellyfin"]["known_fixture_double_nextup"])

    def test_no_alias_in_first_50_is_not_claimed_as_verified(self):
        op = FakeOpener([item("tt99999999")])
        result, _ = self.run_contract(op)
        self.assertEqual(result["jellyfin"]["observation"], "ALIAS_NOT_IN_NEXTUP_PAGE")
        self.assertFalse(result["jellyfin"]["known_fixture_in_nextup"])

    def test_drift_risk_triggers_review_without_any_write(self):
        self.aio_conn.execute(
            "UPDATE watch_state SET dropped=0 WHERE item_key='s|tt44094505'"
        )
        self.aio_conn.commit()
        result, _ = self.run_contract()
        self.assertEqual(result["identity"]["attention"], 1)
        self.assertEqual(result["contract"], "REVIEW_REQUIRED")

    def test_readiness_schema_mismatch_fails_contract(self):
        class BadOpener(FakeOpener):
            def open(self, req, timeout):
                if req.full_url.endswith("/readiness"):
                    return FakeResponse({
                        "status": "ready", "ready": True, "database": "ok",
                        "schemaVersion": 2, "schemaExpected": 1,
                        "connectedProfiles": 1
                    })
                return super().open(req, timeout)
        result, _ = self.run_contract(BadOpener())
        self.assertEqual(result["contract"], "REVIEW_REQUIRED")

    def test_rejects_nonlocal_urls_symlink_and_insecure_key(self):
        for url in ("https://127.0.0.1:3240/jellyfin",
                    "http://192.168.1.2:3240/jellyfin",
                    "http://127.0.0.1:3240/jellyfin?key=secret",
                    "http://127.0.0.1:3240/other"):
            with self.assertRaises(contract.ContractError):
                contract.require_loopback(url, "/jellyfin")
        self.token.chmod(0o644)
        with self.assertRaises(contract.ContractError):
            self.run_contract()
        self.token.chmod(0o600)
        symlink = Path(self.temp.name) / "sym"
        symlink.symlink_to(self.token)
        with self.assertRaises(contract.ContractError):
            contract.token_from_file(symlink)

    def test_opaque_packed_ids_are_not_fuzzy_guessed(self):
        self.assertIsNone(contract.packed_series_imdb({
            "Id": "abc", "SeriesId": "b2" + "00" * 15
        }))
        op = FakeOpener([{"Id": "abcd", "SeriesId": "b2" + "00" * 15}])
        result, _ = self.run_contract(op)
        self.assertEqual(result["jellyfin"]["undecodable_item_series_ids"], 1)
        self.assertEqual(result["jellyfin"]["observation"], "ALIAS_NOT_IN_NEXTUP_PAGE")

    def test_no_sql_mutation_under_auditor(self):
        self.run_contract()
        with closing(contract.import_monitor(
            DIR / "scripts" / "monitor-identity-drift.py"
        ).ro(self.aio)) as ro:
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute("DELETE FROM watch_state")


if __name__ == "__main__":
    unittest.main()
