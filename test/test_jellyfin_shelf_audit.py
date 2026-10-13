import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "audit-jellyfin-shelves.py"
spec = importlib.util.spec_from_file_location("rc7_shelves", AUDIT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def episode(item_id, season=1, number=10, imdb="tt44051354", title="PRIVATE TITLE"):
    return {
        "Id": item_id, "Type": "Episode", "SeriesId": "jf-series-1",
        "ParentIndexNumber": season, "IndexNumber": number,
        "ProviderIds": {"Imdb": imdb, "Mal": "unsafe-private"},
        "Name": title, "SeriesName": "SECRET WATCH HISTORY",
        "UserData": {"PlaybackPositionTicks": 3466260000, "Played": False},
    }


class ShelfAuditTests(unittest.TestCase):
    def emit(self, a, b):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = module.report({"Items": a}, {"Items": b})
        return result, buf.getvalue()

    def test_cross_shelf_exact_item_is_overlap_not_duplicate(self):
        value = episode("same")
        result, output = self.emit([value], [value])
        self.assertEqual(result["cross"], 1)
        self.assertEqual(result["same_shelf"], 0)
        self.assertIn("CROSS_SHELF_EXACT_ID=1", output)
        self.assertIn("CLASSIFICATION=CROSS_SHELF_OVERLAP_ONLY", output)

    def test_duplicate_in_one_resume_shelf(self):
        item = episode("same")
        result, output = self.emit([item, item], [])
        self.assertEqual(result["same_shelf"], 1)
        self.assertIn("SAME_SHELF RESUME MATCH=exact", output)
        self.assertIn("CLASSIFICATION=SAME_SHELF_IDENTITY_COLLISION_CANDIDATE", output)

    def test_different_item_ids_same_series_episode(self):
        result, output = self.emit([episode("a"), episode("b")], [])
        self.assertEqual(result["same_shelf"], 1)
        self.assertIn("MATCH=series-episode", output)

    def test_same_title_different_episode_never_matches(self):
        result, output = self.emit([episode("a", number=9)], [episode("b", number=10)])
        self.assertEqual(result["cross"], 0)
        self.assertIn("NO_MATCH_IN_CAPTURED_SHELVES", output)

    def test_same_episode_with_different_provider_and_series_ids_no_fuzzy_match(self):
        first = episode("a", imdb="tt44051354")
        second = episode("b", imdb="tt44094505")
        second["SeriesId"] = "different-series"
        result, output = self.emit([first], [second])
        self.assertEqual(result["cross"], 0)
        self.assertIn("NO_MATCH_IN_CAPTURED_SHELVES", output)

    def test_provider_match_without_same_jellyfin_id(self):
        one = episode("a")
        two = episode("b")
        two["SeriesId"] = "separate-series"
        result, output = self.emit([one], [two])
        self.assertEqual(result["cross"], 1)
        self.assertIn("MATCH=provider", output)

    def test_never_prints_raw_item_ids_titles_or_unknown_provider(self):
        _, output = self.emit([episode("sensitive-internal-id")], [episode("sensitive-internal-id")])
        for private in ("sensitive-internal-id", "SECRET WATCH HISTORY", "PRIVATE TITLE", "unsafe-private"):
            self.assertNotIn(private, output)
        self.assertIn("tt44051354", output)

    def test_missing_or_malformed_json_fails_closed(self):
        for obj in ({"Items": {}}, {"Items": [None]}, {"Message": "Unauthorized"}):
            with self.assertRaises(SystemExit):
                module.parse_items(obj, "RESUME")

    def test_remote_api_or_credential_url_fails_closed(self):
        for url in (
            "https://example.com/jellyfin",
            "http://192.168.1.2:3000/jellyfin",
            "http://127.0.0.1:3000/jellyfin?token=secret",
            "http://user:pass@127.0.0.1:3000/jellyfin",
            "http://127.0.0.1:3000/other",
        ):
            with self.assertRaises(SystemExit):
                module.checked_local_url(url)
        self.assertEqual(
            module.checked_local_url("http://127.0.0.1:3000/jellyfin"),
            "http://127.0.0.1:3000/jellyfin"
        )

    def test_token_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("abcdef0123456789abcdef0123456789")
            path.chmod(0o644)
            with self.assertRaises(SystemExit):
                module.read_token(path)
            path.chmod(0o600)
            self.assertEqual(module.read_token(path), "abcdef0123456789abcdef0123456789")

    def test_offline_cli_requires_both_files(self):
        with self.assertRaises(SystemExit):
            module.main(["--resume-json", "/nowhere"])
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory) / "resume.json"
            b = Path(directory) / "nextup.json"
            a.write_text(json.dumps({"Items": [episode("a")]}))
            b.write_text(json.dumps({"Items": [episode("a")]}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                module.main(["--resume-json", str(a), "--nextup-json", str(b)])
            self.assertIn("MODE=OFFLINE_JSON_NO_HTTP", buf.getvalue())

    def test_live_api_only_uses_two_gets(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _size):
                return b'{"Items":[]}'
        class FakeOpener:
            def __init__(self):
                self.requests = []
            def open(self, req, timeout):
                self.requests.append((req.get_method(), req.full_url, timeout))
                return FakeResponse()
        opener = FakeOpener()
        base = "http://127.0.0.1:3000/jellyfin"
        module.get_json(opener, base + "/UserItems/Resume?Limit=12", "abcdef0123456789")
        module.get_json(opener, base + "/Shows/NextUp?Limit=12&EnableResumable=true", "abcdef0123456789")
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(all(x[0] == "GET" for x in opener.requests))


if __name__ == "__main__":
    unittest.main()
