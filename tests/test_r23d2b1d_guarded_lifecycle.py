"""R23D-2B1D offline fail-closed state-machine tests (no host I/O)."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/"scripts"/"model-v140-r23d2b1d-guarded-lifecycle.py"
s=importlib.util.spec_from_file_location("model",p)
m=importlib.util.module_from_spec(s)
s.loader.exec_module(m)

def valid():
    return {"synthetic":True,"guard_retained_on_failure":True,
            "stages":[{"stage":x,"passed":True} for x in m.STAGES]}

class LifecycleTest(unittest.TestCase):
    def test_full_synthetic_sequence(self):
        self.assertTrue(m.evaluate(valid()))
    def test_guard_not_before_container(self):
        e=valid()
        i=m.STAGES.index("GUARD_KERNEL_ATTESTED")
        j=m.STAGES.index("FAKE_CANARY_STARTED")
        e["stages"][i],e["stages"][j]=e["stages"][j],e["stages"][i]
        with self.assertRaises(m.Stop):m.evaluate(e)
    def test_missing_supervisor(self):
        e=valid()
        e["stages"][m.STAGES.index("SUPERVISOR_READY")]["passed"]=False
        with self.assertRaises(m.Stop):m.evaluate(e)
    def test_unexpected_extra_step(self):
        e=valid()
        e["stages"].append({"stage":"AUTHENTICATED_AIO_SESSION","passed":True})
        with self.assertRaises(m.Stop):m.evaluate(e)
    def test_non_synthetic_rejected(self):
        e=valid()
        e["synthetic"]=False
        with self.assertRaises(m.Stop):m.evaluate(e)
    def test_failure_keeps_guard(self):
        self.assertEqual(m.safe_cleanup(canary_absent=True,network_absent=True,
               guard_unchanged=True,supervision_alive=False),
               "HOLD_KEEP_GUARD_REQUIRE_INDEPENDENT_RECOVERY")
    def test_network_not_empty_keeps_guard(self):
        self.assertEqual(m.safe_cleanup(canary_absent=True,network_absent=False,
               guard_unchanged=True,supervision_alive=True),"HOLD_KEEP_GUARD")
    def test_clean_verification_only_eligible(self):
        self.assertEqual(m.safe_cleanup(canary_absent=True,network_absent=True,
               guard_unchanged=True,supervision_alive=True),
               "ELIGIBLE_FOR_SEPARATELY_AUTHORIZED_GUARD_REMOVAL")
    def test_unknown_evidence_rejected(self):
        e=valid();e["secret"]="unexpected"
        with self.assertRaises(m.Stop):m.evaluate(e)
if __name__=="__main__":unittest.main()
