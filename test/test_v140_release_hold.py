"""v1.4 release HOLD regression — no privileged host calls or changes."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class V140ReleaseHold(unittest.TestCase):
    def test_release_ci_blocks_before_registry_and_build(self):
        workflow=(ROOT/".github/workflows/release.yml").read_text("utf8")
        gate=workflow.index("Block unqualified v1.4 Gateway publication")
        self.assertIn("startsWith(github.ref_name, 'v1.4')",workflow)
        self.assertIn("V140_STABLE_RELEASE_HOLD",workflow)
        self.assertLess(gate,workflow.index("docker/login-action@v3"))
        self.assertLess(gate,workflow.index("docker/build-push-action@v6"))
        self.assertLess(gate,workflow.index("gh release create"))
        self.assertIn("exit 1",workflow[gate:workflow.index("actions/setup-node@v4")])
    def test_qualified_stable_history_remains_immutable(self):
        pkg=(ROOT/"package.json").read_text("utf8")
        self.assertIn(
            '"version": "1.4.0"', pkg,
            "v1.4 stable source preparation remains under the explicit tag HOLD"
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
