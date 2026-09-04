"""One fact, twelve sightings: an entry asked "which one" and asked it wrong.

    python3 -m unittest tests.test_which_repo_which_task_which_environment -v

Every case below is an entry point resolving an identity from something
ambient. Which repo a hook guards came from a relative `V4_REPO` against the
process cwd. Which task the stop gate is about came from "the newest one open".
Which environment verifies a facts table came from a skip nobody printed. Which
files count against a ceiling came from a subject the checker never passed on.

Each of these fails against the commit before this file, and each of them
enters the symbol its finding names rather than reading its source.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

import _framework                                               # noqa: E402
import stop_gate                                                # noqa: E402
import write_block                                              # noqa: E402
from kernel import cli, doctrine, hashing, ledger, lifecycle     # noqa: E402
from kernel import config as config_mod                         # noqa: E402
from kernel import risk as risk_mod                             # noqa: E402
from kernel import runner as runner_mod                         # noqa: E402
from kernel import state as state_mod                           # noqa: E402


def _scratch(case) -> Path:
    """A git repo with a `.v4/` and nothing else, thrown away afterwards."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp,
                   capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "protected_paths": [".v4/**"]}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


@contextlib.contextmanager
def _env(**over):
    """Set and unset environment variables around a block.  `None` removes."""
    was = {k: os.environ.get(k) for k in over}
    for k, v in over.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _in(path: Path):
    was = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(was)


class AWriteHookGuardsTheRepoAndNotTheCwd(unittest.TestCase):
    """`hooks/write_block.py::main`, from a subdirectory.

    `V4_REPO` shipped as `"."` and a hook's cwd is the shell's, so one `cd`
    moved the boundary: `Path(payload).resolve().relative_to(repo_root)` raised
    `ValueError`, `main` printed `{}` -- which the platform reads as allow --
    and left no `hook_seen` row either, so afterwards it looked like a hook
    nobody had installed.
    """

    def _decide(self, repo: Path, cwd: Path, target: Path) -> dict:
        payload = {"tool_name": "Write", "session_id": "s",
                   "tool_input": {"file_path": str(target)}}
        said = io.StringIO()
        # `V4_TASK` cleared: with a task named, `main` takes the scope branch
        # and this case is about the branch that runs when nothing has declared
        # any work -- which `ledger.ENDED_TASKS_SQL` makes the normal state for
        # a review or monitor session.
        with _env(V4_REPO=".", V4_TASK=None), _in(cwd):
            with contextlib.redirect_stdout(said):
                with unittest.mock.patch("sys.stdin",
                                         io.StringIO(json.dumps(payload))):
                    write_block.main()
        return json.loads(said.getvalue() or "{}")

    def test_the_same_write_is_refused_from_a_subdirectory(self):
        repo = _scratch(self)
        (repo / "sub" / "deeper").mkdir(parents=True)
        answer = self._decide(repo, repo / "sub" / "deeper",
                              repo / ".v4" / "config.json")
        self.assertEqual(
            answer.get("hookSpecificOutput", {}).get("permissionDecision"),
            "deny", answer)

    def test_and_from_the_root_it_always_was(self):
        """The half that already worked has to keep working, or the repair is
        an off switch."""
        repo = _scratch(self)
        answer = self._decide(repo, repo, repo / ".v4" / "config.json")
        self.assertEqual(
            answer.get("hookSpecificOutput", {}).get("permissionDecision"),
            "deny", answer)

    def test_a_file_that_is_not_protected_still_goes_through(self):
        repo = _scratch(self)
        (repo / "sub").mkdir()
        (repo / "app.py").write_text("x = 1\n")
        self.assertEqual(self._decide(repo, repo / "sub", repo / "app.py"), {})


class TheStopGateWillNotPickATask(unittest.TestCase):
    """`hooks/stop_gate.py::_open_task` with more than one task open.

    It called `ledger.open_task_id`, whose own docstring says callers that must
    not guess use `open_task_ids` and refuse -- which is what
    `hooks/write_block.open_task` does. Measured 2026-08-27: one session, three
    tasks open, and the gate asked it whether to ship a task it had never
    touched.
    """

    def _two_open(self, case=None):
        repo = _scratch(self)
        conn = ledger.connect(repo)
        self.addCleanup(conn.close)
        for tid in ("t-first", "t-second"):
            ledger.insert(conn, "task", id=tid, request="r",
                          scope_globs=["app/**"], base_commit="",
                          created_at="2026")
        return repo, conn

    def test_it_refuses_instead_of_taking_the_newest(self):
        repo, _conn = self._two_open()
        got = stop_gate._open_task(repo)
        self.assertNotIn(got, ("t-first", "t-second"),
                         "the gate picked a task with nothing saying which")
        self.assertIs(got, stop_gate.AMBIGUOUS)

    def test_one_task_open_is_still_answered(self):
        repo = _scratch(self)
        conn = ledger.connect(repo)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t-only", request="r",
                      scope_globs=["app/**"], base_commit="", created_at="2026")
        self.assertEqual(stop_gate._open_task(repo), "t-only")

    def test_a_session_that_marked_one_of_them_is_asked_about_that_one(self):
        """Refusing is friction and it should land on the person it is about.
        A mark from this session on exactly one open task answers the
        question, so nothing is guessed and nobody is asked."""
        repo, _conn = self._two_open()
        _framework.record_seen(repo, "t-first", "app/x.py", session="mine")
        self.assertEqual(stop_gate._open_task(repo, "mine"), "t-first")

    def test_a_mark_the_guard_sprayed_across_every_task_is_not_evidence(self):
        """`bash_guard` marks every open task because a shell command names
        none, and `write_block` does the same when two are open. Counting those
        as "this session worked here" is what let `_worked_here` stand behind a
        task nobody had claimed."""
        repo, conn = self._two_open()
        _framework.record_seen(repo, "t-first", "sed", session="mine",
                               scattered=True)
        _framework.record_seen(repo, "t-second", "sed", session="mine",
                               scattered=True)
        self.assertEqual(ledger.sessions_on(conn, "t-first", "mine"), (0, 0))
        self.assertIs(stop_gate._open_task(repo, "mine"), stop_gate.AMBIGUOUS)

    def test_the_turn_is_blocked_and_the_tasks_are_named(self):
        repo, _conn = self._two_open()
        payload = {"session_id": "mine", "stop_hook_active": False,
                   "hook_event_name": "Stop"}
        said = io.StringIO()
        with _env(V4_REPO=str(repo), V4_TASK=None):
            with contextlib.redirect_stdout(said):
                with unittest.mock.patch("sys.stdin",
                                         io.StringIO(json.dumps(payload))):
                    stop_gate.main()
        answer = json.loads(said.getvalue() or "{}")
        self.assertEqual(answer.get("decision"), "block", answer)
        self.assertIn("t-first", answer["reason"])
        self.assertIn("t-second", answer["reason"])


class InitLeavesTheFlagThatKeepsTheFileItWrote(unittest.TestCase):
    """`kernel/cli.py::cmd_init` wrote CLAUDE.md and not `"doctrine": true`.

    `doctrine.drift` is opt-in through that key, so every repo adopted through
    `v4 init` carried a layer-1 file whose deletion produced no finding at all.
    """

    def _init(self, repo: Path):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = cli.cmd_init(argparse.Namespace(repo=str(repo)))
        self.assertEqual(code, 0, said.getvalue())
        return said.getvalue()

    def test_the_opt_in_is_written(self):
        repo = _scratch(self)
        (repo / ".v4" / "config.json").unlink()
        (repo / ".v4" / "claim_kinds.json").unlink()
        self._init(repo)
        self.assertIs(
            json.loads((repo / ".v4" / "config.json").read_text()).get("doctrine"),
            True)

    def test_and_deleting_the_file_is_now_a_finding(self):
        """The measurement the flag exists for: with the key absent `drift`
        returns None for a CLAUDE.md that is not there."""
        repo = _scratch(self)
        (repo / ".v4" / "config.json").unlink()
        (repo / ".v4" / "claim_kinds.json").unlink()
        self._init(repo)
        (repo / "CLAUDE.md").unlink()
        said = doctrine.drift(config_mod.RepoConfig(repo))
        self.assertIsNotNone(said)
        self.assertIn("CLAUDE.md", said)


class TheCeilingCountsWhatTheRepoSaysToCount(unittest.TestCase):
    """`checkers/control_plane_budget.py::main` read `repo_root` and threw the
    rest of the subject away, so `tracked(subject or {})` always got `{}` and
    `derive_exclude` never applied to anything -- while `measure`'s own comment
    gives honouring it as the reason it stopped using `rglob`."""

    @staticmethod
    def _checker():
        spec = importlib.util.spec_from_file_location(
            "control_plane_budget_under_test",
            ROOT / "checkers" / "control_plane_budget.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run(self, repo: Path, exclude) -> int:
        subject = repo / "subject.json"
        subject.write_text(json.dumps(
            {"repo_root": str(repo), "params": {"derive_exclude": exclude}}))
        mod = self._checker()
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            with unittest.mock.patch.object(
                    sys, "argv", ["c", "--subject", str(subject)]):
                code = mod.main()
        self.last = said.getvalue()
        return code

    def _repo_with_a_fixture(self) -> Path:
        repo = _scratch(self)
        (repo / "kernel").mkdir()
        (repo / "kernel" / "small.py").write_text("x = 1\n")
        # A red fixture: deliberately broken code that `v4 install` copies into
        # every adopter, which is exactly what `derive_exclude` names.
        (repo / ".v4" / "fixtures").mkdir()
        big = "\n".join(f"v{i} = {i}" for i in range(200)) + "\n"
        (repo / ".v4" / "fixtures" / "red.py").write_text(big)
        (repo / ".v4" / "control_plane_budget.json").write_text(json.dumps(
            {"ceiling": 10, "why": "small on purpose"}))
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        return repo

    def test_the_excluded_fixture_does_not_count(self):
        repo = self._repo_with_a_fixture()
        self.assertEqual(self._run(repo, [".v4/fixtures/**"]), 0, self.last)

    def test_and_without_the_exclusion_it_does(self):
        """The control: the 200 statements are really there, so a pass above is
        the exclusion working rather than the walk finding nothing."""
        repo = self._repo_with_a_fixture()
        self.assertEqual(self._run(repo, []), 1, self.last)


class TheTableThisSuiteDoesNotCheck(unittest.TestCase):
    """`tests/test_facts.py::setUpClass` opted out in a sentence that named
    nothing, so `run_without_silent_skips` printed no line and the oracle exited
    0 with `OK (skipped=1)` over a table verified in no environment at all."""

    def test_the_opt_out_names_what_goes_unchecked(self):
        import tests.test_facts as tf
        with _env(V4_ADOPTER_REPO=None):
            with self.assertRaises(unittest.SkipTest) as caught:
                tf.AgainstAdopterTable.setUpClass()
        said = str(caught.exception)
        self.assertIn("facts.adopter_a.json", said)
        self.assertIn("seen_at", said)

    def test_and_the_reason_stays_out_of_the_environment_class(self):
        """It must not read as "the world was smaller": unset is a decision,
        and matching would make the sole oracle for every `test` claim
        permanently red for a question CI can never put."""
        import tests.run_without_silent_skips as oracle
        import tests.test_facts as tf
        with _env(V4_ADOPTER_REPO=None):
            with self.assertRaises(unittest.SkipTest) as caught:
                tf.AgainstAdopterTable.setUpClass()
        self.assertIsNone(oracle.ENVIRONMENT.search(str(caught.exception)))

    def test_the_oracle_prints_a_declared_opt_out(self):
        import tests.run_without_silent_skips as oracle

        class Case(unittest.TestCase):
            def runTest(self):
                self.skipTest("a decision, not a smaller world")

        oracle.LoudSkips.env_skips = []
        oracle.LoudSkips.declared_skips = []
        said = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            unittest.TextTestRunner(resultclass=oracle.LoudSkips,
                                    stream=io.StringIO()).run(Case())
        self.assertEqual(len(oracle.LoudSkips.declared_skips), 1)


class TheReaderRunnerIsHandedActuallyRuns(unittest.TestCase):
    """`kernel/lifecycle.py::_latest_attempt_id_of` built its closure over
    `ledger.latest_attempt_id`, and this module binds `ledger_mod` -- so every
    call raised `NameError`. Nothing noticed for its whole existence, because
    `hashing.subject_digest` reaches it only for a subject ref whose kind is not
    `file`, and no claim in this ledger has ever had one."""

    def _claim_with_an_attempt(self):
        repo = _scratch(self)
        conn = ledger.connect(repo)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="runtime-proof",
                      question="q", subject_refs="[]", checker="x",
                      origin="derive", file=None, symbol="", variant="",
                      line=None, note="", detector=None, detector_sha=None,
                      created_at="2026")
        ledger.append_attempt(conn, claim_id="c1", subject_digest="{}",
                              checker_sha="", config_sha="", head_commit="",
                              worktree=str(repo), argv="[]", exit_code=0,
                              stdout="", stderr="", started_at="2026",
                              ended_at="2026", duration_ms=1)
        return repo, conn

    def test_calling_it_answers_instead_of_raising(self):
        repo, conn = self._claim_with_an_attempt()
        read = lifecycle._latest_attempt_id_of(conn)
        self.assertEqual(read("c1"), ledger.latest_attempt_id(conn, "c1"))

    def test_and_it_reaches_the_route_that_never_ran(self):
        """The way it is actually used: a subject ref that is not a file."""
        repo, conn = self._claim_with_an_attempt()
        digest = hashing.subject_digest(
            repo, [{"kind": "attempt", "claim": "c1"}],
            lifecycle._latest_attempt_id_of(conn))
        self.assertEqual(digest["attempt:c1"],
                         str(ledger.latest_attempt_id(conn, "c1")))


class ASignatureSaysWhatItsAnchorIsWorth(unittest.TestCase):
    """`kernel/risk.py::accept` wrote "this file existing in a commit is the
    durable part" into every record, and no commit in this repo is signed:
    `%G?` is `N`, `commit.gpgsign` and `user.signingkey` unset. The durable half
    was an author line from the same editable git config as the field it was
    written to compensate for."""

    def _signable(self):
        repo = _scratch(self)
        (repo / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo,
                       capture_output=True)
        conn = ledger.connect(repo)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint",
                      question="q?", subject_refs="[]", checker="lint",
                      origin="derive", file="app.py", symbol="", variant="",
                      line=None, note="", detector=None, detector_sha=None,
                      created_at="2026")
        return repo, conn

    def test_the_record_measures_the_commit_it_leans_on(self):
        repo, conn = self._signable()
        record, _path = risk_mod.accept(
            conn, config_mod.RepoConfig(repo), claim_id="c1",
            kind="unprovable", require_tty=False,
            why="this scratch repo signs nothing, and the record has to say so "
                "rather than assert a durability it does not have")
        self.assertIn("commit_anchor", record)
        self.assertIs(record["commit_anchor"]["verified"], False)
        self.assertEqual(record["commit_anchor"]["signature"], "N")

    def test_and_the_note_stops_claiming_the_commit_is_the_durable_part(self):
        repo, conn = self._signable()
        record, _path = risk_mod.accept(
            conn, config_mod.RepoConfig(repo), claim_id="c1",
            kind="unprovable", require_tty=False,
            why="the sentence in the record has to match what git actually says "
                "about the commit this file is going to land in")
        self.assertIn("%G?", record["note"])
        self.assertNotIn("existing in a commit is the durable part",
                         record["note"])


class WhereTheSignatureActuallyIs(unittest.TestCase):
    """`kernel/risk.py::route` spelled the path out -- `.v4/risks/{claim
    id}.json` -- which is the wrong file for a repo-scoped signature, while the
    row it had just read carried `git_record` with the real one."""

    def test_it_names_the_file_the_row_points_at(self):
        repo = _scratch(self)
        conn = ledger.connect(repo)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="secret",
                      question="q?", subject_refs="[]", checker="secret",
                      origin="derive", file=None, symbol="", variant="",
                      line=None, note="", detector=None, detector_sha=None,
                      created_at="2026")
        record = ".v4/risks/repo/secret.json"
        ledger.insert(conn, "accepted_risk", claim_id="c1", kind="unprovable",
                      who="t", why="w" * 60, was_tty=0, git_record=record,
                      subject_digest="{}", created_at="2026", scope="repo",
                      cover_key="")
        row = conn.execute("SELECT * FROM claim WHERE id='c1'").fetchone()
        _what, why = risk_mod.route(conn, config_mod.RepoConfig(repo), row,
                                    state_mod.OPEN)
        self.assertIn(record, why)
        self.assertNotIn(".v4/risks/c1.json", why)


class ACheckerIsNotHandedTheOperatorsShell(unittest.TestCase):
    """`kernel/runner.py::child_env` copied the whole parent environment into
    every checker and every detector subprocess -- 27 plus 30 LLM-authored
    programs whose stdout lands in an append-only table that `ship` exports to a
    committed file."""

    def test_a_credential_in_the_parent_does_not_reach_the_child(self):
        env = runner_mod.child_env(
            {"PATH": "/usr/bin", "HOME": "/h",
             "AWS_SECRET_ACCESS_KEY": "not-a-real-key",
             "DATABASE_URL": "not-a-real-connection-string"})
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("DATABASE_URL", env)

    def test_and_what_a_program_needs_to_run_still_does(self):
        env = runner_mod.child_env(
            {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C", "TMPDIR": "/t",
             "V4_TASK": "t-x", "SECRET_TOKEN": "no"})
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/h")
        self.assertEqual(env["V4_TASK"], "t-x")
        self.assertIn("PYTHONPATH", env)
        self.assertNotIn("SECRET_TOKEN", env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
