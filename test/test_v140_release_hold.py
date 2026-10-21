"""v1.4 release HOLD regression — no privileged host calls or changes."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class V140ReleaseHold(unittest.TestCase):
    def test_v140_publication_gate_allows_only_reviewed_main_tip(self):
        workflow=(ROOT/".github/workflows/release.yml").read_text("utf8")
        gate=workflow.index("Qualify exact v1.4.0 publication before registry login")
        self.assertIn("startsWith(github.ref_name, 'v1.4')",workflow)
        self.assertIn("V140_STABLE_RELEASE_HOLD",workflow)
        self.assertIn('"$GITHUB_REF_NAME" != "v1.4.0"',workflow)
        self.assertIn("git fetch --no-tags --depth=1 origin refs/heads/main",workflow)
        self.assertIn("git rev-parse FETCH_HEAD",workflow)
        self.assertIn('test -s docs/releases/v1.4.0-release-qualification.md',workflow)
        self.assertIn("V140_RELEASE_QUALIFICATION=SOURCE_ONLY_APPROVED",workflow)
        self.assertLess(gate,workflow.index("docker/login-action@v3"))
        self.assertLess(gate,workflow.index("docker/build-push-action@v6"))
        self.assertLess(gate,workflow.index("gh release create"))
        self.assertIn("exit 1",workflow[gate:workflow.index("actions/setup-node@v4")])
        self.assertIn("!startsWith(github.ref_name, 'v1.4')",workflow)
        self.assertIn("type=raw,value=latest",workflow)

    def test_qualified_stable_history_remains_immutable(self):
        pkg=(ROOT/"package.json").read_text("utf8")
        self.assertTrue(
            '"version": "1.4.0"' in pkg or '"version": "1.5.0-beta.1"' in pkg,
            "v1.4 stable release fence must remain while a beta advances"
        )
        self.assertTrue((ROOT/"scripts/publish-v1.3.0-tag.py").is_file())
        self.assertTrue((ROOT/"scripts/deploy-v1.3.0-homedocker.py").is_file())
        self.assertIn("V140_STABLE_RELEASE_HOLD",
                      (ROOT/".github/workflows/release.yml").read_text("utf8"))
        from pathlib import Path
        self.assertTrue((ROOT/"docs/releases/v1.3.0.md").is_file())
        self.assertIn("v1.3.0", (ROOT/"docs/STABLE-DEPLOYMENT.md").read_text("utf8"))
    def test_registry_publishing_not_triggered_from_development(self):
        y=(ROOT/".github/workflows/release.yml").read_text("utf8")
        self.assertIn("tags:",y)
        self.assertIn("- 'v*'",y)
        self.assertNotIn("workflow_dispatch:",y)
    def test_v14_docs_explicit_no_go(self):
        p=(ROOT/"docs/V140-PHASE-CLOSURE-AND-RELEASE-HOLD.md").read_text("utf8")
        for required in ("V140_STABLE_RELEASE_HOLD","total nft ruleset flush",
                         "Draft PR #95","No remote host access",
                         "v1.4.0","Docker", "R23D-2B1L"):
            self.assertIn(required,p)
    def test_readonly_cleanup_script_still_safe(self):
        script=(ROOT/"scripts/audit-homedocker-docker-cleanup.sh").read_text("utf8")
        self.assertIn("MODE=READ_ONLY_INVENTORY",script)
        self.assertIn("DELETIONS=0",script)
        self.assertNotIn("docker system prune -a --volumes\n",script)
    def test_docs_index_links_single_v14_status(self):
        d=(ROOT/"docs/README.md").read_text("utf8")
        self.assertIn("V140-PHASE-CLOSURE-AND-RELEASE-HOLD.md",d)

if __name__=="__main__":
    unittest.main()
