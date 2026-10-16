"""Include R23D-2B1D source-only model tests in existing CI unittest discovery.

The repository's CI discovers 'test/', while local phase test files live in
'tests/'. This adapter ensures both are exercised without duplicating tests.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
CASES=(
    ROOT/"tests"/"test_r23d2b1c_existing_entrypoint.py",
    ROOT/"tests"/"test_r23d2b1d_guarded_lifecycle.py",
    ROOT/"tests"/"test_r23d2b1d_supervisor_contract.py",
)

def load_tests(loader, standard_tests, pattern):
    suite=unittest.TestSuite()
    for idx,path in enumerate(CASES):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("R23D2B1D_REQUIRED_TESTS_MISSING")
        spec=importlib.util.spec_from_file_location("r23d2b1d_phase_"+str(idx),path)
        if spec is None or spec.loader is None:
            raise RuntimeError("R23D2B1D_TEST_IMPORT_FAILED")
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
