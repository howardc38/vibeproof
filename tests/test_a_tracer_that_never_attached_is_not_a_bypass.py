"""`_run_traced` distinguished two things and `verify` collapsed them again.

    python3 -m unittest tests.test_a_tracer_that_never_attached_is_not_a_bypass -v

The tracer is installed through a ``sitecustomize.py`` on ``PYTHONPATH`` and
dumps its hits at ``atexit``. A command that is not Python, a runner killed
before exit, or a suite that execs into a subprocess all finish with no trace
file, and none of them says anything about whether the code ran.

``_run_traced`` returns ``None`` for that and ``False`` for "the tracer ran and
never entered the symbol", and its comment gives the reason in as many words:
reading the first as the second "makes ``verify`` accuse the worker of the one
bypass this mechanism exists to refuse, when the truth is that the tracer never
attached".  ``verify`` then wrote ``if not executed:`` and appended exactly
that accusation for ``None`` -- so the distinction the repair introduced had no
reader, and the worker on the other end had a sentence about reading source
text to argue their way out of.

Both answers are still short of proof: ``symbol_executed`` is ``None``,
``as_dict()`` reports ``null``, and ``ok`` is false either way. Unknown is not
a pass. It is just not an allegation.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import redgreen  # noqa: E402

ACCUSATION = "never executed"


def _repo(test) -> tuple:
    """A one-commit git repo, and its HEAD."""
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)],
                                           capture_output=True))
    root = tmp / "r"
    root.mkdir()
    (root / "app.py").write_text("def handle():\n    return 1\n")
    (root / "t_app.py").write_text(
        "import app\n\n\ndef test_it():\n    assert app.handle() == 1\n")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=root, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    return root, head


class ACommandTheTracerCannotSeeInto(unittest.TestCase):
    """`/usr/bin/true` is not Python, so no trace file is ever written."""

    def setUp(self):
        root, head = _repo(self)
        self.res = redgreen.verify(
            root, command=["/usr/bin/true"], test_path="t_app.py",
            target_file="app.py", target_symbol="handle",
            parent_commit=head, timeout=60)

    def test_the_worker_is_not_accused(self):
        for note in self.res.notes:
            self.assertNotIn(
                ACCUSATION, note,
                "the tracer never attached, and that is not evidence that the "
                "symbol was skipped")

    def test_the_unknown_is_said_out_loud(self):
        self.assertTrue(
            any("nothing observed whether" in n for n in self.res.notes),
            f"no note explains the missing observation: {self.res.notes}")

    def test_unknown_is_still_not_proof(self):
        self.assertIsNone(self.res.symbol_executed)
        self.assertIsNone(self.res.as_dict()["symbol_executed"])
        self.assertFalse(self.res.ok)


class ATestThatRanAndNeverEnteredTheSymbol(unittest.TestCase):
    """The other answer, which the accusation is for and still reaches."""

    def test_the_sentence_is_still_there_for_the_case_it_was_written_for(self):
        root, head = _repo(self)
        (root / "t_app.py").write_text(
            "import app\n\n\ndef test_it():\n    assert app.__name__ == 'app'\n")
        res = redgreen.verify(
            root, command=[sys.executable, "-m", "unittest", "-q", "t_app"],
            test_path="t_app.py", target_file="app.py", target_symbol="handle",
            parent_commit=head, timeout=120)
        self.assertIs(False, res.symbol_executed)
        self.assertTrue(any(ACCUSATION in n for n in res.notes),
                        f"the tracer ran and saw nothing: {res.notes}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
