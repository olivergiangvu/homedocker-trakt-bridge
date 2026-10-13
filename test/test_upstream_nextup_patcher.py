import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "contrib/aiostreams/apply-nextup-canary.py"
spec = importlib.util.spec_from_file_location("aio_nextup_patcher", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def old_library_fixture():
    return (
        "\nimport { getMeta } from './resolve.js';\n"
        "    const provider = getWatchStateProvider();\n"
        "    const items: JellyfinItem[] = [];\n"
        "    if (seriesId) {\n"
        "        return nextUpForSeries(ctx, d, row, { includeResumable }).catch(\n"
        "          () => null\n"
        "        );\n"
        "        if (shown.has(series)) continue;\n"
        "        shown.add(series);\n"
        "        items.push(next);\n"
        "      }\n"
        "    }\n"
        "    send(req, res, items, items.length, 0);\n"
    )


class UpstreamPatchTests(unittest.TestCase):
    def test_exact_anchor_patch(self):
        result = mod.patch_source(old_library_fixture())
        self.assertIn("reconcileNextUpCandidates", result)
        self.assertIn("nextUpSourceBaseByItemId.set(candidate.Id, d.i)", result)
        self.assertIn("AIO_NEXTUP_IDENTITY_ALIASES", result)
        self.assertIn("send(req, res, normalized, normalized.length, 0);", result)
        self.assertNotIn(
            "return nextUpForSeries(ctx, d, row, { includeResumable }).catch(", result
        )

    def test_missing_or_ambiguous_anchor_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsafe upstream anchor"):
            mod.patch_source("")
        with self.assertRaisesRegex(ValueError, "Unsafe upstream anchor"):
            mod.patch_source(old_library_fixture() * 2)

    def test_dry_run_does_not_mutate_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / mod.LIBRARY
            target.parent.mkdir(parents=True)
            target.write_text(old_library_fixture())
            helper = root / mod.NEW_HELPER
            def git_stub(checkout, *args):
                if args == ("rev-parse", "HEAD"):
                    return mod.UPSTREAM_SHA
                if args[:1] == ("hash-object",):
                    return mod.LIBRARY_BLOB_SHA
                if args == ("status", "--porcelain"):
                    return ""
                raise AssertionError(args)
            buf = io.StringIO()
            with patch.object(mod, "git", git_stub), contextlib.redirect_stdout(buf):
                mod.stage(root, apply=False)
            self.assertEqual(target.read_text(), old_library_fixture())
            self.assertFalse(helper.exists())
            self.assertIn("RESULT=DRY_RUN_ONLY", buf.getvalue())

    def test_apply_checks_clean_checkout_and_exact_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / mod.LIBRARY
            target.parent.mkdir(parents=True)
            target.write_text(old_library_fixture())
            helper = root / mod.NEW_HELPER
            def good_git(checkout, *args):
                if args == ("rev-parse", "HEAD"):
                    return mod.UPSTREAM_SHA
                if args[:1] == ("hash-object",):
                    return mod.LIBRARY_BLOB_SHA
                if args == ("status", "--porcelain"):
                    return ""
                raise AssertionError(args)
            buf = io.StringIO()
            with patch.object(mod, "git", good_git), contextlib.redirect_stdout(buf):
                mod.stage(root, apply=True)
            self.assertTrue(helper.is_file())
            self.assertIn("nextUpSourceBaseByItemId", target.read_text())
            self.assertIn("PATCH_APPLIED", buf.getvalue())
            # The patch refuses to be applied twice.
            with patch.object(mod, "git", good_git):
                with self.assertRaisesRegex(ValueError, "helper already exists"):
                    mod.stage(root, apply=True)

    def test_dirty_checkout_refused_for_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / mod.LIBRARY
            target.parent.mkdir(parents=True)
            target.write_text(old_library_fixture())
            def dirty_git(checkout, *args):
                if args == ("rev-parse", "HEAD"):
                    return mod.UPSTREAM_SHA
                if args[:1] == ("hash-object",):
                    return mod.LIBRARY_BLOB_SHA
                if args == ("status", "--porcelain"):
                    return " M packages/server/src/routes/jellyfin/library.ts"
                raise AssertionError(args)
            with patch.object(mod, "git", dirty_git):
                with self.assertRaisesRegex(ValueError, "dirty checkout"):
                    mod.stage(root, apply=True)


if __name__ == "__main__":
    unittest.main()
