import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-jellyfin-auth-preview.py"
spec = importlib.util.spec_from_file_location("jf_auth_preview", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

USER_ID = "a" * 32
SESSION = "secret-session-never-print-me"
EPISODES = {
    "Items": [
        {"Id": "packed-ep-1", "Type": "Episode",
         "SeriesId": "packed-series-1",
         "UserData": {"PlaybackPositionTicks": 4500000}},
        {"Id": "packed-ep-2", "Type": "Episode",
         "SeriesId": "packed-series-2",
         "UserData": {"PlaybackPositionTicks": 0}},
    ],
    "TotalRecordCount": 2,
}


def fake_request(method, url, *, token=None, payload=None, limit=None):
    if url.endswith("/Users/Me"):
        return (200, {"Id": USER_ID})
    if "/Shows/NextUp" in url:
        return (200, EPISODES)
    if url.endswith("/Items/packed-series-1"):
        return (200, {
            "Id": "packed-series-1", "Type": "Series",
            "ProviderIds": {"Imdb": "tt44051354"},
        })
    if url.endswith("/Items/packed-series-2"):
        return (200, {
            "Id": "packed-series-2", "Type": "Series",
            "ProviderIds": {"Imdb": "tt44094505"},
        })
    raise AssertionError("Unexpected path; test must not call external websites")


class AuthPreviewTests(unittest.TestCase):
    def test_authenticated_preview_returns_aggregates_and_verified_series_details(self):
        with patch.object(audit, "json_http", side_effect=fake_request) as caller:
            result, user = audit.read_preview(SESSION, 2)
        self.assertEqual(user, USER_ID)
        self.assertEqual(result["aio_nextup_http"], 200)
        self.assertEqual(result["gateway_nextup_http"], 200)
        self.assertEqual(result["direct_items"], 2)
        self.assertTrue(result["preview_opaque_item_signature_equal"])
        self.assertTrue(result["known_identity_both_present_in_sample"])
        self.assertEqual(result["series_lookup"]["metadata_series_valid"], 2)
        self.assertEqual(caller.call_count, 5)
        out = json.dumps(result)
        for forbidden in [
            SESSION, USER_ID, "packed-series", "packed-ep", "Movie Title",
        ]:
            self.assertNotIn(forbidden, out)
        self.assertFalse(result["session_token_saved"])

    def test_full_fifty_card_sample_detects_protected_ids_late_without_leaking_them(self):
        episodes = {"Items": [
            {"Id": "ep" + str(i), "Type": "Episode",
             "SeriesId": "series" + str(i),
             "UserData": {"PlaybackPositionTicks": 0}}
            for i in range(50)
        ]}
        def request(method, url, **kwargs):
            if url.endswith("/Users/Me"):
                return 200, {"Id": USER_ID}
            if "/Shows/NextUp" in url:
                return 200, episodes
            if "/Items/series" in url:
                index = int(url.split("/Items/series", 1)[1])
                imdb = "tt44094505" if index == 49 else (
                    "tt44051354" if index == 48 else "tt12345678")
                return 200, {
                    "Id": "series" + str(index), "Type": "Series",
                    "ProviderIds": {"Imdb": imdb},
                }
            raise AssertionError("unexpected URL")
        with patch.object(audit, "json_http", side_effect=request):
            result, _ = audit.read_preview(SESSION, 50)
        self.assertEqual(result["series_lookup"]["metadata_attempted"], 50)
        self.assertEqual(result["series_lookup"]["metadata_series_valid"], 50)
        self.assertTrue(result["known_identity_both_present_in_sample"])
        output = json.dumps(result)
        for secret in (SESSION, USER_ID, "tt44094505", "tt44051354", "series49"):
            self.assertNotIn(secret, output)

    def test_unauthenticated_or_api_key_users_me_aborts_without_nextup(self):
        with patch.object(audit, "json_http", return_value=(400, None)) as call:
            with self.assertRaisesRegex(audit.Stop, "session token"):
                audit.read_preview("synthetic_api_key", 8)
        self.assertEqual(call.call_count, 1)

    def test_changed_nextup_is_reported_not_silently_passed_as_normalization(self):
        def altered(method, url, **kwargs):
            if url.startswith(audit.GATEWAY) and "/Shows/NextUp" in url:
                new = json.loads(json.dumps(EPISODES))
                new["Items"][0]["Id"] = "changed-episode"
                return 200, new
            return fake_request(method, url, **kwargs)
        with patch.object(audit, "json_http", side_effect=altered):
            out, _ = audit.read_preview(SESSION, 2)
        self.assertFalse(out["preview_opaque_item_signature_equal"])
        self.assertEqual(out["next_gate"], "STOP_REVIEW_PREVIEW_PARITY")

    def test_mismatched_series_detail_is_not_validated_as_show(self):
        def wrong_metadata(method, url, **kwargs):
            if url.endswith("/Items/packed-series-1"):
                return 200, {"Id": "packed-series-1", "Type": "Episode",
                             "ProviderIds": {"Imdb": "tt44051354"}}
            return fake_request(method, url, **kwargs)
        with patch.object(audit, "json_http", side_effect=wrong_metadata):
            out, _ = audit.read_preview(SESSION, 2)
        self.assertFalse(out["series_lookup"]["canonical_imdb_in_sample"])
        self.assertEqual(out["series_lookup"]["metadata_series_valid"], 1)

    def test_private_user_env_file_contains_only_user_id_no_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            path = root / "jellyfin-user.env"
            audit.save_user_env(str(path), USER_ID)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(),
                             "JELLYFIN_GATEWAY_AIO_USER_ID=" + USER_ID + "\n")
            self.assertNotIn(SESSION, path.read_text())
            with self.assertRaises(FileExistsError):
                audit.save_user_env(str(path), USER_ID)

    def test_login_uses_hidden_credentials_and_reads_access_token_without_printing(self):
        def auth(method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/Users/AuthenticateByName"))
            self.assertEqual(kwargs["payload"]["Username"], "private-username")
            self.assertEqual(kwargs["payload"]["Pw"], "private-password")
            return 200, {"AccessToken": SESSION}
        with patch.object(audit.getpass, "getpass",
                          side_effect=["private-username", "private-password"]), \
                patch.object(audit, "json_http", side_effect=auth):
            self.assertEqual(audit.obtain_session("login"), SESSION)

    def test_signature_refuses_overlong_or_bad_shape(self):
        with self.assertRaises(audit.Stop):
            audit.item_signature({"Items": [None]})
        with self.assertRaises(audit.Stop):
            audit.item_signature({"Items": [{}] * 51})
        with self.assertRaises(audit.Stop):
            audit.item_signature({"Items": "not-list"})


if __name__ == "__main__":
    unittest.main()
