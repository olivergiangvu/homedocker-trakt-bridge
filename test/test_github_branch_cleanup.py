import importlib.util
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "cleanup-github-branches.py"
spec = importlib.util.spec_from_file_location("rc7_github_gc", SRC)
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)


def pull(n, name, sha, state="closed", merged="2026-10-13T11:00:00Z", owner=None):
    return {
        "number": n,
        "state": state,
        "merged_at": merged,
        "head": {
            "ref": name, "sha": sha,
            "repo": {"full_name": owner or gc.REPO},
        },
    }


def branch(name, sha):
    return {"name": name, "commit": {"sha": sha}}


class GithubCleanupSelectorTests(unittest.TestCase):
    def test_manual_pagination_works_on_legacy_gh_without_slurp(self):
        from unittest.mock import patch
        import json
        calls = []

        def fake_api(path, *extra):
            calls.append((path, extra))
            if "page=1" in path:
                return json.dumps([{"v": 1}, {"v": 2}])
            if "page=2" in path:
                return json.dumps([{"v": 3}])
            raise AssertionError("Unexpected page: " + path)

        with patch.object(gc, "api", fake_api):
            rows = gc.pages("repos/" + gc.REPO + "/branches?per_page=2",
                            per_page=2)
        self.assertEqual([x["v"] for x in rows], [1, 2, 3])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(extra == () for _, extra in calls))
        self.assertTrue(all("--slurp" not in url for url, _ in calls))

    def test_api_pagination_failure_refuses_partial_cleanup(self):
        from unittest.mock import patch
        import json

        def fake_api(path, *extra):
            if "page=1" in path:
                return json.dumps([{"v": 1}, {"v": 2}])
            return json.dumps([{"v": 3}, {"v": 4}])

        with patch.object(gc, "api", fake_api):
            with self.assertRaisesRegex(RuntimeError, "exceeded safe page budget"):
                gc.pages("repos/" + gc.REPO + "/pulls?per_page=2",
                         per_page=2, max_pages=2)
            with self.assertRaisesRegex(RuntimeError, "expected repository route"):
                gc.pages("repos/someone-else/other/branches")

    def test_only_exact_merged_heads_and_archived_closed_prs(self):
        archived_name = "diagnostics/rc7-jellyfin-shelves"
        archived_sha = gc.ARCHIVED[archived_name][1]
        p = [
            pull(1, "feature/merged", "a" * 40),
            pull(2, "feature/advanced", "b" * 40),
            pull(3, "feature/active", "c" * 40, state="open", merged=None),
            pull(4, "dependabot/docker/fix", "d" * 40),
            pull(83, archived_name, archived_sha, merged=None),
        ]
        branches = [
            branch("main", "f" * 40),
            branch("feature/merged", "a" * 40),
            branch("feature/advanced", "x" * 40),
            branch("feature/active", "c" * 40),
            branch("dependabot/docker/fix", "d" * 40),
            branch(archived_name, archived_sha),
        ]
        picked = gc.select_candidates(branches, p)
        self.assertEqual(
            [(name, typ) for name, _, typ in picked],
            [(archived_name, "CLOSED_UNMERGED_RC7_EXPERIMENT"),
             ("feature/merged", "MERGED_PR_HEAD")]
        )

    def test_does_not_clean_open_prs_even_if_separately_marked_merged(self):
        p = [
            pull(1, "shared", "a" * 40),
            pull(2, "shared", "a" * 40, state="open", merged=None),
        ]
        self.assertEqual(gc.select_candidates([branch("shared", "a" * 40)], p), [])

    def test_does_not_delete_fork_branch_with_same_name(self):
        p = [pull(3, "feature/same", "a" * 40,
                  owner="somebody-else/fork")]
        self.assertEqual(gc.select_candidates([branch("feature/same", "a" * 40)], p), [])

    def test_closed_but_unmerged_unknown_branch_survives(self):
        p = [pull(12, "my-unmerged-work", "a" * 40, merged=None)]
        self.assertEqual(
            gc.select_candidates([branch("my-unmerged-work", "a" * 40)], p), []
        )

    def test_experimental_branch_moved_since_close_survives(self):
        name = "fix/rc7-bridge-side-stale-nextup"
        sha = gc.ARCHIVED[name][1]
        p = [pull(85, name, sha, merged=None)]
        self.assertEqual(
            gc.select_candidates([branch(name, "f" * 40)], p), []
        )

    def test_archived_requires_corresponding_closed_unmerged_pr(self):
        name = "feat/rc7-nextup-canary-patch"
        sha = gc.ARCHIVED[name][1]
        p = [pull(84, name, sha, state="open", merged=None)]
        self.assertEqual(gc.select_candidates([branch(name, sha)], p), [])


if __name__ == "__main__":
    unittest.main()
