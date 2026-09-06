"""The three hooks, and what each did when it could not answer.

    python3 -m unittest tests.test_the_guards_that_stood_down_quietly -v

Nine findings, six facts, and every one of them is a guard that stopped
guarding without saying so or a rule written down twice. They are held together
because they *are* one cut: four of the six appear in two or three of these
files, and repairing one file at a time is the same fact repaired in one place.

Every symbol the findings name is entered here in this process. A subprocess
would answer the same questions -- and several cases run one as well, because
that is how the host invokes these -- but the tracer behind a closure only sees
this one.

All of them fail against b37852c.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _hook(name):
    spec = importlib.util.spec_from_file_location(
        f"v4_hook_under_test_{name}", ROOT / "hooks" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bash_guard = _hook("bash_guard")
write_block = _hook("write_block")
stop_gate = _hook("stop_gate")


def _repo(case, *, adopted=True):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    if adopted:
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "protected_paths": ["checkers/**", ".v4/**"]}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


def _fed(mod, payload, env=None):
    """`main()` in this process, with `payload` on stdin.

    Returns `(exit, stdout, stderr)`. The tracer follows this call; the
    subprocess cases below follow the wiring."""
    out, err = io.StringIO(), io.StringIO()
    old_stdin, old_env = sys.stdin, dict(os.environ)
    sys.stdin = io.StringIO(payload)
    if env:
        os.environ.update(env)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main()
    finally:
        sys.stdin = old_stdin
        os.environ.clear()
        os.environ.update(old_env)
    return code, out.getvalue(), err.getvalue()


class APayloadThatDoesNotReadIsSaidOutLoud(unittest.TestCase):
    """`json.load(sys.stdin)` with a bare `except` sat in all three. Allowing
    is right -- a payload the hook cannot read is not evidence of anything --
    but allowing in silence makes a dead guard and a clean call identical,
    which is how one of these sat broken in every adopting repo.

    Three cases, not one: repairing a hook does not repair its siblings, and
    that is the whole reason these are one cut."""

    def test_the_bash_guard_says_so(self):
        _code, out, err = _fed(bash_guard, "not json")
        self.assertIn("did not read", err)
        self.assertEqual(out.strip(), "{}", "it refused instead of allowing")

    def test_the_write_hook_says_so(self):
        _code, out, err = _fed(write_block, "not json")
        self.assertIn("did not read", err)
        self.assertEqual(out.strip(), "{}")

    def test_the_stop_gate_says_so(self):
        _code, out, err = _fed(stop_gate, "not json")
        self.assertIn("did not read", err)
        self.assertEqual(out.strip(), "{}")

    def test_and_a_payload_for_another_tool_stays_quiet(self):
        """The control. One line per tool call would drown the record the
        guard is trying to keep, so only an unreadable payload speaks."""
        for mod in (bash_guard, write_block, stop_gate):
            with self.subTest(mod.__name__):
                _code, _out, err = _fed(
                    mod, json.dumps({"tool_name": "WebFetch",
                                     "tool_input": {}, "session_id": "s"}))
                self.assertEqual(err, "", err)


class AGuardThatCannotReadTheLedgerSaysSo(unittest.TestCase):
    """`_open_task` answered `None` for a ledger that would not open, which is
    the same answer as a repo with no open task. The two cases beside it in
    that file -- `_states` and `_ended` -- had already been repaired to speak."""

    def _repo_with_a_broken_ledger(self):
        root = _repo(self)
        db = root / ".git" / "v4" / "ledger.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_text("this is not a database\n", encoding="utf-8")
        return root

    def test_the_stop_gate_reports_a_ledger_it_could_not_open(self):
        root = self._repo_with_a_broken_ledger()
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(stop_gate._open_task(root))
        self.assertIn("did not open", err.getvalue())

    def test_and_a_readable_ledger_is_silent(self):
        """The control: a message on every run is the same as none."""
        root = _repo(self)
        err = io.StringIO()
        with redirect_stderr(err):
            stop_gate._open_task(root)
        self.assertNotIn("did not open", err.getvalue())


class AReadGoesThroughTheReadingDoor(unittest.TestCase):
    """`ledger.connect` creates the file, runs the schema and the append-only
    triggers, commits and migrates. Both hooks called it to answer one read.
    Measured before the repair: pointed at a fresh `git init` tree with no
    `.v4/`, the bash guard left a 65,536-byte ledger behind in a repo that had
    never adopted v4."""

    def test_the_bash_guard_creates_no_ledger_in_a_repo_that_has_none(self):
        root = _repo(self, adopted=False)
        _code, _out, _err = _fed(
            bash_guard,
            json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"},
                        "session_id": "s"}),
            env={"V4_REPO": str(root)})
        self.assertFalse(
            (root / ".git" / "v4" / "ledger.db").exists(),
            "a read created a ledger in a repo that has never adopted v4")

    def test_the_write_hook_creates_none_either(self):
        root = _repo(self, adopted=False)
        _code, _out, _err = _fed(
            write_block,
            json.dumps({"tool_name": "Write",
                        "tool_input": {"file_path": str(root / "x.py")},
                        "session_id": "s"}),
            env={"V4_REPO": str(root)})
        self.assertFalse((root / ".git" / "v4" / "ledger.db").exists())

    def test_the_mark_still_lands_when_there_is_a_ledger(self):
        """The control. A read that answers nothing would pass both cases
        above -- `_mark` has to still find the open tasks and write its row."""
        root = _repo(self)
        from kernel import ledger as ledger_mod

        conn = ledger_mod.connect(root)
        ledger_mod.insert(conn, "task", id="t-live", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        conn.close()
        err = io.StringIO()
        with redirect_stderr(err):
            bash_guard._mark(root, "ls -la", allowed=True, reason="",
                             basis="test", session="s")
        conn = sqlite3.connect(f"file:{root / '.git/v4/ledger.db'}?mode=ro",
                               uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM event WHERE kind = 'hook_seen'"
                            ).fetchall()
        conn.close()
        self.assertTrue(rows, f"no mark was written: {err.getvalue()}")

    def test_and_an_unnameable_command_still_gets_a_row(self):
        """`cmd.split()[0]` raised `IndexError` on the empty command, which is
        exactly what the unreadable-payload paths hand it -- so the repair that
        made them loud left them unrecorded."""
        root = _repo(self)
        from kernel import ledger as ledger_mod

        conn = ledger_mod.connect(root)
        ledger_mod.insert(conn, "task", id="t-live", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        conn.close()
        with redirect_stderr(io.StringIO()):
            bash_guard._mark(root, "", allowed=True, reason="unreadable",
                             basis="unreadable payload", session="s")
        conn = sqlite3.connect(f"file:{root / '.git/v4/ledger.db'}?mode=ro",
                               uri=True)
        rows = conn.execute("SELECT COUNT(*) FROM event WHERE kind = "
                            "'hook_seen'").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 1)


class OneQuestionOneMatcher(unittest.TestCase):
    """`is_protected` was three lines byte-identical to `in_scope` under a
    different name, and both hardcoded `on_path(Path("."))` -- so which of the
    two matchers ran depended on the shell's working directory, in the file
    whose own comments call one-rule-two-implementations the failure being
    removed."""

    def test_both_matchers_answer_the_same_question_the_same_way(self):
        cases = [("x.py", ["**/*.py"]), ("a/b/x.py", ["**/*.py"]),
                 ("checkers/t.py", ["checkers/**"]), ("x.py", ["docs/**"]),
                 ("docs/a.md", ["docs/**"]), ("kernel/x.py", ["kernel/*.py"])]
        for rel, globs in cases:
            with self.subTest(rel=rel, globs=globs):
                self.assertEqual(write_block.in_scope(rel, globs, ROOT),
                                 write_block.is_protected(rel, globs, ROOT))

    def test_and_the_answer_does_not_move_with_the_shell(self):
        """The cwd dependency is the half a byte-identical copy hides: both
        resolved the home marker under `.`, so an adopter got one matcher or
        the other depending on where the shell happened to be."""
        # Two things had to be right for this to be observable, and the first
        # spelling had neither.
        #
        # The input: `x.py` against `**/*.py` is the one pair the owner and the
        # fallback disagree on -- the owner reaches it, the fallback does not,
        # which `in_scope`'s docstring records. Measured: with `a/b/x.py` both
        # answer True and the case stayed green against the copy.
        #
        # The probe: `on_path` finds the framework through `kernel/` beside the
        # hooks directory, which is true of this repo from any working
        # directory -- measured, `None` from the repo and `None` from a temp
        # dir -- so chdir alone cannot reach the fallback here at all. It is an
        # adopter, whose framework is found through `.v4/home` under the repo
        # root, where the argument decides. Handing `on_path` an answer that
        # depends on which path it was given is what makes the difference
        # between `Path(".")` and `repo_root` visible without inventing one.
        # And the shell has to actually be somewhere else, or `Path(".")` and
        # `repo_root` resolve to the same thing and the probe sees nothing.
        old = write_block._framework.on_path
        write_block._framework.on_path = (
            lambda p: None if Path(p).resolve() == ROOT else "not here")
        here = os.getcwd()
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        try:
            os.chdir(elsewhere)
            self.assertEqual(
                write_block.is_protected("x.py", ["**/*.py"], ROOT),
                write_block.in_scope("x.py", ["**/*.py"], ROOT),
                "one matcher asked about the repo root and the other about "
                "the working directory")
        finally:
            os.chdir(here)
            write_block._framework.on_path = old


class TheDroppedTaskIsSaidOutLoud(unittest.TestCase):
    """`write_block` keeps a `V4_TASK` that named an ended task and records it
    on the row it writes; the stop gate discarded it, so the same session was
    answerable for its writes and not for its stops."""

    def _repo_with_an_ended_task(self):
        root = _repo(self)
        from kernel import ledger as ledger_mod

        conn = ledger_mod.connect(root)
        ledger_mod.insert(conn, "task", id="t-over", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        ledger_mod.insert(conn, "task", id="t-live", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        ledger_mod.insert(conn, "event", task_id="t-over", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
        conn.close()
        return root

    def test_the_stop_gate_hands_back_what_it_dropped(self):
        root = self._repo_with_an_ended_task()
        old = dict(os.environ)
        os.environ["V4_REPO"], os.environ["V4_TASK"] = str(root), "t-over"
        try:
            with redirect_stderr(io.StringIO()):
                _root, task, dropped = stop_gate._repo_and_task()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(task, "t-live")
        self.assertEqual(dropped, "t-over")

    def test_and_a_live_v4_task_is_not_reported_as_dropped(self):
        """The control: reporting on every run says nothing."""
        root = self._repo_with_an_ended_task()
        old = dict(os.environ)
        os.environ["V4_REPO"], os.environ["V4_TASK"] = str(root), "t-live"
        try:
            with redirect_stderr(io.StringIO()):
                _root, task, dropped = stop_gate._repo_and_task()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(task, "t-live")
        self.assertEqual(dropped, "")


class EveryRefusalGoesThroughTheNamedSymbol(unittest.TestCase):
    """`refuse_the_stop` was extracted so a refusal added later reaches the
    declared decision by calling it; the fourth one printed the same dict
    instead, and the facts table said all of them went through the symbol.
    `write_block` had no such symbol at all and printed three.

    Held by replacing the symbol and watching for its answer, not by reading
    the source: a print statement that happens to match is not a call."""

    def test_the_ambiguous_stop_reaches_refuse_the_stop(self):
        root = _repo(self)
        from kernel import ledger as ledger_mod

        conn = ledger_mod.connect(root)
        for tid in ("t-one", "t-two"):
            ledger_mod.insert(conn, "task", id=tid, request="r" * 80,
                              scope_globs=["**"], base_commit="x",
                              created_at="2026")
        conn.close()
        seen = []
        old = stop_gate.refuse_the_stop
        stop_gate.refuse_the_stop = lambda reason: (seen.append(reason) or 0)
        try:
            _code, _out, _err = _fed(
                stop_gate, json.dumps({"session_id": "s"}),
                env={"V4_REPO": str(root)})
        finally:
            stop_gate.refuse_the_stop = old
        self.assertEqual(len(seen), 1,
                         "the fourth refusal printed its own dict")

    def test_the_write_refusals_reach_refuse_the_write(self):
        """The protected-path refusal, through the new symbol. Two tasks open
        is the ambiguous one and is covered by the case above's sibling; this
        is the branch a repo with no task takes."""
        root = _repo(self)
        (root / "checkers").mkdir()
        seen = []
        old = write_block.refuse_the_write
        write_block.refuse_the_write = lambda reason: (seen.append(reason) or 0)
        try:
            _code, _out, _err = _fed(
                write_block,
                json.dumps({"tool_name": "Write",
                            "tool_input": {"file_path":
                                           str(root / "checkers" / "x.py")},
                            "session_id": "s"}),
                env={"V4_REPO": str(root)})
        finally:
            write_block.refuse_the_write = old
        self.assertEqual(len(seen), 1, "a refusal printed its own dict")

    def test_and_an_allowed_write_reaches_no_refusal(self):
        """The control. A symbol called on every path proves nothing."""
        root = _repo(self)
        seen = []
        old = write_block.refuse_the_write
        write_block.refuse_the_write = lambda reason: (seen.append(reason) or 0)
        try:
            _fed(write_block,
                 json.dumps({"tool_name": "Write",
                             "tool_input": {"file_path": str(root / "x.py")},
                             "session_id": "s"}),
                 env={"V4_REPO": str(root)})
        finally:
            write_block.refuse_the_write = old
        self.assertEqual(seen, [])


class TheUnreadableBranchIsReachable(unittest.TestCase):
    """`open_task` returned `None` when the kernel would not import, because
    the condition was tested twice and the first test caught both cases. The
    distinction the docstring is built around -- a ledger that is absent versus
    a kernel that cannot be reached -- could not happen."""

    def test_a_kernel_that_will_not_import_answers_unreadable(self):
        root = _repo(self)
        db = root / ".git" / "v4" / "ledger.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_text("", encoding="utf-8")
        old = write_block._framework.ledger
        write_block._framework.ledger = lambda _root: None
        try:
            with redirect_stderr(io.StringIO()) as err:
                got = write_block.open_task(root)
        finally:
            write_block._framework.ledger = old
        self.assertIs(got, write_block.UNREADABLE)

    def test_and_no_ledger_file_at_all_is_still_none(self):
        """The other half of the same distinction, and the reason the first
        check exists: a fresh clone has no `.git/v4/ledger.db`, which this repo
        documents as a state rather than a read that failed."""
        root = _repo(self)
        self.assertIsNone(write_block.open_task(root))


class TheHooksStillRunAsThePlatformRunsThem(unittest.TestCase):
    """The wiring, through a real process, because everything above runs
    `main` in-process and a module that imports cleanly can still be a hook the
    host cannot execute."""

    def _run(self, name, payload):
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / f"{name}.py")],
            input=payload, capture_output=True, text=True)

    def test_each_one_exits_zero_and_prints_json(self):
        for name in ("bash_guard", "write_block", "stop_gate"):
            with self.subTest(name):
                r = self._run(name, "not json")
                self.assertEqual(r.returncode, 0, r.stderr[:300])
                json.loads(r.stdout)
                self.assertIn("did not read", r.stderr)


if __name__ == "__main__":
    unittest.main()
