"""Three holes found by reading a much larger system that had died of them.

Each was verified by breaking this repo before it was fixed, and each is here
because the test suite was green while all three were live -- two of them
because every test starts from a fresh database, and the third because nothing
had ever run a checker with a hostile PATH.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel import ledger                                        # noqa: E402


class HashMaterialIsUnambiguous(unittest.TestCase):
    """A checker controls stdout and stderr, and could print the separator."""

    BASE = dict(claim_id="c", subject_digest="s", checker_sha="k", config_sha="g",
                head_commit="h", argv="a", exit_code=0, started_at="t1",
                ended_at="t2", claim_digest="")

    def test_boundary_cannot_be_shifted(self):
        a = ledger._row_hash("p", dict(self.BASE, stdout="A\x1fB", stderr="C"),
                             ledger.CHAIN_SCHEME)
        b = ledger._row_hash("p", dict(self.BASE, stdout="A", stderr="B\x1fC"),
                             ledger.CHAIN_SCHEME)
        self.assertNotEqual(
            a, b,
            "two attempts that differ only in where stdout ends hash the same. "
            "Whoever can do this could already write the database -- what it "
            "buys them is that the edit survives `v4 audit`, which is the one "
            "command whose entire job is to notice.")

    def test_worktree_is_covered_from_scheme_4(self):
        """It was written NOT NULL on every attempt and hashed by nothing.

        So "which worktree produced this answer" -- the fact the shared-ledger
        design exists to preserve -- could be rewritten afterwards and `v4 audit`
        would still report the chain intact. Provenance whose whole value is
        that it cannot be changed later.
        """
        a = ledger._row_hash("p", dict(self.BASE, stdout="x", stderr="y",
                                       worktree="/a", facts_sha=""), "v4-chain-4")
        b = ledger._row_hash("p", dict(self.BASE, stdout="x", stderr="y",
                                       worktree="/b", facts_sha=""), "v4-chain-4")
        self.assertNotEqual(a, b, "the worktree can be edited and the row still "
                                  "hashes the same")

    def test_facts_sha_is_covered_from_scheme_4(self):
        a = ledger._row_hash("p", dict(self.BASE, stdout="x", stderr="y",
                                       worktree="/a", facts_sha=""), "v4-chain-4")
        b = ledger._row_hash("p", dict(self.BASE, stdout="x", stderr="y",
                                       worktree="/a", facts_sha="ff"), "v4-chain-4")
        self.assertNotEqual(a, b)

    def test_scheme_4_is_one_this_ledger_will_verify(self):
        """`SCHEMES` is a hand-written list and the bump missed it once.

        `verify_exported` refused every new row with "unknown chain scheme",
        so the export CI walks would have been red for a chain that was fine.
        """
        self.assertIn(ledger.CHAIN_SCHEME, ledger.SCHEMES)

    def test_old_rows_still_verify_under_their_own_scheme(self):
        """Changing the formula must not turn recorded history permanently red."""
        row = dict(self.BASE, stdout="x", stderr="y")
        old = ledger._row_hash("p", row, "v4-chain-1")
        self.assertEqual(old, ledger._row_hash("p", row, "v4-chain-1"))
        self.assertNotEqual(old, ledger._row_hash("p", row, ledger.CHAIN_SCHEME))


class SchemaGrowthReachesOldDatabases(unittest.TestCase):
    """`CREATE TABLE IF NOT EXISTS` is a no-op against a table that exists."""

    def test_missing_columns_are_added_and_history_survives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True)
            (root / ".git" / "v4").mkdir(parents=True, exist_ok=True)
            db = root / ".git" / "v4" / "ledger.db"

            # A ledger from before the columns existed, with one attempt in it.
            conn = sqlite3.connect(db)
            conn.executescript(
                "CREATE TABLE attempt (id INTEGER PRIMARY KEY, claim_id TEXT, "
                "subject_digest TEXT, checker_sha TEXT, config_sha TEXT, "
                "head_commit TEXT, worktree TEXT, argv TEXT, exit_code INTEGER, "
                "stdout TEXT, stderr TEXT, started_at TEXT, ended_at TEXT, "
                "duration_ms INTEGER, prev_hash TEXT, row_hash TEXT);")
            row = dict(claim_id="c", subject_digest="s", checker_sha="k",
                       config_sha="g", head_commit="h", argv="a", exit_code=0,
                       stdout="x", stderr="y", started_at="t1", ended_at="t2")
            h = ledger._row_hash(ledger.GENESIS, row, "v4-chain-1")
            conn.execute(
                "INSERT INTO attempt (claim_id, subject_digest, checker_sha, "
                "config_sha, head_commit, worktree, argv, exit_code, stdout, "
                "stderr, started_at, ended_at, duration_ms, prev_hash, row_hash) "
                "VALUES (?,?,?,?,?,'',?,?,?,?,?,?,0,?,?)",
                (row["claim_id"], row["subject_digest"], row["checker_sha"],
                 row["config_sha"], row["head_commit"], row["argv"],
                 row["exit_code"], row["stdout"], row["stderr"],
                 row["started_at"], row["ended_at"], ledger.GENESIS, h))
            conn.commit()
            conn.close()

            conn = ledger.connect(root)
            have = {r[1] for r in conn.execute("PRAGMA table_info(attempt)")}
            self.assertIn("claim_digest", have,
                          "an older ledger never gains a column the schema grew, "
                          "so every insert dies and the binding that column "
                          "provides was never active in it")
            self.assertIn("scheme", have)

            got = conn.execute("SELECT * FROM attempt").fetchone()
            self.assertEqual(
                got["row_hash"],
                ledger._row_hash(got["prev_hash"], dict(got),
                                 dict(got).get("scheme") or "v4-chain-1"),
                "migrating must not invalidate rows written before it")


class CheckerRunsOnTheKernelsInterpreter(unittest.TestCase):
    """The checker's bytes are hashed; the interpreter was not."""

    def test_argv_does_not_resolve_off_path(self):
        src = (Path(__file__).resolve().parents[1] / "kernel" / "runner.py").read_text()
        self.assertNotIn(
            '["python3", str(checker_path)', src,
            "`python3` resolves off a PATH the worker controls, so a shim in "
            "front of it decides every checker's exit code -- measured, and it "
            "turned a FAIL into a PASS")
        self.assertIn("sys.executable, str(checker_path)", src)

    def test_a_path_shim_does_not_decide_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            shim = Path(td) / "python3"
            shim.write_text("#!/bin/sh\nexit 0\n")
            shim.chmod(0o755)

            root = Path(__file__).resolve().parents[1]
            subject = Path(td) / "subject.json"
            subject.write_text(
                '{"claim_id":"x","claim_kind":"x","task_id":"x","repo_root":".",'
                '"diff_base":"HEAD","subject_refs":[{"kind":"file",'
                '"path":"docs/SPEC.md"}],"symbol":"","variant":"","params":{}}')

            env = dict(os.environ, PATH=f"{td}{os.pathsep}{os.environ['PATH']}")
            r = subprocess.run(
                [sys.executable, "-m", "kernel.cli", "--repo", ".", "run-checker",
                 "--checker", "secret", "--subject", str(subject)],
                cwd=root, env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(
                r.returncode, 0,
                "a checker's verdict came from a shim on PATH rather than from "
                "the checker whose hash the kernel had just verified")


if __name__ == "__main__":
    unittest.main()


class HookOutputMatchesWhatTheHostReads(unittest.TestCase):
    """A hook that decides to block and prints the wrong shape has allowed it.

    `PreToolUse` reads `hookSpecificOutput.permissionDecision`; the top-level
    `decision` field is not supported for that event. `Stop` is the opposite --
    it reads `decision`. Getting either backwards produces a hook that runs,
    logs, decides, exits 0, and permits. There is no error anywhere.
    """

    HOOKS = Path(__file__).resolve().parents[1] / "hooks"

    def test_pretooluse_hook_denies_in_the_shape_pretooluse_reads(self):
        src = (self.HOOKS / "write_block.py").read_text()
        self.assertIn('"permissionDecision": "deny"', src)
        self.assertIn('"hookEventName": "PreToolUse"', src)
        self.assertNotIn(
            '"decision": "block"', src,
            "PreToolUse ignores the top-level `decision` field, so a hook "
            "printing it has permitted the write it meant to refuse")

    def test_stop_hook_blocks_in_the_shape_stop_reads(self):
        """Run it, rather than grep it.

        This was `assertIn('"decision": "block"', src)`, and `stop_gate.py`
        line 19 carries that exact string in its module docstring -- so the
        assertion held with both live payloads changed to `allow`, verified:
        the Stop gate blocking nothing left 426 tests green. The two other
        tests of this hook (`StopGateAsksOnce`) both exercise early returns,
        so the branch that blocks had nothing on it at all.

        `permissionDecision` is still asserted against the source, because it
        is the *absence* of a spelling that is being pinned and no run can show
        that -- Stop reads top-level `decision`, and `PreToolUse`'s shape here
        would be a refusal nothing reads.
        """
        self.assertNotIn("permissionDecision",
                         (self.HOOKS / "stop_gate.py").read_text())
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["**"])
            (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
                {"probe": {"question_template": "q", "checker": "probe",
                           "staleness": "repo"}}))
            (root / ".v4" / "checkers.json").write_text("{}")
            (root / ".v4" / "config.json").write_text(json.dumps(
                {"test_command": "true", "policy": "allow_accepted_risk"}))
            conn = ledger.connect(root)
            with ledger.writing(conn):
                ledger.insert(conn, "claim", id="c1", task_id="t-hook",
                              kind="probe", question="q", subject_refs="[]",
                              checker="probe", origin="derive",
                              created_at="2026")
            conn.close()
            env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            r = subprocess.run(
                [sys.executable, str(self.HOOKS / "stop_gate.py")],
                input=json.dumps({"hook_event_name": "Stop"}),
                capture_output=True, text=True, env=env, cwd=str(root),
                timeout=60)
        out = json.loads(r.stdout or "{}")
        self.assertEqual(out.get("decision"), "block", r.stdout + r.stderr)
        self.assertIn("still open", out.get("reason", ""))

    def _repo_with_one_task(self, td, scope):
        """A repo where exactly one task is open, scoped to `scope`."""
        root = Path(td)
        (root / ".v4").mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "a.py").write_text("x = 1\n")
        (root / "b.py").write_text("y = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t-hook", request="r" * 80,
                      scope_globs=json.dumps(scope), base_commit=head,
                      created_at="2026-01-01T00:00:00+00:00")
        conn.close()
        return root

    def _run_hook(self, root, tool_input, tool_name="Write"):
        env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        r = subprocess.run([sys.executable, str(self.HOOKS / "write_block.py")],
                           input=json.dumps({"tool_name": tool_name,
                                             "tool_input": tool_input}),
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, "a guard hook must exit 0; a non-zero "
                                          "exit without a deny payload fails open")
        return json.loads(r.stdout or "{}")

    def test_write_block_actually_emits_a_deny(self):
        """Not the source -- the bytes it writes when it refuses.

        This ran against the repo itself with `V4_TASK=t-stop`, a task that does
        not exist, so `current_scope` returned None, the hook stood down with
        `{}`, and the assertion sat behind `if out:` and never ran. Replacing the
        deny payload with `print("{}")` -- the hook failing open for every write
        in every repo -- left the suite green. The scope here is built so the
        refusal is the only correct answer.
        """
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            out = self._run_hook(root, {"file_path": str(root / "b.py")})
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            f"b.py is outside the scope ['a.py'] and the hook allowed it: {out!r}")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_a_v4_task_naming_an_ended_task_does_not_win(self):
        """A shell outlives a task, and the hook trusted the shell.

        Measured on the reference adopter: `V4_TASK=t-e2e-green` was exported,
        that task was abandoned, a new one was opened, and every write for the
        whole of the new task was checked against the abandoned task's scope and
        denied. The worker could not write a line, and the refusal named a task
        the ledger says is over. `V4_TASK` exists to say which of the *open*
        tasks a write is for; a finished one is not one of them.
        """
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            conn = ledger.connect(root)
            ledger.insert(conn, "event", task_id="t-hook", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
            ledger.insert(conn, "task", id="t-now", request="r" * 80,
                          scope_globs=json.dumps(["b.py"]), base_commit="x",
                          created_at="2026-01-02T00:00:00+00:00")
            conn.close()
            env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            r = subprocess.run(
                [sys.executable, str(self.HOOKS / "write_block.py")],
                input=json.dumps({"tool_name": "Write",
                                  "tool_input": {"file_path": str(root / "b.py")}}),
                capture_output=True, text=True, env=env, timeout=60)
            out = json.loads(r.stdout or "{}")
        self.assertEqual(
            out, {},
            f"b.py is in the open task's scope and the hook judged it against a "
            f"task that has ended: {out!r}")

    def test_an_ended_v4_task_is_recorded_not_swallowed(self):
        """Dropping what somebody told the hook has to leave a trace."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            conn = ledger.connect(root)
            ledger.insert(conn, "event", task_id="t-hook", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
            ledger.insert(conn, "task", id="t-now", request="r" * 80,
                          scope_globs=json.dumps(["b.py"]), base_commit="x",
                          created_at="2026-01-02T00:00:00+00:00")
            conn.close()
            env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            subprocess.run(
                [sys.executable, str(self.HOOKS / "write_block.py")],
                input=json.dumps({"tool_name": "Write",
                                  "tool_input": {"file_path": str(root / "b.py")}}),
                capture_output=True, text=True, env=env, timeout=60)
            conn = ledger.connect(root)
            marks = conn.execute(
                "SELECT task_id, payload FROM event WHERE kind='hook_seen'").fetchall()
            conn.close()
        self.assertTrue(marks, "the hook decided and recorded nothing")
        self.assertEqual(marks[-1]["task_id"], "t-now")
        payload = json.loads(marks[-1]["payload"])
        # In `note`, not in `basis`. `basis` is an enum `unengaged` reads back
        # by equality -- `json_extract(payload, '$.basis') = 'cleared'` -- and
        # concatenating this sentence onto it meant the engagement gate was
        # never marked spent in exactly the case `open_task` documents as
        # ordinary: a shell outliving its task. The fact still has to be on the
        # record, which is what this asserts; the field it lives in is the one
        # nothing compares.
        self.assertIn("t-hook", payload.get("note", ""),
                      "the mark does not say the hook ignored what it was told")
        self.assertNotIn("t-hook", payload.get("basis", ""),
                         "`basis` is compared by equality and must stay a value")

    def test_an_ended_v4_task_with_nothing_open_stands_down(self):
        """The one case this change makes more permissive, on purpose.

        Before, a stale `V4_TASK` meant writes were judged against a finished
        task's scope even when nothing was open. Now the hook stands down --
        which is already what it does for everybody with no open task, and an
        ended task has no claim on writes made after it ended. `ship` reports a
        task that ran without the hook, so the loss is recorded rather than
        silent. Written down as a test because a widening nobody asserted is a
        widening nobody chose.
        """
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            conn = ledger.connect(root)
            ledger.insert(conn, "event", task_id="t-hook", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
            conn.close()
            env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            r = subprocess.run(
                [sys.executable, str(self.HOOKS / "write_block.py")],
                input=json.dumps({"tool_name": "Write",
                                  "tool_input": {"file_path": str(root / "b.py")}}),
                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(json.loads(r.stdout or "{}"), {})

    def test_the_stop_gate_treats_abandon_as_an_ending(self):
        """`_shipped` asked only for `shipped`, and the gate exists to stop a
        turn ending on a task *nobody tried to end*. `abandon` is one of the two
        ways to end one, so a task somebody had deliberately closed still held
        the session -- reachable whenever `V4_TASK` names it, because
        `_open_task` would never return it.
        """
        import importlib.util as _u
        spec = _u.spec_from_file_location("sg", self.HOOKS / "stop_gate.py")
        sg = _u.module_from_spec(spec); spec.loader.exec_module(sg)
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            self.assertFalse(sg._ended(root, "t-hook"),
                             "an open task has not been ended")
            conn = ledger.connect(root)
            ledger.insert(conn, "event", task_id="t-hook", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
            conn.close()
            self.assertTrue(
                sg._ended(root, "t-hook"),
                "the task was abandoned and the gate still calls it unfinished")

    def test_the_stop_gate_drops_a_v4_task_that_ended(self):
        """The same rule `write_block` holds, in the hook that did not hold it."""
        import importlib.util as _u
        spec = _u.spec_from_file_location("sg2", self.HOOKS / "stop_gate.py")
        sg = _u.module_from_spec(spec); spec.loader.exec_module(sg)
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            conn = ledger.connect(root)
            ledger.insert(conn, "event", task_id="t-hook", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
            ledger.insert(conn, "task", id="t-live", request="r" * 80,
                          scope_globs=json.dumps(["b.py"]), base_commit="x",
                          created_at="2026-01-02T00:00:00+00:00")
            conn.close()
            old = os.environ.get("V4_TASK")
            os.environ["V4_REPO"], os.environ["V4_TASK"] = str(root), "t-hook"
            try:
                # Three values. The third is the `V4_TASK` that was dropped for
                # naming an ended task, and it is what this case is named
                # after: it used to be discarded with no trace, so a session
                # whose shell pointed at a finished task was accountable for
                # its writes and not for its stops.
                _, task, dropped = sg._repo_and_task()
            finally:
                os.environ.pop("V4_REPO", None)
                if old is None:
                    os.environ.pop("V4_TASK", None)
                else:
                    os.environ["V4_TASK"] = old
        self.assertEqual(
            task, "t-live",
            "a V4_TASK naming an ended task decided which claims hold the session")
        self.assertEqual(
            dropped, "t-hook",
            "the ended task was dropped without a word, so a session pointing "
            "at it is answerable for its writes and not for its stops")

    def test_a_v4_task_that_is_open_still_wins(self):
        """The other half: explicit still beats inferred while it is live."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            (root / ".v4" / "home").write_text(
                str(Path(__file__).resolve().parents[1]))
            conn = ledger.connect(root)
            ledger.insert(conn, "task", id="t-other", request="r" * 80,
                          scope_globs=json.dumps(["b.py"]), base_commit="x",
                          created_at="2026-01-02T00:00:00+00:00")
            conn.close()
            env = dict(os.environ, V4_REPO=str(root), V4_TASK="t-hook",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            r = subprocess.run(
                [sys.executable, str(self.HOOKS / "write_block.py")],
                input=json.dumps({"tool_name": "Write",
                                  "tool_input": {"file_path": str(root / "b.py")}}),
                capture_output=True, text=True, env=env, timeout=60)
            out = json.loads(r.stdout or "{}")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            "two tasks are open, V4_TASK named the one whose scope excludes "
            f"b.py, and it was not honoured: {out!r}")

    def test_write_block_allows_what_is_in_scope(self):
        """The other half: a deny that fires on everything is not a guard."""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            out = self._run_hook(root, {"file_path": str(root / "a.py")})
        self.assertEqual(out, {}, f"a.py is in scope and was refused: {out!r}")

    def test_notebook_edit_is_judged_by_the_key_notebook_edit_sends(self):
        """`NotebookEdit` is in the matcher and sends `notebook_path`.

        The hook read `file_path` only, so `path` was always None for this tool
        and it returned the allow payload before the scope check, the engagement
        gate and the `hook_seen` mark. A notebook write was ungoverned and left
        no trace that it had been.
        """
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            out = self._run_hook(root, {"notebook_path": str(root / "b.ipynb")},
                                 tool_name="NotebookEdit")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            f"a NotebookEdit outside the scope was allowed: {out!r}")

    def test_a_refused_widen_does_not_widen(self):
        """`widen` records the event and then raises, and the readers summed
        every row. So the CLI printed REFUSED, exited 2, and the scope had grown
        for this hook, for `derive` and for the `scope` checker.
        """
        from kernel import config as config_mod, scope as scope_mod
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_one_task(td, ["a.py"])
            src = json.loads((Path(__file__).resolve().parents[1]
                              / ".v4" / "config.json").read_text())
            keep = {k: src[k] for k in ("policy", "thresholds") if k in src}
            keep["test_command"] = "true"
            (root / ".v4" / "config.json").write_text(json.dumps(keep))
            conn = ledger.connect(root)
            cfg = config_mod.RepoConfig(root)
            with self.assertRaises(scope_mod.WidenRefused):
                # Long enough to clear the length pre-check, which raises before
                # the event is written and so misses this bug entirely, and
                # naming none of the added paths so the engagement judge is what
                # refuses it -- that is the path that records and then raises.
                scope_mod.widen(conn, cfg, task_id="t-hook",
                                add=["vendor/thirdparty/**"],
                                why="the weather is fine today and on the whole "
                                    "this change looks broadly acceptable to me "
                                    "so it should simply be allowed through")
            self.assertEqual(scope_mod.current_scope(conn, "t-hook"), ["a.py"],
                             "the widen was refused and the scope grew anyway")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM event WHERE kind='scope_widen'"
                             ).fetchone()[0], 1,
                "the refused attempt must stay on the record; it just must not count")
            conn.close()
            out = self._run_hook(
                root, {"file_path": str(root / "vendor" / "thirdparty" / "x.py")})
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            f"the hook honoured a widen the kernel refused: {out!r}")




class TheShellGuardsRollCall(unittest.TestCase):
    """Four ways past it, each measured returning `[]` before this.

    A whitelist of writers is only as good as its roll call, which is the
    argument SPEC.md §10 makes against whitelists generally. It is kept rather
    than replaced because the alternative is refusing every command that names
    a protected path, and reading them is free -- so the roll call is asserted
    instead, one row per evasion that worked.
    """

    P = [".v4/**", "checkers/**", "detectors/**", ".github/**"]

    def _hits(self, cmd):
        from kernel.analysis.shell_command import writes_to_protected
        return writes_to_protected(cmd, self.P)

    def test_a_harmless_first_line_no_longer_hides_the_second(self):
        """A newline went to the whitespace branch, so a multi-line payload was
        one segment and only its first word was ever read as a command."""
        self.assertTrue(self._hits("sed -i s/a/b/ .v4/config.json"))
        self.assertTrue(self._hits("echo hi\nsed -i s/a/b/ .v4/config.json"))
        self.assertTrue(self._hits("cd /tmp\necho a\nrm -rf checkers/"))

    def test_a_destination_named_first_is_still_the_destination(self):
        """GNU `cp`/`install` take `-t DIR SRC...`, and `operands[-1:]` picked
        the source -- a read reported as the write."""
        for cmd in ("cp -t .v4 /tmp/config.json",
                    "install -t checkers /tmp/evil.py",
                    "cp --target-directory=.v4 /tmp/config.json"):
            self.assertTrue(self._hits(cmd), cmd)

    def test_git_writes_through_its_subcommands(self):
        """`git` was absent from the table entirely, so three ways to revert or
        delete the files that judge the work returned `[]`."""
        for cmd in ("git checkout -- .v4/config.json",
                    "git restore .v4/",
                    "git rm .v4/config.json",
                    "git apply /tmp/p.diff checkers/scope.py"):
            self.assertTrue(self._hits(cmd), cmd)

    def test_git_reading_is_still_free(self):
        """Listing `git` under ALL_ARGS would report `git status .v4` as a
        write, which is why the subcommand is read rather than the name."""
        for cmd in ("git status .v4", "git log -- checkers/", "git diff .v4"):
            self.assertEqual(self._hits(cmd), [], cmd)

    def test_the_three_that_create_rather_than_edit(self):
        for cmd in ("touch .v4/config.json", "mkdir -p .v4/foo",
                    "patch -p1 checkers/scope.py"):
            self.assertTrue(self._hits(cmd), cmd)


class ShellGuardJudgesRedirectionByTokenNotByPattern(unittest.TestCase):
    """`echo "a > b"` is one word; `echo a > b` is a redirect.

    The distinction is decided once, at tokenise time, and every consumer after
    that trusts the token kind. A pattern would need a heuristic here and the
    heuristic is what gets it wrong.
    """

    P = [".v4/**", "checkers/**", "detectors/**", ".github/**"]

    def _blocked(self, cmd):
        from kernel.analysis.shell_command import writes_to_protected
        r = writes_to_protected(cmd, self.P)
        return r is None or bool(r)

    def test_real_writes_are_refused(self):
        for cmd in ("echo x > .v4/config.json",
                    "sed -i '' 's/a/b/' .v4/config.json",
                    "cp /tmp/x checkers/scope.py",
                    "rm -rf detectors/",
                    "tee .v4/config.json < /dev/null",
                    "printf x >> checkers/test.py",
                    "dd of=.v4/config.json if=/dev/zero",
                    "mv /tmp/x .github/workflows/v4.yml"):
            self.assertTrue(self._blocked(cmd), cmd)

    def test_quoted_and_reading_forms_are_allowed(self):
        for cmd in ('echo "x > .v4/config.json"',
                    "echo 'a > b'",
                    'grep -n ">" .v4/config.json',
                    "cat .v4/config.json",
                    "ls -la .v4/",
                    "git log --oneline",
                    "echo hi > /tmp/out",
                    "dd if=.v4/x of=/tmp/y",
                    "for f in src/*.py; do python3 -m py_compile $f; done"):
            self.assertFalse(self._blocked(cmd), cmd)

    def test_unparseable_fails_closed_only_when_a_protected_path_is_named(self):
        self.assertTrue(self._blocked('eval "rm .v4/config.json"'))
        self.assertTrue(self._blocked('$(printf "rm .v4/config.json")'))
        # Unsupported syntax with nothing protected in it is not this guard's
        # business. Refusing it anyway is how a guard becomes the thing that
        # gets switched off.
        self.assertFalse(self._blocked('eval "echo hello"'))
        self.assertFalse(self._blocked("for f in *; do echo $f; done"))

    def test_the_hook_emits_the_shape_pretooluse_reads(self):
        src = (Path(__file__).resolve().parents[1] / "hooks" / "bash_guard.py").read_text()
        self.assertIn('"permissionDecision": "deny"', src)
        self.assertNotIn('"decision": "block"', src)


class TwoProcessesAppendingEventsDoNotForkTheChain(unittest.TestCase):
    """`writing()` is a counter, not a lock, and the event chain read its tail
    inside one.

    SQLite's default deferred transaction takes the write lock at the INSERT --
    after both readers have seen the same tail -- and several worktrees share
    this file by design. `append_attempt` was given `BEGIN IMMEDIATE` in its
    first version with a docstring naming this exact race; the event chain,
    added later, was not.

    Reproduced before the fix: four processes, 40 events each, 3 linkage breaks
    and 4 rows sharing a prev_hash. `hook_seen` is the most frequent event
    there is, and two sessions in one repo write them at once.
    """

    WRITERS, PER_WRITER = 4, 25

    @staticmethod
    def _write(root, tag, n):
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from kernel import ledger as _l
        conn = _l.connect(Path(root))
        for i in range(n):
            with _l.writing(conn):
                # `hook`, not the writer's tag. `actor` is a gated vocabulary
                # now -- `ledger.ACTORS` -- and which of four processes wrote a
                # row is not a fact that column answers. The tag was already in
                # the payload beside it, which is where this case reads it.
                _l.insert(conn, "event", task_id="t", claim_id=None,
                          kind="hook_seen", actor="hook",
                          payload={"n": i, "who": tag}, created_at="2026")
        conn.close()

    def test_the_chain_still_links(self):
        from multiprocessing import Process
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        conn.close()

        ps = [Process(target=self._write, args=(str(root), f"w{k}", self.PER_WRITER))
              for k in range(self.WRITERS)]
        for p in ps:
            p.start()
        for p in ps:
            p.join()

        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT prev_hash, row_hash FROM event "
                            "WHERE row_hash != '' ORDER BY id").fetchall()
        self.assertEqual(len(rows), self.WRITERS * self.PER_WRITER)
        prev, breaks = None, 0
        for r in rows:
            if prev is not None and r["prev_hash"] != prev:
                breaks += 1
            prev = r["row_hash"]
        self.assertEqual(breaks, 0, "two kernels read the same tail and forked")


class AForkIsNotATamper(unittest.TestCase):
    """Three faults reached one line and it named all three.

    A break in the linkage was reported as "a row was removed, reordered, or
    inserted", and `ship` held on it. For a concurrent append every one of
    those sentences is false: nothing was edited (each row still hashes over
    its own content), nothing was removed (no id is missing), and the
    predecessor each row names is in the chain -- just not the newest one.

    Measured on the reference adopter: 21 of them, all `hook_seen`, all inside
    the ten minutes two sessions were running at once, and the repo could not
    ship afterwards. The ledger is append-only by design, so there is no
    repair -- which makes a fatal verdict here one of this framework's own
    writer bugs becoming permanent.
    """

    def _repo(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for i in range(4):
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="hook_seen", actor="hook",
                          payload={"n": i}, created_at="2026")
        return root, conn

    def _rows(self, conn):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM event WHERE row_hash != '' ORDER BY id")]

    def test_an_untouched_chain_reports_nothing(self):
        _root, conn = self._repo()
        self.assertEqual(ledger._walk_events(conn), [])

    def test_a_fork_is_reported_and_is_not_fatal(self):
        """Written the way two kernels write it: the newest row names the row
        before the one it followed."""
        _root, conn = self._repo()
        rows = self._rows(conn)
        forked = dict(rows[-1])
        forked["prev_hash"] = rows[-3]["row_hash"]
        forked.pop("id"), forked.pop("row_hash")
        forked["row_hash"] = ledger._event_hash(forked["prev_hash"], forked)
        with ledger.writing(conn):
            conn.execute(
                f"INSERT INTO event ({', '.join(forked)}) VALUES "
                f"({', '.join('?' * len(forked))})", tuple(forked.values()))
            conn.commit()
        said = ledger._walk_events(conn)
        self.assertTrue(said)
        self.assertTrue(any(p.startswith(ledger._FORK_PREFIX) for p in said), said)
        self.assertEqual(ledger.fatal(said), [],
                         "a fork held the task, and it cannot be repaired")

    def test_an_edited_row_is_still_fatal(self):
        """The control, and the thing the chain is for."""
        _root, conn = self._repo()
        conn.execute("DROP TRIGGER IF EXISTS no_update_event")
        conn.execute("UPDATE event SET actor = 'someone else' WHERE id = "
                     "(SELECT max(id) FROM event)")
        conn.commit()
        said = ledger._walk_events(conn)
        self.assertTrue(ledger.fatal(said), said)
        self.assertTrue(any("edited after it was written" in p for p in said), said)

    def test_a_predecessor_that_is_not_in_the_chain_is_still_fatal(self):
        """A removed or reordered row leaves a `prev_hash` pointing at a hash
        that is not there. That is the half a fork does not look like."""
        _root, conn = self._repo()
        rows = self._rows(conn)
        orphan = dict(rows[-1])
        orphan["prev_hash"] = "f" * 64
        orphan.pop("id"), orphan.pop("row_hash")
        orphan["row_hash"] = ledger._event_hash(orphan["prev_hash"], orphan)
        with ledger.writing(conn):
            conn.execute(
                f"INSERT INTO event ({', '.join(orphan)}) VALUES "
                f"({', '.join('?' * len(orphan))})", tuple(orphan.values()))
            conn.commit()
        said = ledger._walk_events(conn)
        self.assertTrue(ledger.fatal(said), said)
        self.assertTrue(any("removed, reordered, or inserted" in p
                            for p in ledger.fatal(said)), said)
