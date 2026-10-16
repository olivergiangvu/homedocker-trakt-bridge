"""R23D-2B1D Phase 2: deterministic offline supervisory protocol tests."""
import ast
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "model-v140-r23d2b1d-supervisor-contract.py"
spec = importlib.util.spec_from_file_location("r23d2b1d_supervisor", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
EPOCH = "R23D_SYNTHETIC_EPOCH_001"

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.gate = m.SupervisorContract(EPOCH)
        self.proof = m.example_proof(EPOCH)

    def test_valid_synthetic_proofs_eligible_only(self):
        result = self.gate.observe(self.proof)
        self.assertTrue(result["synthetic_canary_model_eligible"])
        self.assertFalse(result["authenticated_aio_relay_allowed"])
        self.assertFalse(result["live_supervisor_implemented"])

    def test_missing_fence_latches(self):
        self.proof["fence_ready"] = False
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")
        self.assertEqual(self.gate.observe(m.example_proof(EPOCH, 11_000_000_000))["status"], "LATCHED_STOP")

    def test_stale_heartbeat_stops(self):
        self.proof["heartbeat_ns"] -= m.MAX_AGE_NS + 1
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_future_heartbeat_stops(self):
        self.proof["heartbeat_ns"] = self.proof["now_ns"] + 1
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_replayed_monotonic_time_stops(self):
        self.gate.observe(self.proof)
        self.assertEqual(self.gate.observe(self.proof)["reason"], "MONOTONIC_CLOCK_REWIND_OR_REPLAY")

    def test_reboot_requires_rearm(self):
        self.proof["host_reboot_detected"] = True
        self.assertEqual(self.gate.observe(self.proof)["reason"], "REBOOT_REQUIRES_INDEPENDENT_REARM")

    def test_epoch_change_stops(self):
        self.proof["epoch"] = "R23D_SYNTHETIC_EPOCH_002"
        self.assertEqual(self.gate.observe(self.proof)["reason"], "EPOCH_DRIFT_OR_RESTART")

    def test_kernel_guard_drift_stops(self):
        self.proof["kernel_guard_attested"] = False
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_network_drift_stops(self):
        self.proof["network_identity_attested"] = False
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_denied_tcp_test_failure_stops(self):
        self.proof["denied_tcp_control_pass"] = False
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_non_boolean_proof_stops(self):
        self.proof["supervisor_independent"] = "true"
        self.assertEqual(self.gate.observe(self.proof)["reason"], "PROOF_BOOLEAN_TYPES_REJECTED")

    def test_unknown_extra_field_stops(self):
        self.proof["admin_token"] = "do-not-read"
        self.assertEqual(self.gate.observe(self.proof)["reason"], "PROOF_SHAPE_REJECTED")

    def test_non_synthetic_stops(self):
        self.proof["canary_synthetic_only"] = False
        self.assertEqual(self.gate.observe(self.proof)["status"], "LATCHED_STOP")

    def test_source_forbids_host_side_effect_imports(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"subprocess", "socket", "requests", "urllib", "os", "shutil", "ctypes"})

if __name__ == "__main__":
    unittest.main()
