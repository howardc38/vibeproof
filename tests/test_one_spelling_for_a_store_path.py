"""`kernel/spec_coverage.py` -- where it looks for the three registries.

    python3 -m unittest tests.test_one_spelling_for_a_store_path -v

`kernel/layout.py` exists so the framework's store paths are spelled once, and
`kernel/config.py` re-exports them. This module imported `config` already and
still opened all three registries by hand at six sites, in three functions --
`engaged_kinds_explained`, `_reality` and `unbuilt_list_is_honest`. The same
fact was reported against `kernel/register.py` too, repaired there, and left
here.

Put behaviourally by moving what the owner says and watching the module follow.
A literal cannot follow; a name can. Nothing here reads source text, and the
counts come back from files this test wrote.

All of them fail against 17be6a2.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage as sc  # noqa: E402

#: `engagement` is what makes a kind engaged, which is what
#: `engaged_kinds_explained` reports on. Five, all engaged, none of them in the
#: table the spec text below carries.
KINDS = {"alpha": {"checker": "a", "engagement": ["say why"]},
         "beta": {"checker": "b", "engagement": ["say why"]},
         "gamma": {"checker": "c", "engagement": ["say why"]},
         "delta": {"checker": "d", "engagement": ["say why"]},
         "epsilon": {"checker": "e", "engagement": ["say why"]}}
CHECKERS = {"a": {"path": "x"}, "b": {"path": "y"}}
DETECTORS = {"one": {"path": "p"}, "two": {"path": "q"}, "three": {"path": "r"}}


def _repo(case):
    """A repo whose registries sit somewhere the literal would never look.

    `.v4/` carries decoys with different sizes, so a site still reading the
    hardcoded path answers -- and answers wrong -- rather than raising."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    (tmp / ".v4").mkdir()
    (tmp / "elsewhere").mkdir()
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({"only": {}}))
    (tmp / ".v4" / "checkers.json").write_text(json.dumps({}))
    (tmp / ".v4" / "detectors.json").write_text(json.dumps({}))
    (tmp / "elsewhere" / "claim_kinds.json").write_text(json.dumps(KINDS))
    (tmp / "elsewhere" / "checkers.json").write_text(json.dumps(CHECKERS))
    (tmp / "elsewhere" / "detectors.json").write_text(json.dumps(DETECTORS))
    return tmp


def _moved():
    """The owner, pointed somewhere else."""
    return mock.patch.multiple(
        sc,
        CLAIM_KINDS="elsewhere/claim_kinds.json",
        CHECKERS="elsewhere/checkers.json",
        DETECTORS="elsewhere/detectors.json")


class TheCountsComeFromWhereTheOwnerSays(unittest.TestCase):
    def test_reality_reads_the_registries_the_owner_names(self):
        root = _repo(self)
        with _moved():
            counts = sc._reality(root)
        self.assertEqual(counts.get("kind"), len(KINDS),
                         "kinds came from a path this module chose itself")
        self.assertEqual(counts.get("checker"), len(CHECKERS),
                         "checkers came from a path this module chose itself")

    def test_and_so_does_the_detector_count(self):
        """The third registry, in the same function and a hundred lines down --
        separate because a partial repair leaves exactly this."""
        root = _repo(self)
        with _moved():
            counts = sc._reality(root)
        self.assertEqual(counts.get("conditional detector"), len(DETECTORS))

    def test_the_decoys_are_what_a_literal_would_have_found(self):
        """The control. Without it the cases above would pass in a repo where
        both paths happened to hold the same thing."""
        root = _repo(self)
        counts = sc._reality(root)
        self.assertEqual(counts.get("kind"), 1)
        self.assertEqual(counts.get("checker"), 0)


class TheOtherTwoFunctionsAskTheSameOwner(unittest.TestCase):
    def test_engaged_kinds_explained_reads_the_registry_the_owner_names(self):
        """It reports on kinds that block work. Reading the wrong registry
        makes it report on the wrong repo's kinds, silently."""
        root = _repo(self)
        with _moved():
            moved = sc.engaged_kinds_explained(root, "## 9 Engagement\n")
        self.assertEqual(
            len(moved), len(KINDS),
            f"five engaged kinds with no row, and it named {len(moved)}")
        with mock.patch.object(sc, "CLAIM_KINDS", ".v4/claim_kinds.json"):
            here = sc.engaged_kinds_explained(root, "## 9 Engagement\n")
        self.assertEqual(
            len(here), 0,
            "the decoy holds one kind and it carries no engagement rule")

    def test_unbuilt_list_is_honest_reads_them_too(self):
        """The last two sites. It reports a name the unbuilt list calls missing
        that the registries say is built, so a registry it cannot find reads as
        a repo where nothing on that list was ever built."""
        root = _repo(self)
        (root / "docs").mkdir()
        (root / "docs" / "SPEC.md").write_text(
            "## 0 Scope\n\n<!-- unbuilt-list -->\n\n"
            "| what | why | when |\n| --- | --- | --- |\n"
            "| `alpha` | not started | later |\n"
            "| `a` | not started | later |\n\n"
            "### 1 Next\n", encoding="utf-8")
        with _moved():
            moved = sc.unbuilt_list_is_honest(root)
        self.assertEqual(
            len(moved), 2,
            f"`alpha` is a kind and `a` is a checker in the registries the "
            f"owner names, and it reported {moved}")
        with mock.patch.object(sc, "CHECKERS", ".v4/checkers.json"), \
                mock.patch.object(sc, "CLAIM_KINDS", ".v4/claim_kinds.json"):
            here = sc.unbuilt_list_is_honest(root)
        self.assertEqual(here, [],
                         "the decoys hold neither name, so nothing on the "
                         "list is built as far as they know")


class TheWholeCheckFollowsTheOwnerToo(unittest.TestCase):
    """`check` is what the checker calls and what runs all three of the
    functions above, so it is asked here rather than only its parts: a module
    that reads the right registry in a unit test and the wrong one through its
    entry has not been repaired."""

    def _spec(self, root):
        # The rest of the tree `check` reads before it gets to the registries:
        # it parses `kernel/cli.py` for the subcommand list and opens
        # `kernel/ledger.py` for the schema pin. Absent, it raises on the way
        # in and never reaches the question this case is about.
        (root / "kernel").mkdir(exist_ok=True)
        (root / "kernel" / "cli.py").write_text(
            'p0 = sub.add_parser("audit")\n', encoding="utf-8")
        (root / "kernel" / "ledger.py").write_text("SCHEMA = 1\n",
                                                   encoding="utf-8")
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs" / "SPEC.md").write_text(
            "## 0 Scope\n\n<!-- unbuilt-list -->\n\n"
            "| what | why | when |\n| --- | --- | --- |\n"
            "| `alpha` | not started | later |\n\n"
            "### 1 Next\n\nShort.\n", encoding="utf-8")
        return (root / "docs" / "SPEC.md").read_text(encoding="utf-8")

    def test_check_names_what_the_owners_registries_say_is_built(self):
        root = _repo(self)
        spec = self._spec(root)
        with _moved():
            problems = sc.check(root, spec)
        self.assertTrue(
            any("alpha" in p and "registered" in p for p in problems),
            f"`alpha` is a kind in the registry the owner names, and the "
            f"unbuilt list still calls it missing: {problems}")

    def test_and_the_decoy_registries_do_not_know_it(self):
        """The control, through the same entry: with the hardcoded paths the
        name is unknown, which is the wrong answer this repair removes."""
        root = _repo(self)
        spec = self._spec(root)
        problems = sc.check(root, spec)
        self.assertFalse(
            any("alpha" in p and "registered" in p for p in problems),
            f"the decoys hold no `alpha`, so nothing should call it built: "
            f"{problems}")


if __name__ == "__main__":
    unittest.main()
