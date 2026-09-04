"""`lint` comes back with three rules, and the fourth is an asserted blind spot.

    python3 -m unittest tests.test_lint_keeps_three_rules -v

The kind was cut on a coverage-class argument: `v4 coverage` maps `lint` to
PO-4, `test` maps there too, so removing it lost no class its only mechanism.
That is true at the class level and wrong at the rule level. Measured across the
113-task eval, from the per-task ledgers:

    LINT-IMPORT-CYCLE            14 FAIL claims across 14 tasks
    LINT-PRIVATE-IMPORT           4 across 4
    LINT-PRIVATE-MODULE-ACCESS    2 across 2
    LINT-CONFIG-DUAL-TRUTH        0

and nothing else asks those first three in Python. `layer-boundary` is the
answer that gets offered, and it raised **zero** claims in all 113 tasks --
it is `applies_to: declared` and no repo declared `.v4/layers.json`, so it
cannot be the mechanism that took over.

The fourth rule is dropped for the reason the whole trim exists. Its zero is
not a gate that never opened: it needs no declared fact, it finds config
modules by name, and it ran inside all 178 executions this kind performed.
`bundle-secret`'s zero was the other shape -- a detector whose gate was shut
before it looked -- and telling the two apart is the point.

SPEC.md's fixture rule: a gap this checker does not catch goes in `known_miss/`
with a test asserting it returns 0. An executable known gap beats a sentence of
prose.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHECKER = ROOT / "checkers" / "structural_lint.py"
FIXTURES = ROOT / "tests" / "fixtures" / "lint"

#: The two cases the dropped rule used to catch. Named, so that restoring
#: `LINT-CONFIG-DUAL-TRUTH` is a change that moves these back rather than a
#: change that quietly starts failing on fixtures nobody remembered.
DROPPED_RULE_CASES = (
    "a_config_value_written_again_beside_its_name",
    "the_same_value_on_a_positional_only_parameter",
)


def _checker_module():
    spec = importlib.util.spec_from_file_location("_lint_probe", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TheRuleSetIsThreeAndSaysSo(unittest.TestCase):
    def test_the_checker_names_exactly_the_three_it_judges(self):
        from kernel.analysis import structural_lint as lint
        self.assertEqual(
            _checker_module().RULES_HERE,
            frozenset({lint.PRIVATE_IMPORT, lint.PRIVATE_MODULE_ACCESS,
                       lint.IMPORT_CYCLE}))

    def test_the_dropped_one_is_not_among_them(self):
        from kernel.analysis import structural_lint as lint
        self.assertNotIn(lint.CONFIG_DUAL_TRUTH, _checker_module().RULES_HERE)

    def test_the_analysis_still_knows_the_fourth(self):
        """Dropped from this checker, not deleted from the module.

        Another rule may want it, and a deletion there would be a decision
        made in the wrong place.
        """
        from kernel.analysis import structural_lint as lint
        self.assertIn(lint.CONFIG_DUAL_TRUTH, lint.RULES)


class TheDroppedRuleIsAnAssertedBlindSpot(unittest.TestCase):
    """`known_miss/` plus a test that it returns 0."""

    def _run(self, case_dir: Path) -> int:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        work = tmp / "case"
        shutil.copytree(case_dir, work)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=work, capture_output=True)
        (work / ".v4").mkdir(exist_ok=True)
        if not (work / ".v4" / "config.json").is_file():
            (work / ".v4" / "config.json").write_text(json.dumps(
                {"test_command": "true", "policy": "allow_accepted_risk"}))
        subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "in"], cwd=work,
                       capture_output=True)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work,
                              capture_output=True, text=True).stdout.strip()
        subject = tmp / "subject.json"
        subject.write_text(json.dumps({"repo_root": str(work),
                                       "diff_base": base, "subject_refs": []}))
        r = subprocess.run(
            [sys.executable, str(CHECKER), "--subject", str(subject)],
            capture_output=True, text=True, cwd=ROOT)
        return r.returncode

    def test_every_case_the_dropped_rule_covered_now_returns_zero(self):
        for name in DROPPED_RULE_CASES:
            case = FIXTURES / "known_miss" / name
            self.assertTrue(case.is_dir(), f"{name} moved to known_miss")
            self.assertEqual(
                self._run(case), 0,
                f"{name} is a gap this checker no longer covers, asserted so "
                f"it cannot rot into an unexamined belief")


if __name__ == "__main__":
    unittest.main(verbosity=2)
