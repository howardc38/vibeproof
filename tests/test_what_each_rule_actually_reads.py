"""Repairs across `hooks/`, `kernel/analysis/` and `checkers/test_token_shape.py`.

    python3 -m unittest tests.test_what_each_rule_actually_reads -v

A gate that decides whether a turn may end, a guard that decides whether a
command may write, a parser that decides whether a dependency is pinned, and a
checker that prints what it found: each was reading something next to its
subject rather than its subject.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from kernel import ledger  # noqa: E402
from kernel.analysis import (layers, # noqa: E402
                             shell_command, structural_lint, subject_files)

import stop_gate  # noqa: E402


def _repo(case, files=None, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    for rel, text in (files or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "in"], cwd=tmp, capture_output=True)
    return tmp


class TheGateThatDecidesWhetherATurnMayEnd(unittest.TestCase):
    """It recovered claim state by splitting the human-readable status lines.

    `cmd_status` grew `--json` because "which claim, in which state, and what
    is blocking it had to be recovered by parsing lines written for a human,
    and those lines change whenever the wording improves" -- and the one caller
    doing that parsing was this. A parse yielding zero rows made `open_claims`
    empty, which takes the *other* branch and tells the worker to ship.
    """

    def test_it_reads_the_machine_answer(self):
        root = _repo(self, {"a.py": "x = 1\n"})
        # What `install.write_launcher` records, and what the hook reads to
        # find a kernel an adopter does not have of its own.
        (root / ".v4" / "home").write_text(str(ROOT))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="scope",
                      question="q", subject_refs=[], checker="scope",
                      origin="derive", file=None, symbol=None, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at="2026-08-19T00:00:00+00:00")
        conn.commit()
        got = stop_gate._states(root, "t1")
        self.assertIsInstance(got, list, got)
        self.assertEqual([c[1] for c in got], ["c1"])
        self.assertTrue(all(len(c) == 3 for c in got), got)

    def test_a_status_that_died_says_so_rather_than_standing_down_in_silence(self):
        """`v4 status` on a repo whose config does not parse exits 5; the
        timeout and exception paths above both print, and this one returned
        `None` with nothing said -- indistinguishable, to the platform, from a
        task with nothing owed."""
        root = _repo(self, {"a.py": "x = 1\n"})
        (root / ".v4" / "home").write_text(str(ROOT))
        ledger.connect(root).close()
        (root / ".v4" / "config.json").write_text("{ not json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = stop_gate._states(root, "t1")
        self.assertIsNone(got)
        self.assertTrue(err.getvalue().strip(),
                        "silence is what the platform reads as allow")


class TheBasisIsAnEnumNotASentence(unittest.TestCase):
    """`basis` is read back by equality -- `json_extract(payload, '$.basis') =
    'cleared'` -- and `main` concatenated prose onto it whenever `V4_TASK` named
    a task that had ended, so the engagement gate was never marked spent in
    exactly the case `open_task` documents as ordinary."""

    def test_the_note_is_its_own_field(self):
        """Run as the platform runs it: a hook payload on stdin, with `V4_TASK`
        naming a task that has ended, which `open_task` documents as ordinary
        -- a shell outliving its task."""
        import os
        root = _repo(self, {"a.py": "x = 1\n"})
        (root / ".v4" / "home").write_text(str(ROOT))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t-ended", request="r",
                      scope_globs=["**"], base_commit="",
                      created_at="2026-08-19T00:00:00+00:00")
        ledger.insert(conn, "task", id="t-live", request="r",
                      scope_globs=["**"], base_commit="",
                      created_at="2026-08-19T00:00:01+00:00")
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t-ended", claim_id=None,
                          kind="abandoned", actor="worker",
                          payload={"why": "the shell outlived it"},
                          created_at="2026-08-19T00:00:02+00:00")
        conn.commit()

        payload = json.dumps({"tool_name": "Write",
                              "tool_input": {"file_path": str(root / "a.py")},
                              "cwd": str(root)})
        argv, stdin, task = sys.argv, sys.stdin, os.environ.get("V4_TASK")
        repo_env = os.environ.get("V4_REPO")
        os.environ["V4_TASK"] = "t-ended"
        os.environ["V4_REPO"] = str(root)
        sys.argv = ["write_block.py"]
        sys.stdin = io.StringIO(payload)
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                try:
                    runpy.run_path(str(ROOT / "hooks" / "write_block.py"),
                                   run_name="__main__")
                except SystemExit:
                    pass
        finally:
            sys.argv, sys.stdin = argv, stdin
            for name, was in (("V4_TASK", task), ("V4_REPO", repo_env)):
                if was is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = was

        row = conn.execute(
            "SELECT payload FROM event WHERE kind = 'hook_seen' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row, said.getvalue())
        seen = json.loads(row["payload"])
        # `basis` is read back by equality -- `json_extract(payload, '$.basis')
        # = 'cleared'` -- so anything appended to it makes every such read miss.
        self.assertEqual(seen.get("basis"), (seen.get("basis") or "").strip())
        self.assertNotIn("V4_TASK", seen.get("basis") or "", seen)
        self.assertNotIn("(", seen.get("basis") or "", seen)
        self.assertIn("has ended", json.dumps(seen, ensure_ascii=False),
                      "and the fact still has to be recorded somewhere")


class WhatALayerScanWalks(unittest.TestCase):
    """`root.rglob("*.py")` with no exclusion of any kind, which is how
    `checkers/layer_boundary.py` always calls it -- and a relative import that
    did get reported came back with line 0."""

    def _scan(self, root, **kw):
        return layers.scan(root, {
            "layers": [{"name": "app", "paths": ["app/**"]},
                       {"name": "core", "paths": ["core/**"]}],
            "allow": [["app", "core"]]}, **kw)

    def test_an_import_going_the_wrong_way_is_found_with_its_line(self):
        root = _repo(self, {"app/__init__.py": "", "app/x.py": "y = 1\n",
                            "core/__init__.py": "",
                            "core/y.py": "import os\nfrom app.x import y\n"})
        got = self._scan(root)
        self.assertTrue(got, got)
        self.assertTrue(all(g[1] > 0 for g in got), got)

    def test_and_what_git_does_not_track_is_not_walked(self):
        """Inside a declared layer, so the only thing keeping it out of the
        scan is that git never heard of it."""
        root = _repo(self, {"app/__init__.py": "", "app/x.py": "y = 1\n",
                            "core/__init__.py": "", "core/y.py": "z = 1\n",
                            ".gitignore": "core/vendor/\n"})
        junk = root / "core" / "vendor"
        junk.mkdir(parents=True)
        (junk / "z.py").write_text("from app.x import y\n")
        got = self._scan(root)
        self.assertEqual([g for g in got if "vendor" in str(g[0])], [], got)


class WhichCommandsWriteWhere(unittest.TestCase):
    """`cp -t DIR SRC` puts the destination first and `LAST_ARG` picked the
    source; `git`, `touch`, `mkdir` and `patch` were absent entirely."""

    def _hits(self, cmd):
        return shell_command.writes_to_protected(
            cmd, subject_files.PROTECTED_DEFAULT)

    def test_a_destination_named_by_a_flag_is_the_destination(self):
        self.assertTrue(self._hits("cp -t .v4 /tmp/config.json"))
        self.assertTrue(self._hits("install -t checkers /tmp/evil.py"))

    def test_reverting_the_files_that_judge_the_work_is_a_write(self):
        for cmd in ("git checkout -- .v4/config.json", "git restore .v4/",
                    "git rm .v4/config.json"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self._hits(cmd))

    def test_and_reading_is_still_free(self):
        self.assertEqual(self._hits("git status .v4"), [])
        self.assertEqual(self._hits("cat .v4/config.json"), [])

    def test_the_quiet_writers_too(self):
        for cmd in ("touch .v4/config.json", "mkdir -p .v4/foo",
                    "patch -p1 checkers/scope.py"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self._hits(cmd))


class WhatCountsAsATestFile(unittest.TestCase):
    """`_is_test` accepted `tests/`, `/tests/` and `test_*`, and left out
    `*_test.py` -- the only convention a Go-style layout uses, and one of
    pytest's two defaults. In such a repo every threshold a fixture states is
    judged as logic."""

    def test_the_other_default_naming_counts(self):
        self.assertTrue(structural_lint._is_test("pkg/thing_test.py"))
        self.assertTrue(structural_lint._is_test("tests/test_thing.py"))

    def test_and_conftest_is_not_a_source_of_config(self):
        self.assertFalse(structural_lint._is_config("tests/conftest.py"))


class APositionalOnlyDefaultBelongsToItsOwnParameter(unittest.TestCase):
    """`args.defaults` covers the last N of `posonlyargs + args` and the names
    came from `args.args` alone, so `def f(x=0.75, /, y=0.8)` reported 0.75
    against `y` -- a real claim from a pairing that does not exist."""

    def test_a_slash_does_not_shift_the_pairing(self):
        got = structural_lint._restated(
            ast.parse("def f(x=0.75, /, y=0.8):\n    return x + y\n"),
            {0.75: ("y", 1)})
        self.assertEqual(got, [], got)

    def test_and_the_true_duplicate_is_still_found(self):
        got = structural_lint._restated(
            ast.parse("def g(a=0.8, /, b=0.75):\n    return a + b\n"),
            {0.75: ("b", 1)})
        self.assertTrue([name for _v, _line, name in got if name == "b"], got)


class WhatTheCheckerPrintsGoesIntoACommittedFile(unittest.TestCase):
    """`test_token_shape` printed the raw literal to stdout.

    Checker stdout is stored verbatim in `attempt.stdout` and exported to
    `.v4/ledger_export.jsonl`, and `runner.redact` does not cover the telegram
    shape this checker exists for -- so `123:SECRET` was written unredacted
    into a git-tracked file by the checker whose purpose is keeping it out of
    one. The module it imports has provided `redact()` all along.
    """

    def test_the_literal_it_found_is_redacted_on_the_way_out(self):
        secret = "1234567:AAF-thisIsShapedLikeATelegramToken12345"
        root = _repo(self, {"tests/test_thing.py":
                            f"TOKEN = {secret!r}\n\n\n"
                            "def test_thing():\n    assert TOKEN\n"})
        subj = root / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(root)}))
        argv = sys.argv
        sys.argv = ["test_token_shape.py", "--subject", str(subj)]
        said, code = io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                try:
                    runpy.run_path(str(ROOT / "checkers" / "test_token_shape.py"),
                                   run_name="__main__")
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 0
        finally:
            sys.argv = argv
        self.assertEqual(code, 1, said.getvalue())
        self.assertIn("test_thing.py", said.getvalue())
        self.assertNotIn(secret, said.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
