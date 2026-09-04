"""The repo's name is a declaration, not a directory listing.  SPEC.md §0.

Twice a verdict turned on the name of the directory a checkout sat in, and the
second time is what says the first repair was in the wrong place.

The first was worktrees: `v4 doctrine` rewrote `CLAUDE.md`'s title to the
worktree's basename, and `registry-consistency` failed in every worktree
afterwards. That was repaired by asking git for the main worktree's directory
-- still a directory name.

The second is this repo's own published tree. Cloned into `verify-public`, `v4
accept` came back 6/7 on `registry-consistency`, with the same complaint about
`CLAUDE.md`, on bytes that pass 7/7 under the directory name they were
developed in. `git clone <url> my-name` and GitHub's Download ZIP -- which
unpacks to `<repo>-main/` -- both reach it.

So the cases below fix the directory name to something the repo is not called
and ask what the repo says it is. Against the derivation-only version every
`DeclaredNameWins` case fails; `FilesystemAnswersWhenNothingIsDeclared` is what
stops the repair from becoming "always trust a file that may not be there".
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import doctrine, layout                             # noqa: E402
from kernel.config import RepoConfig, facts_path_for            # noqa: E402


def _tree(case, dirname, tables=(), git=True, control_plane=False):
    """A checkout at `dirname` carrying `tables` as (filename, repo) pairs.

    `control_plane` copies this repo's own `config.json` and `claim_kinds.json`,
    which `doctrine.render` needs: the rendered document is the registry, and a
    tree with no registry renders nothing to compare.
    """
    import shutil
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    root = tmp / dirname
    (root / ".v4").mkdir(parents=True)
    if control_plane:
        for name in ("config.json", "claim_kinds.json"):
            shutil.copy(ROOT / ".v4" / name, root / ".v4" / name)
    for filename, declared in tables:
        # Empty lists need an `absent` reason or `validate` refuses the table,
        # which is the rule that stops a blank table reading as a clean repo.
        # These trees hold no source at all, so the reasons are true.
        blank = "This tree is a fixture with no source in it, so there is none."
        body = {"repo": declared, "generated_from_commit": "0" * 40,
                "absent": {k: blank for k in
                           ("outbound_write", "outbound_read", "auth_decision",
                            "entrypoint_globs", "protected_paths")},
                "outbound_write": [], "outbound_read": [], "auth_decision": [],
                "entrypoint_globs": [], "ui_globs": [], "config_files": [],
                "protected_paths": []}
        (root / ".v4" / filename).write_text(json.dumps(body))
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


class DeclaredNameWins(unittest.TestCase):
    """The directory is named one thing and the table says another."""

    def test_the_table_names_the_repo_and_the_directory_does_not(self):
        root = _tree(self, "verify-public", [("facts.myrepo.json", "myrepo")])
        self.assertEqual(layout.repo_name(root), "myrepo")

    def test_the_download_zip_shape(self):
        """GitHub unpacks `<repo>-main/`, which is nobody's repo name."""
        root = _tree(self, "myrepo-main", [("facts.myrepo.json", "myrepo")])
        self.assertEqual(layout.repo_name(root), "myrepo")

    def test_and_the_facts_lookup_still_finds_the_file(self):
        """The circularity worth checking: `facts_path_for` builds its
        preferred filename out of this name, so a declaration that disagreed
        with the filename would send it looking for a file that is not there."""
        root = _tree(self, "verify-public", [("facts.myrepo.json", "myrepo")])
        got = facts_path_for(root)
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "facts.myrepo.json")

    @staticmethod
    def _title(text):
        """The `# ` heading, not the first line.

        Written as `splitlines()[0]` first, which is the `v4:doctrine:begin`
        marker -- a constant, identical in every checkout. The case passed with
        the repair torn out, which is the shape `test-shape` exists to report:
        an assertion that holds whatever the code does.
        """
        for line in text.splitlines():
            if line.startswith("# "):
                return line
        raise AssertionError("the rendered doctrine has no title")

    def test_the_doctrine_title_stops_moving_with_the_directory(self):
        """The symptom both times: a generated file that differs by checkout,
        which `registry-consistency` reads as an edited-by-hand document."""
        here = self._title(doctrine.render(RepoConfig(ROOT)))
        self.assertIn("vibeproof", here)
        moved = _tree(self, "somewhere-else",
                      [("facts.vibeproof.json", "vibeproof")],
                      control_plane=True)
        self.assertEqual(self._title(doctrine.render(RepoConfig(moved))), here)


class FilesystemAnswersWhenNothingIsDeclared(unittest.TestCase):
    """Otherwise the repair is "trust a file", and the file is often absent."""

    def test_no_table_falls_back_to_the_repo_git_calls_it(self):
        root = _tree(self, "plain-checkout")
        self.assertEqual(layout.repo_name(root), "plain-checkout")

    def test_two_tables_fall_back_rather_than_pick_one(self):
        """`facts_path_for` needs this answer to choose between them, so
        reading either here would be circular."""
        root = _tree(self, "two-tables", [("facts.aaa.json", "aaa"),
                                          ("facts.zzz.json", "zzz")])
        self.assertEqual(layout.repo_name(root), "two-tables")
        self.assertIsNone(layout.declared_repo_name(root))

    def test_a_draft_is_not_a_declaration(self):
        """`v4 install` writes `facts.<name>.json.draft` for a person to
        review, and a proposal is not a decision -- `facts_path_for` excludes
        drafts for the same reason."""
        root = _tree(self, "drafting", [("facts.mine.json.draft", "mine")])
        self.assertIsNone(layout.declared_repo_name(root))
        self.assertEqual(layout.repo_name(root), "drafting")

    def test_a_table_with_no_repo_field_declares_nothing(self):
        root = _tree(self, "no-field")
        (root / ".v4" / "facts.x.json").write_text(json.dumps({"absent": {}}))
        self.assertIsNone(layout.declared_repo_name(root))
        self.assertEqual(layout.repo_name(root), "no-field")

    def test_a_table_that_will_not_parse_declares_nothing(self):
        root = _tree(self, "broken-json")
        (root / ".v4" / "facts.x.json").write_text("{not json")
        self.assertIsNone(layout.declared_repo_name(root))
        self.assertEqual(layout.repo_name(root), "broken-json")

    def test_not_a_git_repo_at_all_uses_the_basename(self):
        root = _tree(self, "bare-directory", git=False)
        self.assertEqual(layout.repo_name(root), "bare-directory")


class TheWorktreeCaseStillHolds(unittest.TestCase):
    """The first repair must survive the second: a worktree of a repo with no
    facts table still gets the repo's name, not the worktree directory's."""

    def test_a_worktree_is_still_named_after_its_repo(self):
        main = _tree(self, "the-repo")
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"],
                       cwd=main, check=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
                            "PATH": __import__("os").environ["PATH"],
                            "HOME": str(main.parent)})
        wt = main.parent / "wt-d1"
        subprocess.run(["git", "worktree", "add", "-q", str(wt)],
                       cwd=main, check=True)
        self.assertEqual(layout.repo_name(wt), "the-repo")


if __name__ == "__main__":
    unittest.main()
