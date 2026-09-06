"""Repairs in `hooks/`, called directly.

    python3 -m unittest tests.test_a_hook_asks_the_owner_when_it_can_reach_it -v

A hook fires from the coding agent's settings, in a process this framework did
not start, so it cannot depend on the kernel being importable. That is why each
of these carries a fallback -- and it is not a licence to answer a *different*
question when the kernel is there, which is what three of them did.

All of these fail against 0ad6b61.
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
sys.path.insert(0, str(ROOT / "hooks"))

import _framework                                               # noqa: E402
import bash_guard                                               # noqa: E402
import write_block                                              # noqa: E402
from kernel import ledger                                       # noqa: E402


def _repo(case, *, scope=("app/**",)):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "protected_paths": ["secrets/**"]}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    conn = ledger.connect(tmp)
    case.addCleanup(conn.close)
    ledger.insert(conn, "task", id="t", request="r", scope_globs=list(scope),
                  base_commit="", created_at="2026")
    return tmp, conn


class OneBootstrapForThreeHooks(unittest.TestCase):
    """Each hook carried its own `sys.path` dance and its own failure text."""

    def test_the_framework_is_reachable_from_beside_the_hooks(self):
        self.assertIsNone(_framework.on_path(ROOT))

    def test_and_a_repo_with_no_framework_says_why(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        import os
        old = os.environ.pop("V4_HOME", None)
        try:
            # `on_path` finds the framework beside the hooks, so this asks the
            # question the other way: `why` is the string a hook prints, and it
            # has to be a sentence rather than silence.
            self.assertIsInstance(_framework.why(tmp), str)
        finally:
            if old is not None:
                os.environ["V4_HOME"] = old


class AWidenThatWasRefusedIsNotScope(unittest.TestCase):
    """`write_block.current_scope` was a second implementation in raw SQL.

    Its own comment gave the reason -- "kernel/scope.py holds the same read and
    the same reason; this file cannot import it" -- and `hooks/_framework.py`
    made that sentence untrue. The rule it has to keep is one line long, which
    is exactly the kind that gets fixed on one side.
    """

    def _widened(self, accepted):
        root, conn = _repo(self)
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="scope_widen", actor="worker",
                          payload={"added": ["docs/**"],
                                   "engagement": {"accepted": accepted}},
                          created_at="2026")
        return root

    def test_an_accepted_widen_is_in_scope(self):
        self.assertIn("docs/**", write_block.current_scope(self._widened(True), "t"))

    def test_a_refused_one_is_not(self):
        self.assertNotIn("docs/**", write_block.current_scope(self._widened(False), "t"))


class AProtectedPathIsGuardedWithNoTaskOpen(unittest.TestCase):
    """The Write/Edit half never asked whether a path was protected.

    It read `current_scope`/`in_scope` only, so with no task open it stood down
    entirely -- and no task open is the normal state for a review or a monitor
    session, since `ENDED_TASKS_SQL` unions `repo-review` into the ended set.
    """

    def test_the_repos_protected_set_is_readable_without_a_task(self):
        root, _conn = _repo(self)
        got = write_block.protected(root)
        self.assertIsNotNone(got, "the hook could not read what judges the work")
        self.assertIn("secrets/**", got)

    def test_the_framework_defaults_are_in_it_too(self):
        """A union, not the repo's list alone: `.v4/**` is protected whether or
        not an adopter remembered to say so."""
        root, _conn = _repo(self)
        self.assertTrue(any(g.startswith(".v4") for g in write_block.protected(root)))


class InScopeAsksTheOwnerWhenItCan(unittest.TestCase):
    """The hook's own two spellings are narrower than `subject_files.matches`."""

    def test_a_recursive_glob_reaches_a_file_at_the_root(self):
        self.assertTrue(write_block.in_scope("x.py", ["**/*.py"]))

    def test_and_something_outside_is_still_outside(self):
        self.assertFalse(write_block.in_scope("docs/x.md", ["app/**"]))


class TheShellGuardLeavesAMarkToo(unittest.TestCase):
    """`bash_guard` wrote no `hook_seen` row on any path.

    `v4 ship` reports DEGRADED off those rows, so the one guard covering the
    shell route to protected paths left nothing behind: a deny that actually
    stopped `sed -i .v4/config.json` was unrecoverable afterwards, and
    `trend.gate` reported refusals from one hook only.
    """

    def test_the_guard_records_that_it_ran(self):
        root, conn = _repo(self)
        failed = _framework.record_seen(root, "t", "sed", allowed=False,
                                        reason="protected path", basis="protected")
        self.assertEqual(failed, "")
        rows = list(conn.execute(
            "SELECT payload FROM event WHERE kind = 'hook_seen'"))
        self.assertTrue(rows, "the hook fired and left nothing behind")
        self.assertIn("protected", rows[0]["payload"])

    def test_bash_guard_reaches_the_same_recorder(self):
        """Named rather than reimplemented: the finding was that one hook had
        this and the other did not."""
        self.assertIs(getattr(bash_guard, "_framework"), _framework)


class TheHooksActuallyRun(unittest.TestCase):
    """`main`, `_record_seen` and the kernel lookup, entered rather than read.

    Each of these is the shape `redgreen` refuses when a test asserts around
    the code instead of through it -- and it refused these bindings until the
    tests below called them.
    """

    def _run(self, module, payload, root):
        import io
        import os
        old_in, old_out = sys.stdin, sys.stdout
        old_repo = os.environ.get("V4_REPO")
        os.environ["V4_REPO"] = str(root)
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()
        try:
            code = module.main()
            return code, sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            if old_repo is None:
                os.environ.pop("V4_REPO", None)
            else:
                os.environ["V4_REPO"] = old_repo

    def test_the_write_hook_denies_a_protected_path_with_no_task_open(self):
        root, _conn = _repo(self)
        (root / "secrets").mkdir()
        code, out = self._run(
            write_block, {"tool_name": "Write",
                          "tool_input": {"file_path": str(root / "secrets/x.env")}},
            root)
        self.assertEqual(code, 0, "a hook must never exit non-zero")
        self.assertIn("deny", out)

    def test_and_lets_an_ordinary_path_through(self):
        """The control: denying everything would pass the test above."""
        root, _conn = _repo(self)
        (root / "app").mkdir()
        code, out = self._run(
            write_block, {"tool_name": "Write",
                          "tool_input": {"file_path": str(root / "app/x.py")}},
            root)
        self.assertEqual(code, 0)
        self.assertNotIn("deny", out)

    def test_the_shell_guard_denies_a_write_to_a_protected_path(self):
        root, _conn = _repo(self)
        code, out = self._run(bash_guard,
                              {"tool_name": "Bash",
                               "tool_input": {"command": "sed -i '' s/a/b/ .v4/config.json"}},
                              root)
        self.assertEqual(code, 0)
        self.assertIn("deny", out)

    def test_and_leaves_a_mark_that_it_ran(self):
        root, conn = _repo(self)
        self._run(bash_guard,
                  {"tool_name": "Bash",
                   "tool_input": {"command": "sed -i '' s/a/b/ .v4/config.json"}},
                  root)
        rows = list(conn.execute("SELECT payload FROM event WHERE kind = 'hook_seen'"))
        self.assertTrue(rows, "the guard denied and recorded nothing")

    def test_record_seen_says_on_stderr_when_it_cannot_write(self):
        import io
        import contextlib
        # A repo that adopted v4 and whose mark still could not be written.
        # A bare directory is a different fact -- a tree that never adopted v4,
        # where this hook has nothing to record and says nothing, because it
        # fires in every repo the agent touches and a line per tool call there
        # is noise. `.v4/` is the adoption; the kernel being out of reach is
        # the failure this case is about.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            write_block._record_seen(tmp, None, "x.py")
        self.assertTrue(err.getvalue().strip(),
                        "a mark that could not be written said nothing at all")

    def test_and_a_repo_that_never_adopted_v4_is_left_alone(self):
        """The other side of the same line. These hooks are installed in the
        agent's settings, so they fire in every repository their user opens;
        recording in one that has no `.v4/` both talks over every tool call and
        creates a ledger nobody asked for."""
        import io
        import contextlib
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            write_block._record_seen(tmp, None, "x.py")
        self.assertEqual(err.getvalue(), "")
        self.assertFalse((tmp / ".git" / "v4" / "ledger.db").exists())

    def test_the_hook_reaches_the_kernel_through_one_bootstrap(self):
        """`write_block._kernel` is gone. It read `V4_HOME` and `.v4/home`,
        returned None in this repo on every invocation, and each hook carried
        its own copy of that lookup with its own failure text."""
        self.assertFalse(hasattr(write_block, "_kernel"))
        self.assertIsNotNone(_framework.config(ROOT))

    def test_and_says_why_when_there_is_none(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertIsInstance(_framework.why(tmp), str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
