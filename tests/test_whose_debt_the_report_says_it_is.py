"""`derive`'s inherited-debt line counted files, and the cost lands per symbol.

    python3 -m unittest tests.test_whose_debt_the_report_says_it_is -v

The line exists because a scope wider than the work pulls in claims about code
the task never wrote, and, as the comment beside it says, "the only thing
missing was being able to see it before paying for it". It compared a claim's
`file` against `changed_since`, so a file with one added line was this task's
own -- along with everything that file had ever earned.

Measured on the reference adopter, 2026-08-26: `app/chat/poller/poller.py`
took an addition, 26 claims came back, 23 of them on symbols the diff never
entered, and this line printed nothing at all.

All of it fails against the commit before this file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli  # noqa: E402
from kernel.analysis import subject_files, symbols  # noqa: E402

TWO_FUNCTIONS = '''\
def touched():
    return 1


def untouched():
    return 2
'''


def _claim(file, symbol):
    return {"file": file, "symbol": symbol}


class WhichLinesThisChangeWrote(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=self.root, capture_output=True)
        (self.root / "m.py").write_text(TWO_FUNCTIONS)
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "base"], cwd=self.root, capture_output=True)

    def test_only_the_edited_function_comes_back(self):
        (self.root / "m.py").write_text(
            TWO_FUNCTIONS.replace("return 1", "return 11"))
        lines = subject_files.changed_lines(self.root, "HEAD")
        self.assertIn("m.py", lines)
        owners = symbols.owner_of_line(self.root / "m.py", lines["m.py"])
        self.assertEqual(set(owners.values()), {"touched"})

    def test_a_pure_deletion_claims_no_line_in_the_new_file(self):
        """`@@ -4,2 +3,0 @@` names a line that is not there. Counting it would
        hand the change whichever symbol happens to sit at that number."""
        (self.root / "m.py").write_text("def touched():\n    return 1\n")
        lines = subject_files.changed_lines(self.root, "HEAD")
        owners = symbols.owner_of_line(self.root / "m.py", lines["m.py"])
        self.assertNotIn("untouched", set(owners.values()))

    def test_a_language_with_no_span_reader_says_so(self):
        (self.root / "x.json").write_text("{}\n")
        self.assertIsNone(symbols.owner_of_line(self.root / "x.json", [1]))


class WhoseDebtItIs(unittest.TestCase):
    """The classification, without a ledger in the way."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "m.py").write_text(TWO_FUNCTIONS)

    def call(self, rows, changed, lines):
        return cli.inherited_claims(self.root, rows, changed, lines)

    LINES = {"m.py": {1, 2}}          # inside `touched`

    def test_a_claim_on_a_symbol_the_diff_never_entered_is_inherited(self):
        rows = [_claim("m.py", "touched"), _claim("m.py", "untouched")]
        on_file, on_symbol = self.call(rows, {"m.py"}, self.LINES)
        self.assertEqual(on_file, [], "the file itself was changed")
        self.assertEqual([c["symbol"] for c in on_symbol], ["untouched"])

    def test_a_claim_on_an_untouched_file_is_still_counted(self):
        """The half that already worked, kept: a repair that traded one for the
        other would report the same total and mean something else."""
        rows = [_claim("other.py", "whatever")]
        on_file, on_symbol = self.call(rows, {"m.py"}, self.LINES)
        self.assertEqual([c["file"] for c in on_file], ["other.py"])
        self.assertEqual(on_symbol, [])

    def test_an_unreadable_diff_reports_nothing_as_untouched(self):
        """`None` is not an empty diff. Reporting a symbol as inherited on a
        guess is the error this line exists to prevent."""
        rows = [_claim("m.py", "untouched")]
        _on_file, on_symbol = self.call(rows, {"m.py"}, None)
        self.assertEqual(on_symbol, [])

    def test_a_file_in_a_language_with_no_span_reader_is_left_alone(self):
        (self.root / "c.json").write_text("{}\n")
        rows = [_claim("c.json", "whatever")]
        _on_file, on_symbol = self.call(rows, {"c.json"}, {"c.json": {1}})
        self.assertEqual(on_symbol, [],
                         "a language this cannot read gets no verdict, not a "
                         "verdict against the worker")

    def test_a_claim_with_no_symbol_is_not_guessed_at(self):
        rows = [_claim("m.py", "")]
        _on_file, on_symbol = self.call(rows, {"m.py"}, self.LINES)
        self.assertEqual(on_symbol, [])


if __name__ == "__main__":
    unittest.main()
