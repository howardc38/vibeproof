"""Blind spots, asserted rather than promised.

    python3 -m unittest tests.test_the_gaps_that_are_written_down -v

SPEC's fixture rule: a gap a checker does not catch goes in `known_miss/` with a
test asserting it returns 0. The rule exists because a gap described in prose
and a gap nobody noticed read the same way six months later, and because a
comment claiming a fixture exists tells the next reader to stop looking.

That is not hypothetical here. `test_expectation.py`'s Rust header said the 409
`insta` snapshot assertions it skips were covered by "a `known_miss/` fixture",
and `tests/fixtures/test_expectation/` held no such directory. A reviewer found
it by going to look.
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

FIXTURES = ROOT / "tests" / "fixtures"


def run_case(test, checker: Path, case_dir: Path) -> tuple:
    """`(exit code, stdout)` from running `checker` against a fixture case.

    The case is copied, committed at its `parent_content`, and then restored --
    the same shape `register` builds, so a case that holds here holds there.
    """
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    work = tmp / "case"
    shutil.copytree(case_dir, work)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=work, capture_output=True)
    spec = json.loads((work / ".v4" / "fixture.json").read_text())
    parent = spec.get("parent_content") or {}
    restore = {r: (work / r).read_text() for r in parent}
    for rel, body in parent.items():
        (work / rel).write_text(body)
    subprocess.run(["git", "add", *spec.get("committed", [])], cwd=work,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=work,
                   capture_output=True)
    for rel, body in restore.items():
        (work / rel).write_text(body)
    subject = work / "subject.json"
    subject.write_text(json.dumps({
        "repo_root": str(work), "diff_base": "HEAD",
        "subject_refs": spec.get("subject_refs", [])}))
    r = subprocess.run([sys.executable, str(checker), "--subject", str(subject)],
                       capture_output=True, text=True, cwd=work)
    return r.returncode, r.stdout


class AnInstaSnapshotKeepsItsExpectationElsewhere(unittest.TestCase):
    """`test-expectation` reads the source of a test. An `insta` snapshot's
    expected value is in a `.snap` file beside it, so moving the expectation
    never touches what this reads.

    409 of the corpus's assertions are written this way, all in one repo. The
    fixture edits the *input* of a snapshot test and asserts the checker stays
    quiet -- which is correct, and is the whole miss: it would stay just as
    quiet if the `.snap` had moved instead.
    """

    CASE = (FIXTURES / "test_expectation" / "known_miss"
            / "rs_insta_snapshot_lives_in_another_file")

    def test_the_fixture_is_there(self):
        """A comment once said this existed while it did not."""
        self.assertTrue(self.CASE.is_dir(), f"{self.CASE} is missing")

    def test_it_returns_zero(self):
        code, out = run_case(self, ROOT / "checkers" / "test_expectation.py",
                             self.CASE)
        self.assertEqual(
            code, 0,
            f"a documented blind spot that started firing: {out.strip()[:200]}\n"
            f"If the rule really grew to see snapshot expectations, move this "
            f"case out of known_miss/ rather than editing it quiet.")


class EveryKnownMissCaseIsRunAndReturnsZero(unittest.TestCase):
    """The rule, applied to all of them rather than to the ones somebody
    remembered.

    This started as a check that some test file mentioned each case by name,
    which is a proxy, and the proxy immediately found two cases nothing
    asserted -- `lint/known_miss/second_site_same_file` and
    `untyped_collaborator`, both named in `structural_lint.py`'s docstring and
    neither run by anything. Naming a gap in a docstring is the prose this
    directory replaces.

    So it runs them. The checker for each case comes from `.v4/checkers.json`,
    which is the registry that already maps a fixtures directory to the program
    that owns it -- so a new `known_miss/` case is covered the day it is added,
    without anybody adding it to a list here.
    """

    def _cases(self):
        reg = json.loads((ROOT / ".v4" / "checkers.json").read_text())
        reg = reg.get("checkers", reg)
        by_dir = {Path(v["fixtures"]).name: v["path"]
                  for v in reg.values() if v.get("fixtures")}
        out = []
        for case in sorted(FIXTURES.glob("*/known_miss/*")):
            if not case.is_dir() or not (case / ".v4" / "fixture.json").is_file():
                continue
            checker = by_dir.get(case.parent.parent.name)
            if checker:
                out.append((case, ROOT / checker))
        return out

    def test_there_are_some(self):
        self.assertTrue(self._cases(), "no known_miss cases found at all")

    def test_none_of_them_has_started_firing(self):
        for case, checker in self._cases():
            with self.subTest(case=case.relative_to(FIXTURES).as_posix()):
                code, out = run_case(self, checker, case)
                self.assertEqual(
                    code, 0,
                    f"{case.name} is a documented blind spot and it just "
                    f"fired: {out.strip()[:180]}\nIf the rule really grew to "
                    f"see this, move the case out of known_miss/ rather than "
                    f"editing it quiet.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
