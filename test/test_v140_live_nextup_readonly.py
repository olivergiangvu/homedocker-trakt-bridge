import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
from unittest import mock
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-live-nextup-readonly.py"
spec = importlib.util.spec_from_file_location("r23b2_live_source", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

PROFILE = "a" * 24
CANON, ALIAS = "tt44051354", "tt44094505"

def make_db(root):
    path = Path(root) / "bridge.db"
    now = int(time.time())
    rows = {
        str(i + 1): {
            "traktImdb": "tt" + str(i + 10000000),
            "preferredMetaId": "tt" + str(i + 10000000),
            "updatedAt": now - 300,
        } for i in range(14)
    }
    rows["263102"] = {"traktImdb": CANON, "preferredMetaId": ALIAS,
                      "updatedAt": now - 300}
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE profiles (id TEXT PRIMARY KEY, access_token_enc TEXT);
        CREATE TABLE media_cache (cache_key TEXT PRIMARY KEY,
            payload_json TEXT, expires_at INTEGER);
    """)
    db.execute("INSERT INTO profiles VALUES (?,?)", (PROFILE, "DO_NOT_LOG_TOKEN"))
    db.execute("INSERT INTO media_cache VALUES (?,?,?)", (
        "identity-alias:v1:" + PROFILE, json.dumps({"shows": rows}),
        now + 3600
    ))
    db.commit()
    db.close()
    return path

def aio_responses(with_duplicate):
    series_one = "f" * 32
    series_two = "e" * 32
    rows = [{"Id": "ep1", "SeriesId": series_one}]
    if with_duplicate:
        rows.append({"Id": "ep2", "SeriesId": series_two})
    def response(path, *, token=None, limit=None):
        assert token == "X" * 40
        if path == "/Users/Me":
            return {"Id":"A" * 16}, 1
        if path == "/Shows/NextUp?Limit=50":
            return {"Items":rows}, 5
        if path == "/Items/" + series_one:
            return {"Type":"Series", "Id":series_one,
                    "ProviderIds":{"Imdb": CANON}}, 2
        if path == "/Items/" + series_two:
            return {"Type":"Series", "Id":series_two,
                    "ProviderIds":{"Imdb": ALIAS}}, 2
        raise AssertionError("unexpected or non-GET endpoint "+path)
    return response

class R23B2LiveTests(unittest.TestCase):
    def test_actual_readonly_rc6_legacy_scrobble_provenance(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            old = path.read_bytes()
            state = app.verified_source(str(path))
            self.assertEqual(state["shows"], 15)
            self.assertEqual(state["alias_groups"], 1)
            self.assertEqual(path.read_bytes(), old)
            self.assertNotIn(CANON, json.dumps(state))

    def test_duplicated_live_response_detected_with_no_ids_or_secrets_printed(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            with mock.patch.object(app, "check_localhost"), \
                 mock.patch.object(app, "fixed_get",
                                   side_effect=aio_responses(True)):
                result = app.audit("X" * 40, str(path), 20)
            self.assertEqual(result["status"], "SOURCE_DUPLICATE_PAIR_PRESENT")
            self.assertEqual(result["known_canonical_cards_in_sample"], 1)
            self.assertEqual(result["known_alias_cards_in_sample"], 1)
            self.assertTrue(result["sample_exhaustive_for_returned_nextup"])
            self.assertFalse(result["gateway_normalization_measured"])
            self.assertEqual(result["aio_api_calls"], 4)
            self.assertEqual(result["aio_history_or_playback_write_requests"], 0)
            self.assertEqual(result["trakt_requests"], 0)
            wire = json.dumps(result)
            for secret in (PROFILE, CANON, ALIAS, "X" * 40,
                           "f"*32, "e"*32, "DO_NOT_LOG_TOKEN"):
                self.assertNotIn(secret, wire)

    def test_single_result_does_not_claim_duplicate(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            with mock.patch.object(app, "check_localhost"), \
                 mock.patch.object(app, "fixed_get",
                                   side_effect=aio_responses(False)):
                result = app.audit("X" * 40, str(path), 20)
            self.assertEqual(result["status"], "DUPLICATE_NOT_OBSERVED_IN_SAMPLED_NEXTUP")
            self.assertEqual(result["known_alias_cards_in_sample"], 0)
            self.assertTrue(result["sample_exhaustive_for_returned_nextup"])

    def test_truncated_metadata_scope_disclosed(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            with mock.patch.object(app, "check_localhost"), \
                 mock.patch.object(app, "fixed_get",
                                   side_effect=aio_responses(True)):
                result = app.audit("X"*40, str(path), 1)
            self.assertFalse(result["sample_exhaustive_for_returned_nextup"])
            self.assertEqual(result["series_metadata_checked"], 1)
            self.assertEqual(result["known_alias_cards_in_sample"], 0)

    def test_invalid_token_and_id_fail_closed(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            with self.assertRaisesRegex(app.Stop, "token invalid"):
                app.audit("bad", str(path), 1)
        self.assertEqual(app.item_imdb("../injection", "X"*40),
                         ("invalid_id", None))

    def test_missing_stale_series_metadata_is_reported_not_claimed(self):
        with mock.patch.object(app, "fixed_get", return_value=(None, 2)):
            status, imdb = app.item_imdb("f" * 32, "X" * 40)
        self.assertEqual((status, imdb), ("metadata_404", None))

    def test_preflight_does_not_need_session_or_call_aio(self):
        with TemporaryDirectory() as root:
            db = make_db(root)
            with mock.patch.object(app, "check_localhost") as check, \
                 mock.patch.object(app, "fixed_get") as aio:
                out = app.source_preflight(str(db))
            check.assert_called_once_with()
            aio.assert_not_called()
            self.assertEqual(out["status"], "PRECHECK_PASS")
            self.assertFalse(out["session_token_requested_or_saved"])

    def test_preflight_legacy_expiry_has_safe_stage_and_reason(self):
        with TemporaryDirectory() as root:
            db = make_db(root)
            sqlite = sqlite3.connect(db)
            sqlite.execute("UPDATE media_cache SET expires_at=1")
            sqlite.commit()
            sqlite.close()
            with mock.patch.object(app, "check_localhost"):
                with self.assertRaises(app.StageStop) as exc:
                    app.source_preflight(str(db))
            self.assertEqual(exc.exception.phase, "RC6_LEGACY_PROVENANCE")
            self.assertEqual(exc.exception.code, "RC6_LEGACY_IDENTITY_NOT_READY")

    def test_canary_precheck_failure_skips_token(self):
        with TemporaryDirectory() as root:
            db = make_db(root)
            with mock.patch.object(app, "check_localhost",
                                   side_effect=app.Stop("R23B-1 localhost canary no longer reachable")):
                with self.assertRaises(app.StageStop) as exc:
                    app.source_preflight(str(db))
            self.assertEqual(exc.exception.phase, "CANARY_LOCAL_HEALTH")
            self.assertEqual(exc.exception.code, "CANARY_LOOPBACK_UNREACHABLE")

    def test_http_401_is_safe_coded_without_token_or_url(self):
        with TemporaryDirectory() as root:
            db = make_db(root)
            def deny(path, **kwargs):
                raise app.Stop("AIO GET status 401")
            with mock.patch.object(app, "check_localhost"), \
                 mock.patch.object(app, "fixed_get", side_effect=deny):
                with self.assertRaises(app.StageStop) as exc:
                    app.audit("X" * 40, str(db), 20)
            self.assertEqual(exc.exception.phase, "AIO_USERS_ME")
            self.assertEqual(exc.exception.code, "AIO_HTTP_401")
            self.assertNotIn("X" * 40, str(exc.exception))

    def test_no_raw_untrusted_errors_are_reflected_in_sanitized_stage(self):
        secret = "not_for_log-PRIVATE-TOKEN"
        with self.assertRaises(app.StageStop) as exc:
            app.run_phase("AIO_USERS_ME", lambda: (_ for _ in ()).throw(
                app.Stop(secret)
            ))
        self.assertEqual(exc.exception.code, "SOURCE_AUDIT_REJECTED")
        self.assertNotIn(secret, str(exc.exception))

    def test_expired_legacy_source_rejected(self):
        with TemporaryDirectory() as root:
            path = make_db(root)
            db = sqlite3.connect(path)
            db.execute("UPDATE media_cache SET expires_at=1")
            db.commit()
            db.close()
            with self.assertRaises(app.Stop):
                app.verified_source(str(path))

if __name__ == "__main__":
    unittest.main()
