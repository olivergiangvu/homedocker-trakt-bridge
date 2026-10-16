"""R23D-2B1E read-only host-boot-fence audit regression tests."""
import ast
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

FILE = Path(__file__).resolve().parents[1]/"scripts"/"audit-v140-r23d2b1e-host-boot-fence-readonly.py"
spec = importlib.util.spec_from_file_location("r23d2b1e_audit",FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class BootFenceTests(unittest.TestCase):
    def report(self,**kw):
        values={"image_attested":True,"docker_active":True,
                "tagged_peers_absent":True,"guardian_unit_absent":True,
                "canary_unit_absent":True}
        values.update(kw)
        return m.evaluate_host_fence(**values)
    def test_all_readonly_invariants_hold_is_not_permission(self):
        result=self.report()
        self.assertEqual(result["status"],"PROTECTED_HOST_BASELINE_PASS_BOOT_FENCING_HOLD")
        self.assertFalse(result["independent_host_fence_installed"])
        self.assertFalse(result["boot_order_enforcement_proven"])
        self.assertFalse(result["authenticated_aio_relay_allowed"])
    def test_image_not_attested_stop(self):
        with self.assertRaises(m.Stop):self.report(image_attested=False)
    def test_docker_inactive_stop(self):
        with self.assertRaises(m.Stop):self.report(docker_active=False)
    def test_r23d_labeled_container_stop(self):
        with self.assertRaises(m.Stop):self.report(tagged_peers_absent=False)
    def test_guardian_collision_stop(self):
        with self.assertRaises(m.Stop):self.report(guardian_unit_absent=False)
    def test_canary_service_collision_stop(self):
        with self.assertRaises(m.Stop):self.report(canary_unit_absent=False)
    def test_evidence_must_be_boolean(self):
        with self.assertRaises(m.Stop):self.report(tagged_peers_absent="true")
    def test_parse_systemd_properties_exact_only(self):
        got=m.parse_properties("LoadState=loaded\nActiveState=active\n",{"LoadState","ActiveState"})
        self.assertEqual(got["ActiveState"],"active")
        for raw in ("ActiveState=active\nActiveState=inactive\nLoadState=loaded",
                    "LoadState=loaded\nOther=good\nActiveState=active",
                    "LoadState=loaded\nActiveState=active\nActiveState=active"):
            with self.assertRaises(m.Stop):
                m.parse_properties(raw,{"LoadState","ActiveState"})
    def test_allowlist_rejects_mutations_before_subprocess(self):
        with mock.patch.object(m.subprocess,"run",side_effect=AssertionError("HOST MUTATION")):
            for command in (["systemctl","start","docker.service"],
                            ["docker","run","--rm","hello-world"],
                            ["nft","flush","ruleset"],
                            ["docker","network","create","unwanted"]):
                with self.assertRaisesRegex(m.Stop,"READ_ONLY_COMMAND_NOT_ALLOWLISTED"):
                    m.read_only_command(command)
    def test_allowlisted_docker_inspection(self):
        p=mock.Mock(returncode=0,stdout=b"29.9.0\n",stderr=b"")
        with mock.patch.object(m.subprocess,"run",return_value=p) as run:
            self.assertEqual(m.read_only_command(["docker","info","--format","{{.ServerVersion}}"]),"29.9.0\n")
            self.assertTrue(run.called)
    def test_host_audit_mocks_and_does_not_touch_real_host(self):
        answers={
            ("docker","info","--format","{{.ServerVersion}}"):"29.9.0\n",
            ("systemctl","show","docker.service","--property=LoadState","--property=ActiveState","--no-pager"):
                "LoadState=loaded\nActiveState=active\n",
            ("systemctl","show",m.UNITS[0],"--property=LoadState","--no-pager"):"LoadState=not-found\n",
            ("systemctl","show",m.UNITS[1],"--property=LoadState","--no-pager"):"LoadState=not-found\n",
            ("docker","ps","-a","--filter","label="+m.PHASE_LABEL,"--format","{{.ID}}"):"",
        }
        with mock.patch.object(m,"attest_existing",return_value=True), \
             mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"read_only_command",side_effect=lambda x: answers[tuple(x)]):
            self.assertIn("HOLD",m.audit()["status"])
    def test_source_no_runtime_control_command(self):
        source=FILE.read_text(encoding="utf8")
        self.assertIsInstance(ast.parse(source),ast.Module)
        for bad in ("subprocess.Popen(","os.system(","shell=True"):
            self.assertNotIn(bad,source)

if __name__=="__main__":
    unittest.main()
