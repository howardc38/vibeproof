"""`detectors/design_pins.py::pinned_docs` -- which documents it can see.

    python3 -m unittest tests.test_one_grammar_for_a_pin -v

The detector decides whether a `design-pins` claim is raised at all, so a
document it cannot see is a document nothing asks about. Its own comment said
it matched with the same expression as the checker; it carried a second one,
stricter by a `::symbol`, and a pin naming a file and no symbol fell through
the gap. The checker has always accepted those -- there is a fixture pair for
them -- and no claim ever reached it.

The four shapes are put separately, because "sees more" and "still refuses the
right things" fail in opposite directions and one case cannot hold both.

All of them fail against 93471f6.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "v4_design_pins_detector", ROOT / "detectors" / "design_pins.py")
detector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detector)


def _repo(case, **docs):
    """A real repository with a real index.

    `pinned_docs` opens with `git ls-files`, so a directory that is not a work
    tree answers nothing and every case below would pass without asking
    anything."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    for name, body in docs.items():
        # `DESIGN_md` -> `DESIGN.md`. Keyword arguments cannot carry a dot.
        p = tmp / (name.rsplit("_", 1)[0] + "." + name.rsplit("_", 1)[1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                    "add", "-A"], cwd=tmp, capture_output=True)
    return tmp


class WhatTheDetectorCanSee(unittest.TestCase):
    def test_a_pin_that_names_a_file_and_no_symbol_is_seen(self):
        """The one that was invisible. `checkers/design_pins.py` accepts it --
        `tests/fixtures/design_pins/green/a_file_only_pin_that_holds` exists
        for it -- and no claim ever arrived to be checked."""
        root = _repo(self, DESIGN_md="<!-- pinned: src/core.py -->\n")
        self.assertEqual(detector.pinned_docs(root), ["DESIGN.md"])

    def test_a_pin_that_names_a_symbol_is_still_seen(self):
        """The control. Widening what is seen must not narrow it elsewhere."""
        root = _repo(self, DESIGN_md="<!-- pinned: src/core.py::run -->\n")
        self.assertEqual(detector.pinned_docs(root), ["DESIGN.md"])

    def test_a_marker_being_quoted_is_not_a_pin(self):
        """`docs/README.md` shows the marker inside an inline code span to
        explain the convention. Matching on the marker alone would raise a
        claim about a page that pins nothing -- which is why this reads through
        `spec_pins.pins`, whose `prose` drops quoted markers, and not through
        `spec_pins.MARKER`."""
        root = _repo(self, READ_md=(
            "Every `<!-- pinned: src/core.py::run -->` in this document is a\n"
            "claim about a symbol.\n"))
        self.assertEqual(detector.pinned_docs(root), [])

    def test_an_empty_marker_is_not_a_pin(self):
        """`<!-- pinned: -->` names nothing, so there is nothing to resolve and
        nothing to ask about. The owner reports it as itself rather than as a
        pin, and this is the half of that decision the detector owes."""
        root = _repo(self, DESIGN_md="<!-- pinned: -->\n")
        self.assertEqual(detector.pinned_docs(root), [])

    def test_a_document_with_no_marker_at_all_is_not_listed(self):
        """The floor. A detector that listed every markdown would raise a
        claim in every repo, which is the state this one exists to avoid."""
        root = _repo(self, NOTES_md="No pins here, just prose.\n")
        self.assertEqual(detector.pinned_docs(root), [])


class ThePathsTheSpecNames(unittest.TestCase):
    """The other half of the same finding, and the half already repaired.

    `spec_coverage.PATH` did not match `hooks/`, `bin/` or `tools/`, so the
    files SPEC names there resolved to nothing and renaming one broke no
    check -- against a README that promises the opposite. Entered rather than
    read: `check` is the symbol, and this asks it."""

    def test_a_hook_the_spec_names_and_the_repo_does_not_have_is_found(self):
        from kernel import spec_coverage

        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        before = spec_coverage.check(ROOT, spec)
        renamed = spec.replace("hooks/stop_gate.py",
                               "hooks/stop_gate_RENAMED.py")
        self.assertNotEqual(renamed, spec, "the spec stopped naming that hook")
        after = spec_coverage.check(ROOT, renamed)
        new = [p for p in after if p not in before]
        self.assertTrue(any("hooks/stop_gate_RENAMED.py" in p for p in new),
                        f"renaming a hook in the spec broke no check: {new}")

    def test_and_the_repo_as_it_stands_resolves(self):
        """The control. A check that fires on the tree as it is would make the
        case above pass whatever `PATH` matched."""
        from kernel import spec_coverage

        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        self.assertEqual(spec_coverage.check(ROOT, spec), [])


class WhenGitCannotBeRead(unittest.TestCase):
    """The list is built from `git ls-files`, and the return code is inspected
    -- checked here because the same silence was found twice in this repo this
    week and repaired in `checkers/test.py` and `kernel/spec_coverage.py`."""

    def test_a_directory_that_is_not_a_work_tree_lists_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "DESIGN.md").write_text("<!-- pinned: src/core.py -->\n",
                                       encoding="utf-8")
        self.assertEqual(detector.pinned_docs(tmp), [])


if __name__ == "__main__":
    unittest.main()
