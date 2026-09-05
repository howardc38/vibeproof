"""The public demonstration must run real checks in both directions."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("first_proof", ROOT / "examples/first-proof/run.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class PublicFirstProof(unittest.TestCase):
    def test_failure_repair_and_limits_are_executable(self):
        result = demo.demonstrate(quiet=True)
        steps = {step["id"]: step for step in result["steps"]}
        self.assertEqual(steps["green_suite"]["exit_code"], 0)
        self.assertEqual(steps["wrong_result"]["output"], "120")
        self.assertEqual(steps["unexecuted"]["exit_code"], 1)
        self.assertEqual(steps["regression_red"]["exit_code"], 1)
        self.assertEqual(steps["repair_verified"]["exit_code"], 0)
        self.assertEqual(steps["correct_result"]["output"], "80")
        self.assertEqual(steps["limit_import_only"]["exit_code"], 0)
        self.assertEqual(steps["limit_unrelated_closure"]["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
