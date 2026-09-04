"""`test-expectation`: the expectation moved and the code did not.

Built end to end through the `checker-author` path -- analysis, detector,
checker, three fixture colours, both gates -- because "the agent exists" and
"the path works" are different claims and only one of them had been tested.

The rule is layer 1's "Never edit the expectation to match the result", which
had no checker. `test-weakened` catches deleting a test; changing what one
expects is cheaper and leaves the suite the same size.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import test_expectation as expect          # noqa: E402
from kernel.analysis import test_expectation_diff as diff       # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class Literals(unittest.TestCase):
    def test_a_changed_number_is_a_changed_expectation(self):
        self.assertEqual(
            [n for n, _, _ in expect.changed("def test_a():\n    assert f() == 3\n",
                                             "def test_a():\n    assert f() == 4\n")],
            ["test_a"])

    def test_reordering_assertions_is_not(self):
        # A checker that calls formatting a changed expectation spends its
        # credibility on formatting.
        self.assertEqual(expect.changed(
            "def test_a():\n    assert a() == 1\n    assert b() == 2\n",
            "def test_a():\n    assert b() == 2\n    assert a() == 1\n"), [])

    def test_moving_the_literal_out_of_the_assertion_still_counts(self):
        # The bypass a rule-aware author reaches for first. The assertion no
        # longer carries an expectation, which is itself the change.
        self.assertEqual(
            [n for n, _, _ in expect.changed(
                "def test_a():\n    assert f() == 3\n",
                "EXPECTED = 4\n\n\ndef test_a():\n    assert f() == EXPECTED\n")],
            ["test_a"])

    def test_widening_to_a_type_check_counts(self):
        self.assertEqual(
            [n for n, _, _ in expect.changed(
                "def test_a():\n    assert f() == 3\n",
                "def test_a():\n    assert isinstance(f(), int)\n")],
            ["test_a"])

    def test_an_added_test_is_not_this_rule(self):
        self.assertEqual(expect.changed(
            "def test_a():\n    assert f() == 3\n",
            "def test_a():\n    assert f() == 3\n\n\ndef test_b():\n    assert g() == 9\n"),
            [])

    def test_an_unparsable_side_says_nothing(self):
        self.assertEqual(expect.changed("def test_a(:\n", "def test_a():\n    pass\n"), [])


class TheConjunct(unittest.TestCase):
    """The whole rule is "and no non-test source changed"."""

    def _repo(self, before, now, extra=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        (tmp / "test_thing.py").write_text(before)
        for rel, body in (extra or {}).items():
            (tmp / rel).write_text(body[0])
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        (tmp / "test_thing.py").write_text(now)
        for rel, body in (extra or {}).items():
            (tmp / rel).write_text(body[1])
        return tmp

    def test_the_expectation_alone_is_a_finding(self):
        tmp = self._repo("def test_a():\n    assert f() == 3\n",
                         "def test_a():\n    assert f() == 4\n")
        self.assertEqual(len(diff.scan(tmp, "HEAD")), 1)

    def test_the_code_moving_too_is_ordinary_work(self):
        tmp = self._repo("def test_a():\n    assert f() == 3\n",
                         "def test_a():\n    assert f() == 4\n",
                         {"app.py": ("def f():\n    return 3\n",
                                     "def f():\n    return 4\n")})
        self.assertEqual(diff.scan(tmp, "HEAD"), [])

    def test_a_non_python_change_is_not_cover(self):
        tmp = self._repo("def test_a():\n    assert f() == 3\n",
                         "def test_a():\n    assert f() == 4\n",
                         {"notes.md": ("# a\n", "# b\n")})
        self.assertEqual(len(diff.scan(tmp, "HEAD")), 1)


class Registered(unittest.TestCase):
    def test_the_kind_is_wired_end_to_end(self):
        kinds = json.loads((REPO / ".v4/claim_kinds.json").read_text())
        reg = json.loads((REPO / ".v4/checkers.json").read_text())
        det = json.loads((REPO / ".v4/detectors.json").read_text())
        self.assertIn("test-expectation", kinds)
        self.assertIn("test-expectation", reg)
        self.assertIn("test_expectation.py", det)
        # Engagement, because "was the old expectation wrong, and where" has no
        # mechanical answer.
        self.assertTrue(kinds["test-expectation"]["engagement"])
        self.assertTrue(kinds["test-expectation"]["rule"][0]["text"].strip())


if __name__ == "__main__":
    unittest.main()
