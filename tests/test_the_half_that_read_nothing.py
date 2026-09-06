"""`kernel/spec_coverage.py::dead_references` -- what it returns when it read
nothing.

    python3 -m unittest tests.test_the_half_that_read_nothing -v

The function has two halves that each start with a `git` call. One learns which
documents this project deleted, so a pointer at one of them can be called dead;
the other reads every tracked `.py` so a docstring citing a section that does
not exist can be found. Both took `stdout` without looking at the exit code,
and both `except` branches were silent, so a git that exits non-zero with empty
output left each set empty -- and an empty set is the same list a clean repo
produces.

The two halves are put separately: repairing one and leaving the other would
pass a single case that only asked whether the function said anything.

All of them fail against 143cec2.
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

from kernel.spec_coverage import dead_references  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                           *args], cwd=root, capture_output=True, text=True)


class _Docs(unittest.TestCase):
    def _tree(self, *, git: bool):
        """A docs/ tree, in a real repository or in a bare directory.

        The bare one is not a contrivance: `git ls-files` outside a work tree
        exits 128 with nothing on stdout, which is exactly the shape both
        halves treated as "read it, found nothing"."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "docs").mkdir()
        (tmp / "docs" / "SPEC.md").write_text(
            "# S\n\n## 1 One\n\ntext\n\n## 2 Two\n\ntext\n", encoding="utf-8")
        (tmp / "docs" / "README.md").write_text(
            "See docs/SPEC.md §1 for the contract.\n", encoding="utf-8")
        (tmp / "mod.py").write_text(
            '"""A module.\n\nWritten against docs/SPEC.md §1.\n"""\n'
            "def helper():\n    return 1\n", encoding="utf-8")
        if git:
            _git(tmp, "init", "-q")
            _git(tmp, "add", "-A")
            _git(tmp, "commit", "-qm", "base")
        return tmp


class WhenGitCannotBeRead(_Docs):
    def test_the_deleted_document_half_says_it_did_not_run(self):
        problems = dead_references(self._tree(git=False))
        self.assertTrue(
            any("deleted-document half" in p for p in problems),
            f"a clean list from a check that read nothing: {problems}")

    def test_the_source_citation_half_says_it_did_not_run(self):
        """Separate from the case above. Repairing one half and leaving the
        other would satisfy a single assertion that the function said
        anything at all."""
        problems = dead_references(self._tree(git=False))
        self.assertTrue(
            any("source-citation half" in p for p in problems),
            f"the half where the findings were, silent: {problems}")

    def test_and_it_carries_the_exit_code(self):
        """`128` is what a reader needs to recognise "not a work tree" rather
        than go looking for a broken document."""
        problems = dead_references(self._tree(git=False))
        self.assertTrue(any("128" in p for p in problems), problems)


class ARepositoryWithNoHistory(_Docs):
    """Between the two. `git log` exits 128 in a repository whose branch has no
    commits yet -- the same code it gives outside a work tree -- but a history
    with nothing in it has deleted no document, so an empty answer there is
    correct rather than unread. Measured: `rev-parse --verify -q HEAD` exits 1
    for this state and 128 for the other, and `ls-files` exits 0."""

    def test_an_empty_history_is_not_reported_as_unread(self):
        tmp = self._tree(git=False)
        _git(tmp, "init", "-q")
        problems = dead_references(tmp)
        self.assertEqual([p for p in problems if "deleted-document" in p], [],
                         f"a first-day repo told its docs check failed: "
                         f"{problems}")

    def test_and_neither_is_a_tree_with_nothing_tracked(self):
        """`ls-files` exits 0 with no output there, which is an answer -- no
        tracked Python -- and not a silence."""
        tmp = self._tree(git=False)
        _git(tmp, "init", "-q")
        problems = dead_references(tmp)
        self.assertEqual([p for p in problems if "source-citation" in p], [],
                         f"an empty index read as an unreadable one: "
                         f"{problems}")


class WhenGitCanBeRead(_Docs):
    """The control. A check that reports its own failure on every run has
    replaced one useless answer with another."""

    def test_a_readable_repo_reports_neither_half_as_skipped(self):
        problems = dead_references(self._tree(git=True))
        self.assertEqual([p for p in problems if "did not run" in p], [],
                         "reported as unread in a repository it could read")

    def test_and_a_real_dead_section_is_still_found(self):
        """The other control: the halves must still do their work. A docstring
        citing a section the document does not have is the finding this check
        exists for."""
        tmp = self._tree(git=True)
        (tmp / "mod.py").write_text(
            '"""A module.\n\nWritten against docs/SPEC.md §9.\n"""\n'
            "def helper():\n    return 1\n", encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "cite")
        problems = dead_references(tmp)
        self.assertTrue(any("mod.py" in p for p in problems),
                        f"the source half read nothing: {problems}")

    def test_and_a_pointer_at_a_deleted_document_is_still_found(self):
        """The first half's own work, in a repository where a document really
        was removed."""
        tmp = self._tree(git=True)
        (tmp / "docs" / "OLD.md").write_text("# old\n", encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "add old")
        (tmp / "docs" / "OLD.md").unlink()
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "remove old")
        (tmp / "docs" / "README.md").write_text(
            "See docs/OLD.md §1 for the contract.\n", encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "point at it")
        problems = dead_references(tmp)
        self.assertTrue(any("OLD.md" in p for p in problems),
                        f"the deleted-document half read nothing: {problems}")


if __name__ == "__main__":
    unittest.main()
