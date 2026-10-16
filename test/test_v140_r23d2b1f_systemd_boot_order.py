"""R23D-2B1F: synthetic systemd ordering and guardian contract regression."""
import ast
import importlib.util
from pathlib import Path
import unittest

FILE=Path(__file__).resolve().parents[1]/"scripts"/"model-v140-r23d2b1f-systemd-boot-order.py"
spec=importlib.util.spec_from_file_location("r23d2b1f_systemd",FILE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class UnitContract(unittest.TestCase):
    def test_exact_pinned_abstract_contract_passes(self):
        self.assertTrue(m.validate_unit_contract(m.proposed_units()))
    def test_requires_alone_does_not_guarantee_after(self):
        o=m.proposed_units();o["canary_after"]=[]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_missing_guardian_requires_docker(self):
        o=m.proposed_units();o["guardian_requires"]=[]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_missing_guardian_before_canary(self):
        o=m.proposed_units();o["guardian_before"]=[]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_missing_binds_to_guardian(self):
        o=m.proposed_units();o["canary_binds_to"]=[]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_guardian_oneshot_rejected(self):
        o=m.proposed_units();o["guardian_type"]="oneshot"
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_no_watchdog_rejected(self):
        o=m.proposed_units();o["guardian_watchdog_sec"]=0
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_canary_systemd_restart_rejected(self):
        o=m.proposed_units();o["canary_restart"]="always"
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_canary_docker_restart_rejected(self):
        o=m.proposed_units();o["docker_canary_restart_policy"]="unless-stopped"
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_auto_enabled_canary_rejected(self):
        o=m.proposed_units();o["canary_install_targets"]=["multi-user.target"]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_auto_enabled_guardian_rejected(self):
        o=m.proposed_units();o["guardian_install_targets"]=["multi-user.target"]
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_unfenced_external_docker_launch_rejected(self):
        o=m.proposed_units();o["canary_external_launcher_fenced"]=False
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_unknown_manifest_key_rejected(self):
        o=m.proposed_units();o["override"]="shell command"
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)
    def test_fake_boolean_watchdog_rejected(self):
        o=m.proposed_units();o["guardian_watchdog_sec"]=True
        with self.assertRaises(m.Stop):m.validate_unit_contract(o)

class BootModel(unittest.TestCase):
    def test_complete_model_never_authorizes_runtime(self):
        seq=m.BootSequence()
        for event in m.EVENTS:
            result=seq.event(event,trusted=True)
        self.assertEqual(result["state"],"MODEL_SEQUENCE_COMPLETE_NO_RUNTIME_AUTHORIZATION")
        self.assertFalse(result["real_canary_start_authorized"])
        self.assertFalse(result["authenticated_aio_relay_allowed"])
    def test_canary_start_before_nft_guard_latches(self):
        seq=m.BootSequence()
        seq.event("BOOT",trusted=True)
        self.assertEqual(seq.event("SYNTHETIC_CANARY_ALLOWED",trusted=True)["state"],"LATCHED_STOP")
    def test_missing_external_evidence_latches(self):
        seq=m.BootSequence()
        self.assertEqual(seq.event("BOOT",trusted=False)["state"],"LATCHED_STOP")
        self.assertEqual(seq.event("BOOT",trusted=True)["state"],"LATCHED_STOP")
    def test_no_implicit_progress_when_fence_missing(self):
        seq=m.BootSequence()
        seq.event("BOOT",trusted=True)
        seq.event("DOCKER_DAEMON_READY",trusted=True)
        self.assertEqual(seq.event("GUARD_INSTALLED",trusted=True)["state"],"LATCHED_STOP")
    def test_all_faults_latch(self):
        for fault in (
            "GUARD_FLUSHED","SUPERVISOR_CRASH","DOCKER_DAEMON_RESTART",
            "HOST_REBOOT","CANARY_RESTART_REQUEST","KERNEL_GUARD_DRIFT",
            "UNOWNED_CONTAINER_DETECTED","FENCE_EVIDENCE_EXPIRED"):
            with self.subTest(fault=fault):
                seq=m.BootSequence()
                seq.event("BOOT",trusted=True)
                self.assertEqual(seq.fault(fault)["state"],"LATCHED_STOP")
                self.assertFalse(seq.event("DOCKER_DAEMON_READY",trusted=True)["real_canary_start_authorized"])
    def test_unknown_fault_also_latches(self):
        self.assertEqual(m.BootSequence().fault("RANDOM_NEW_FAULT")["state"],"LATCHED_STOP")
    def test_event_after_completion_rejected(self):
        seq=m.BootSequence()
        for event in m.EVENTS:
            seq.event(event,trusted=True)
        self.assertEqual(seq.event("SYNTHETIC_CANARY_ALLOWED",trusted=True)["state"],"LATCHED_STOP")
    def test_source_is_pure_offline(self):
        tree=ast.parse(FILE.read_text("utf-8"))
        imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"subprocess","os","ctypes","socket","urllib","requests","shutil"})

if __name__=="__main__":
    unittest.main()
