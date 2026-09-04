"""Repairs in `kernel/register.py`, called directly.

    python3 -m unittest tests.test_the_gate_that_grades_every_checker -v

This is the gate a checker has to pass before its verdict counts, so a defect
here is a defect in every verdict downstream. Each of these was one.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import register  # noqa: E402


def _set(case, cases):
    """A fixture set on disk.  `cases` is {colour: {name: source}}."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for colour, members in cases.items():
        (tmp / colour).mkdir(parents=True)
        for name, src in members.items():
            (tmp / colour / name).write_text(src)
    return tmp


class ABypassIsNotACopyOfARedCase(unittest.TestCase):
    """The gate counted bypass cases and never asked whether they were bypasses.

    A copy of a red payload passes because red payloads fail: it proves the
    checker still catches what it already caught, which is what `red/` is for.
    The agent that writes the checker writes the fixtures it is graded by, so
    this is the one place the ritual is cheapest to perform.
    """

    CHECKER = ("#!/usr/bin/env python3\n"
               "import argparse, json, sys\n"
               "from pathlib import Path\n"
               "p = argparse.ArgumentParser()\n"
               "p.add_argument('--subject', required=True)\n"
               "p.add_argument('--facts'); p.add_argument('--out')\n"
               "a = p.parse_args()\n"
               "s = json.loads(Path(a.subject).read_text())\n"
               "root = Path(s['repo_root'])\n"
               "bad = any('BOOM' in (root / r['path']).read_text()\n"
               "          for r in s.get('subject_refs', []))\n"
               "sys.exit(1 if bad else 0)\n")

    def _checker(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "c.py"
        path.write_text(self.CHECKER)
        return path

    def _cases(self, bypass_body):
        red = {f"r{i}.py": f"# BOOM {i}\n" for i in range(5)}
        green = {f"g{i}.py": f"# fine {i}\n" for i in range(5)}
        bypass = {f"b{i}.py": bypass_body(i) for i in range(3)}
        return {"red": red, "green": green, "bypass": bypass}

    def test_a_bypass_copied_from_a_red_case_is_refused(self):
        fixtures = _set(self, self._cases(lambda i: "# BOOM 0\n"))
        ok, report = register.verify_checker(
            repo_root=fixtures, checker_path=self._checker(),
            fixtures_dir=fixtures, kind="k", min_cases=5)
        self.assertFalse(ok)
        self.assertTrue(any("byte-identical" in f for f in report["failures"]),
                        report["failures"])

    def test_a_bypass_that_is_its_own_attempt_is_not(self):
        """The control: refusing every bypass would pass the test above."""
        fixtures = _set(self, self._cases(lambda i: f"# BOOM rewritten {i}\n"))
        ok, report = register.verify_checker(
            repo_root=fixtures, checker_path=self._checker(),
            fixtures_dir=fixtures, kind="k", min_cases=5)
        self.assertTrue(ok, report["failures"])


class TheFailureLineSaysWhatTheGateWanted(unittest.TestCase):
    """For a bypass case the verb was inverted.

    `bypass/` wants exit 1, and the message said "should have passed this
    (want 1)" -- the sentence and the number contradicting each other, in the
    line a reader acts on, about the colour that carries the most information.
    """

    def test_a_bypass_that_walked_through_is_reported_as_a_failure_to_fail(self):
        checker = Path(tempfile.mkdtemp()) / "c.py"
        checker.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, checker.parent, ignore_errors=True)
        checker.write_text("#!/usr/bin/env python3\n"
                           "import argparse, sys\n"
                           "p = argparse.ArgumentParser()\n"
                           "p.add_argument('--subject'); p.add_argument('--facts')\n"
                           "p.add_argument('--out'); p.parse_args()\n"
                           "sys.exit(0)\n")           # always passes
        fixtures = _set(self, {
            "red": {f"r{i}.py": f"# {i}\n" for i in range(5)},
            "green": {f"g{i}.py": f"# {i}\n" for i in range(5)},
            "bypass": {f"b{i}.py": f"# evasion {i}\n" for i in range(3)}})
        _ok, report = register.verify_checker(
            repo_root=fixtures, checker_path=checker, fixtures_dir=fixtures,
            kind="k", min_cases=5)
        bypass_lines = [f for f in report["failures"] if f.startswith("bypass/")]
        self.assertTrue(bypass_lines, report["failures"])
        for line in bypass_lines:
            self.assertIn("should have failed on this", line)
            self.assertNotIn("should have passed this", line)


class ACaseThatCannotBeReadIsNotACaseWithoutOne(unittest.TestCase):
    """`case_facts` swallowed a `.v4/facts.json` that did not parse.

    So the case ran against the set-level vocabulary its author did not write,
    and the gate recorded that verdict as legitimate -- silent in both
    directions: a red case can go green and a green case red, for a reason
    that has nothing to do with the checker.
    """

    def test_a_case_table_is_used(self):
        fixtures = _set(self, {"red": {}})
        d = fixtures / "red" / "case" / ".v4"
        d.mkdir(parents=True)
        (d / "facts.json").write_text(json.dumps({"ui_globs": ["own/**"]}))
        got = register.case_facts(fixtures, "red", "case", {"ui_globs": ["set/**"]})
        self.assertEqual(got["ui_globs"], ["own/**"])

    def test_one_that_does_not_parse_raises_rather_than_falling_back(self):
        fixtures = _set(self, {"red": {}})
        d = fixtures / "red" / "case" / ".v4"
        d.mkdir(parents=True)
        (d / "facts.json").write_text("{ not json")
        with self.assertRaises(ValueError):
            register.case_facts(fixtures, "red", "case", {"ui_globs": ["set/**"]})

    def test_a_case_with_no_table_uses_the_sets(self):
        fixtures = _set(self, {"red": {}})
        (fixtures / "red" / "case").mkdir(parents=True)
        got = register.case_facts(fixtures, "red", "case", {"ui_globs": ["set/**"]})
        self.assertEqual(got["ui_globs"], ["set/**"])


class OneSetupForAllThreeGates(unittest.TestCase):
    """`_Case` exists because the mini-repo setup was written three times.

    `verify_checker` learned mini-repo cases and `verify_detector` did not;
    `verify_checker` learned `parent_content` and `verify_detector` did not.
    Both times every red case failed for a reason unrelated to the code under
    test -- the case was simply not set up.
    """

    DETECTOR = ("#!/usr/bin/env python3\n"
                "import argparse, json, subprocess, sys\n"
                "from pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--subject', required=True)\n"
                "p.add_argument('--facts'); p.add_argument('--out')\n"
                "a = p.parse_args()\n"
                "s = json.loads(Path(a.subject).read_text())\n"
                "root = s['repo_root']\n"
                # Only a case with a parent commit can answer this.
                "d = subprocess.run(['git', 'diff', '--name-only', 'HEAD'],\n"
                "                   cwd=root, capture_output=True, text=True)\n"
                "if d.stdout.strip():\n"
                "    print('V4-CLAIM: kind=k')\n"
                "sys.exit(0)\n")

    def _case(self, parent):
        fixtures = _set(self, {"red": {}, "green": {}})
        for colour, body in (("red", parent), ("green", None)):
            for i in range(3):
                d = fixtures / colour / f"c{i}"
                (d / ".v4").mkdir(parents=True)
                (d / ".v4" / "config.json").write_text('{"test_command": "true"}')
                spec = {"committed": ["mod.py"]}
                if body is not None:
                    spec["parent_content"] = {"mod.py": body}
                (d / ".v4" / "fixture.json").write_text(json.dumps(spec))
                (d / "mod.py").write_text("def f(a, b):\n    return a\n")
        return fixtures

    def test_the_detector_gate_sets_up_a_parent_the_same_way(self):
        """A red case whose whole point is the diff, run through
        `verify_detector`. Without the shared setup there is no parent commit,
        `git diff HEAD` is empty, and every red case fails for a reason that
        has nothing to do with the detector."""
        det = Path(tempfile.mkdtemp()) / "d.py"
        det.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, det.parent, ignore_errors=True)
        det.write_text(self.DETECTOR)
        fixtures = self._case("def f(a):\n    return a\n")
        ok, report = register.verify_detector(
            repo_root=fixtures, detector_path=det, fixtures_dir=fixtures,
            min_cases=3)
        self.assertTrue(ok, report["failures"])

    def test_and_a_case_with_no_parent_content_has_nothing_to_diff(self):
        """The control: a gate that reported a claim for every case would pass
        the test above."""
        det = Path(tempfile.mkdtemp()) / "d.py"
        det.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, det.parent, ignore_errors=True)
        det.write_text(self.DETECTOR)
        fixtures = self._case(None)
        ok, _report = register.verify_detector(
            repo_root=fixtures, detector_path=det, fixtures_dir=fixtures,
            min_cases=3)
        self.assertFalse(ok, "no parent, no diff, and the red cases passed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
