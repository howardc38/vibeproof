"""Seven places where a rule was written once and implemented again.

    python3 -m unittest tests.test_one_rule_with_one_implementation -v

Each of these is the same fact: a decision has an owner, and a second program
was making it as well. Where the two answers still agreed, the test pins the
call reaching the owner; where they had already diverged -- `derive`'s glob
matcher, `register`'s probe, `structural_lint`'s idea of a test -- the test
pins the answer that was wrong.

21 of the 23 fail against 0785a00. The two that do not are named as such:
`test_and_a_file_outside_the_scope_is_still_refused` and
`test_and_a_real_config_module_still_is_one` pass on both sides on purpose --
each repair here replaces a narrow answer with a wider one, and these pin the
direction that must not have moved with it.
"""

from __future__ import annotations

import json
import os
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
import stop_gate                                                # noqa: E402
import write_block                                              # noqa: E402
from kernel import (config as config_mod, derive, detector_protocol,  # noqa: E402
                    hashing, ledger, register, risk, runner, state)
from kernel.analysis import (structural_lint, subject_files,     # noqa: E402
                             test_weakened as tw_analysis)


#: Prints the subject it was handed and exits 0. The payload that crossed the
#: boundary, rather than a reconstruction of it.
_ECHO_CHECKER = (
    "import argparse, json, sys, pathlib\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--subject'); p.add_argument('--facts')\n"
    "p.add_argument('--out')\n"
    "a = p.parse_args()\n"
    "print(json.dumps(json.loads(pathlib.Path(a.subject).read_text()),\n"
    "                 sort_keys=True))\n"
    "sys.exit(0)\n")


def _git_repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    return tmp


def _v4_repo(case, **cfg):
    tmp = _git_repo(case)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / ".v4" / "checkers.json").write_text("{}")
    return tmp


class TheScopeADetectorIsGivenAndTheScopeItIsJudgedBy(unittest.TestCase):
    """`derive._in_scope` was a seventh private copy of `subject_files.matches`.

    They disagreed on `**/x`, the rule git already means: with a scope of
    `**/*.py`, `lifecycle._files_in_scope` handed every root-level module to the
    detectors and every claim raised about one was then dropped here as out of
    scope. Nothing failed -- the claims simply never existed.
    """

    def _repo(self):
        root = _v4_repo(self, derive_exclude=[])
        (root / "detectors").mkdir()
        (root / "detectors" / "always_probe.py").write_text(
            'print("V4-CLAIM: kind=scope file=app.py symbol=f")\n')
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"scope": {"checker": "scope", "question_template": "is {file} in?",
                       "applies_to": "always", "staleness": "subject"}}))
        (root / ".v4" / "checkers.json").write_text(json.dumps(
            {"scope": {"path": "checkers/scope.py", "kinds": ["scope"],
                       "sha256": "x", "reads": ["**"]}}))
        (root / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "in"], cwd=root, capture_output=True)
        return root

    def _derive(self, root, scope_globs):
        from kernel import config as config_mod
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r",
                      scope_globs=scope_globs, base_commit="",
                      created_at="2026-08-27T00:00:00+00:00")
        return derive.derive(conn, config_mod.RepoConfig(root), task_id="t1",
                             scope_globs=scope_globs, subject_files=["app.py"],
                             phase="open", detectors_dir=root / "detectors")

    def test_a_root_level_file_is_in_a_double_star_scope(self):
        """`matches('app.py', ['**/*.py'])` is True, and now derive agrees."""
        self.assertTrue(subject_files.matches("app.py", ["**/*.py"]))
        res = self._derive(self._repo(), ["**/*.py"])
        self.assertEqual(len(res["created"]), 1, res)
        self.assertFalse([r for r in res["refused"] if r[1] == "out-of-scope"],
                         res["refused"])

    def test_and_a_file_outside_the_scope_is_still_refused(self):
        res = self._derive(self._repo(), ["docs/**"])
        self.assertEqual(len(res["created"]), 0, res)
        self.assertTrue([r for r in res["refused"] if r[1] == "out-of-scope"],
                        res["refused"])


class WhereTheFrameworkLivesHasOneAnswer(unittest.TestCase):
    """`stop_gate._v4_home` read the home marker and nothing else.

    `V4_HOME` lost, `kernel/` beside `hooks/` lost, and the fallback was the
    repo root -- so an adopter with no marker got a child `PYTHONPATH` with no
    `kernel` on it and the gate stood down every turn, silently.
    """

    def test_v4_home_wins_over_the_marker(self):
        root = _git_repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "home").write_text(str(root / "marker-home"))
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        (elsewhere / "kernel").mkdir()
        old = os.environ.get("V4_HOME")
        os.environ["V4_HOME"] = str(elsewhere)
        try:
            self.assertEqual(_framework.home(root), str(elsewhere))
            self.assertEqual(stop_gate._child_env(root)["PYTHONPATH"]
                             .split(os.pathsep)[0], str(elsewhere))
        finally:
            if old is None:
                os.environ.pop("V4_HOME", None)
            else:
                os.environ["V4_HOME"] = old

    def test_the_child_gets_a_path_a_kernel_is_actually_under(self):
        """The measured failure: a repo with no marker got its own root.

        `python -m kernel.cli` in that child exits with ModuleNotFoundError,
        which `_states` reads as "cannot tell" and the gate stands down.
        """
        adopter = _git_repo(self)
        (adopter / ".v4").mkdir()
        old = os.environ.pop("V4_HOME", None)
        try:
            first = stop_gate._child_env(adopter).get("PYTHONPATH", "") \
                .split(os.pathsep)[0]
        finally:
            if old is not None:
                os.environ["V4_HOME"] = old
        self.assertNotEqual(first, str(adopter))
        self.assertTrue((Path(first) / "kernel").is_dir(), first)


class WhereTheLedgerIsHasOneAnswer(unittest.TestCase):
    """Four copies in `stop_gate`, a fifth in `write_block`.

    Eight lines of `git rev-parse --git-common-dir`, resolve, join `v4/ledger.db`
    -- so moving a ledger was a five-site edit whose missed site is a gate that
    quietly stops finding one.
    """

    def test_the_hooks_ask_the_module_that_owns_the_path(self):
        root = _git_repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        want = ledger.ledger_path(root)
        self.assertEqual(Path(_framework.db_path(root)), Path(want))
        self.assertEqual(Path(write_block._framework.db_path(root)), Path(want))

    def test_moving_the_ledger_moves_every_reader_at_once(self):
        """One patch to the owner, and all five call sites follow.

        The point of the repair: before it, `stop_gate` would still have been
        looking in four places of its own.
        """
        root = _git_repo(self)
        ledger.connect(root).close()
        real = ledger.ledger_path(root)
        moved = real.parent / "moved.db"
        shutil.copy(real, moved)
        saw = []

        def fake_path(repo_root):
            saw.append(str(repo_root))
            return moved

        original = ledger.ledger_path
        ledger.ledger_path = fake_path
        try:
            self.assertEqual(Path(_framework.db_path(root)), moved)
            self.assertEqual(Path(stop_gate._framework.db_path(root)), moved)
        finally:
            ledger.ledger_path = original
        self.assertTrue(saw)

    def test_a_directory_git_cannot_answer_for_is_none(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.assertIsNone(_framework.db_path(outside))


class AGitFailureIsNotACleanTree(unittest.TestCase):
    """`tree_state` and `worktree_digest` each held the same nested `git()`.

    Both turned any non-zero exit into `""`, and the second copy was dead --
    `worktree_digest` takes its state from `tree_state`. So a git that refused
    inside a real repository produced `{}`, which is what a directory git does
    not manage produces, in the module that computes the staleness digests.
    """

    def _repo_with_a_broken_index(self):
        root = _git_repo(self)
        (root / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        self.assertTrue(hashing.tree_state(root))
        (root / ".git" / "index").write_bytes(b"garbage-not-an-index")
        return root

    def test_a_refusal_inside_a_repository_is_raised(self):
        root = self._repo_with_a_broken_index()
        self.assertTrue(hashing._is_repo(root))
        with self.assertRaises(hashing.GitUnreadable):
            hashing.tree_state(root)

    def test_and_the_digest_does_not_answer_from_a_walk_instead(self):
        root = self._repo_with_a_broken_index()
        with self.assertRaises(hashing.GitUnreadable):
            hashing.worktree_digest(root)

    def test_a_directory_git_does_not_manage_still_answers(self):
        """The case the `{}` was for, kept: a fixture or a scratch dir."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertFalse(hashing._is_repo(tmp))
        self.assertEqual(hashing.tree_state(tmp), {})
        before = hashing.worktree_digest(tmp)
        (tmp / "x.py").write_text("x = 1\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_the_adapter_says_which_command_refused(self):
        root = self._repo_with_a_broken_index()
        with self.assertRaises(hashing.GitUnreadable) as caught:
            hashing._git(root, "ls-files", "-s")
        self.assertIn("ls-files", str(caught.exception))


class TheCheckerAndTheDetectorCountTheSameWay(unittest.TestCase):
    """`_count` was written out in both halves of the one pair whose two module
    docstrings are about them not answering by two programs.

    They still ask the question separately -- the checker re-asks it at answer
    time against the tree as it is -- and that is not the same as each holding
    its own dispatch over suffixes, which is what adding a language touches.
    """

    def test_the_dispatch_has_one_owner(self):
        self.assertFalse(hasattr(_load("detectors/test_weakened.py"), "_count"))
        self.assertFalse(hasattr(_load("checkers/test_weakened.py"), "_count"))

    def test_and_it_answers_for_every_language_the_pair_claims(self):
        cases = (
            ("tests/test_x.py", "def test_a():\n    assert 1\n", 1),
            ("src/x.test.ts", 'it("one", () => { expect(1).toBe(1) })\n', 1),
            ("src/lib.rs", "#[test]\nfn a() { assert!(true); }\n", 1),
        )
        for path, src, want in cases:
            with self.subTest(path=path):
                self.assertEqual(tw_analysis.count_for(path, src), want)

    def test_a_language_added_once_reaches_both_programs(self):
        """The drift this closes, exercised rather than argued.

        Teaching `count_for` a suffix moves the checker and the detector
        together, because there is only one of it.
        """
        src = "#[test]\nfn a() { assert!(true); }\n"
        self.assertEqual(tw_analysis.count_for("src/lib.rs", src),
                         tw_analysis.rs_count(src))
        self.assertEqual(tw_analysis.count_for("notes.txt", "def test_a():\n    assert 1\n"),
                         tw_analysis.count("def test_a():\n    assert 1\n"))


def _load(rel):
    import importlib.util
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(
        "loaded_" + path.stem + "_" + path.parent.name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WhatThisTaskSignedForIsOneQuery(unittest.TestCase):
    """The accepted-risk join was hand-written three times in `kernel/cli.py`.

    Twice in `status` (once for a person, once for `--json`) and once in `ship`
    -- about a table `kernel/risk.py` owns and writes.
    """

    def _conn(self):
        root = _v4_repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-27T00:00:00+00:00")
        for i, kind in enumerate(("unprovable", "unprovable", "no_checker")):
            cid = f"c{i}"
            ledger.insert(conn, "claim", id=cid, task_id="t1", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026-08-27T00:00:00+00:00")
            ledger.insert(conn, "accepted_risk", claim_id=cid, kind=kind,
                          who="t@t", why="w" * 60, was_tty=0, git_record="",
                          subject_digest="{}", scope="task",
                          created_at="2026-08-27T00:00:00+00:00")
        return conn

    def test_the_counts_come_from_the_module_that_owns_the_table(self):
        by_kind, _by_signer, _line = risk.signatures(self._conn(), "t1")
        self.assertEqual(by_kind, {"no_checker": 1, "unprovable": 2})

    def test_and_the_two_renderings_differ_only_in_the_empty_word(self):
        conn = self._conn()
        self.assertEqual(risk.signatures(conn, "t1")[2],
                         "no_checker=1, unprovable=2  |  by unrecorded=3")
        self.assertEqual(risk.signatures(conn, "no-such-task")[2], "none")
        self.assertEqual(
            risk.signatures(conn, "no-such-task", empty="nothing")[2], "nothing")

    def test_and_the_line_carries_the_route_a_second_call_would_have_dropped(self):
        """The half that decided which of the two implementations survives.

        Both existed after a merge -- one grouping by kind, one by kind and
        route -- and a resolver kept the wrong one, so `v4 ship` stopped saying
        whether a signature came from a monitor or from the worker waiving its
        own judge. One accessor cannot be picked over the other if there is
        only one.
        """
        conn = self._conn()
        ledger.insert(conn, "claim", id="c3", task_id="t1", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026-08-27T00:00:00+00:00")
        ledger.insert(conn, "accepted_risk", claim_id="c3", kind="unprovable",
                      who="m@m", why="w" * 60, was_tty=0, git_record="",
                      subject_digest="{}", scope="task", signed_by=risk.MONITOR,
                      created_at="2026-08-27T00:00:00+00:00")
        _by_kind, by_signer, line = risk.signatures(conn, "t1")
        self.assertEqual(by_signer, {risk.MONITOR: 1, risk.UNRECORDED: 3})
        self.assertIn(f"by {risk.MONITOR}=1, {risk.UNRECORDED}=3", line)


class SixWaysToBeStaleReachedOneWord(unittest.TestCase):
    """`STALE` told all six the same thing, and for one of them it was wrong.

    `risk.route` said "`v4 check` asks it again and costs a run of one checker".
    A detector edit is not cleared by re-running the checker: the claim row keeps
    the sha of the version that raised it until a re-derive appends
    `claim_reraised`, so the checker answers PASS and the state stays STALE.
    Measured on this repo's own ledger 2026-08-27: claim `c8f3f1d4` passed eight
    times between 12:34 and 17:31 and read STALE after every one.
    """

    def _repo(self):
        root = _v4_repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-27T00:00:00+00:00")
        (root / "detectors").mkdir(exist_ok=True)
        (root / "detectors" / "d.py").write_text("# raised it\n")
        subject = root / "s.py"
        subject.write_text("x = 1\n")
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="lint",
                      question="q", subject_refs=json.dumps(
                          [{"kind": "file", "path": "s.py"}]),
                      checker="lint", origin="derive", detector="d.py",
                      detector_sha=hashing.file_sha(root / "detectors" / "d.py"),
                      created_at="2026-08-27T00:00:00+00:00")
        return root, conn

    def _cfg(self, root):
        """The real config, so the answer is recorded against the real shas.

        A fixture that records `config_sha="fs"` makes every claim stale on the
        config comparison, which is two lines above the one under test -- the
        test would pass on a reason it was not asking about.
        """
        cfg = config_mod.RepoConfig(root)
        cfg.kinds = {"lint": {"staleness": "subject"}}
        return cfg

    def _answer(self, conn, root):
        cfg = self._cfg(root)
        row = conn.execute("SELECT * FROM claim WHERE id='c1'").fetchone()
        ledger.append_attempt(
            conn, claim_id="c1", exit_code=0, duration_ms=1,
            subject_digest=json.dumps(state._digest_now(conn, root, row)),
            checker_sha=cfg.checker_sha_on_disk("lint"), config_sha=cfg.sha,
            facts_sha=cfg.facts_sha_for("lint"), head_commit="",
            worktree=str(root), argv="[]", stdout="", stderr="",
            started_at="2026-08-27T01:00:00+00:00",
            ended_at="2026-08-27T01:00:01+00:00")
        return row

    def _reason(self, conn, root, row):
        cfg = self._cfg(root)
        att = state.latest_attempt(conn, "c1")
        return state.stale_reason(
            conn, root, row, att, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=lambda cid: cfg.checker_sha_on_disk(cid),
            facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for)

    def test_an_untouched_claim_names_nothing(self):
        root, conn = self._repo()
        row = self._answer(conn, root)
        self.assertEqual(self._reason(conn, root, row), "")

    def test_a_moved_subject_names_the_file(self):
        root, conn = self._repo()
        row = self._answer(conn, root)
        (root / "s.py").write_text("x = 2\n")
        self.assertIn("s.py", self._reason(conn, root, row))

    def test_a_changed_detector_says_check_cannot_clear_it(self):
        """The half that cost eight runs. A re-run is not the way out here."""
        root, conn = self._repo()
        row = self._answer(conn, root)
        (root / "detectors" / "d.py").write_text("# narrowed\n")
        why = self._reason(conn, root, row)
        self.assertIn("detectors/d.py", why)
        self.assertIn(state.NEEDS_DERIVE, why)

    def test_and_the_router_sends_it_somewhere_else(self):
        """`route` is where the wrong advice was printed, so it is pinned here."""
        root, conn = self._repo()
        self._answer(conn, root)
        (root / "detectors" / "d.py").write_text("# narrowed\n")
        cfg = self._cfg(root)
        row = conn.execute("SELECT * FROM claim WHERE id='c1'").fetchone()
        what, why = risk.route(conn, cfg, row, state.STALE)
        self.assertEqual(what, risk.REDERIVE)
        self.assertIn("v4 derive", why)
        self.assertNotIn("`v4 check` asks it again", why)


class OneShapeCrossesTheProcessBoundary(unittest.TestCase):
    """The subject payload was an untyped dict literal built by hand in three
    places, and their `params` had already diverged.

    `register`'s probe omitted `derive_exclude` that `lifecycle` sends -- the
    probe runs right after `v4 install` writes every checker's bypass fixtures
    into the repo, so without it the probe asks repo-scoped checkers about
    deliberately broken code.
    """

    def test_the_probe_carries_the_repos_exclusions(self):
        root = _v4_repo(self, derive_exclude=["tests/fixtures/**"])
        (root / "checkers").mkdir()
        # One short line: `probe_repo_subject` hands back the first line of
        # output and nothing else.
        (root / "checkers" / "echo.py").write_text(
            "import argparse, json, sys, pathlib\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out')\n"
            "a = p.parse_args()\n"
            "s = json.loads(pathlib.Path(a.subject).read_text())\n"
            "print('|'.join(s['params'].get('derive_exclude') or ['NONE']))\n"
            "sys.exit(0)\n")
        code, line = register.probe_repo_subject(
            repo_root=root, checker_path=root / "checkers" / "echo.py",
            kind="lint")
        self.assertEqual(code, 0, line)
        self.assertEqual(line.strip(), "tests/fixtures/**")

    def test_a_claim_check_hands_over_the_same_shape(self):
        """The third site, `lifecycle`, run rather than read.

        An echo checker prints the subject it was given, so this is the payload
        that actually crossed the boundary and not a reconstruction of it.
        """
        from kernel import config as config_mod, lifecycle
        root = _v4_repo(self, derive_exclude=["tests/fixtures/**"])
        (root / "checkers").mkdir()
        checker = root / "checkers" / "echo.py"
        checker.write_text(_ECHO_CHECKER)
        (root / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "in"], cwd=root, capture_output=True)
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"lint": {"checker": "echo", "question_template": "q {file}",
                      "applies_to": "always", "staleness": "subject"}}))
        (root / ".v4" / "checkers.json").write_text(json.dumps(
            {"echo": {"path": "checkers/echo.py", "kinds": ["lint"],
                      "sha256": hashing.file_sha(checker), "reads": ["**"]}}))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="HEAD",
                      created_at="2026-08-27T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="lint",
                      question="q", checker="echo", origin="derive",
                      file="app.py", symbol="f", variant="",
                      subject_refs=json.dumps([{"kind": "file",
                                                "path": "app.py"}]),
                      created_at="2026-08-27T00:00:00+00:00")
        results = lifecycle.check(conn, config_mod.RepoConfig(root), "t1")
        said = [r[2].stdout for r in results if r[2] is not None]
        self.assertTrue(said, results)
        payload = json.loads([l for l in said[0].splitlines()
                              if l.startswith("{")][0])
        self.assertEqual(sorted(payload),
                         sorted(runner.Subject(repo_root="/x").as_dict()))
        self.assertEqual(payload["params"]["derive_exclude"],
                         ["tests/fixtures/**"])

    def test_derive_exclude_has_to_be_said_out_loud(self):
        """No default: a caller that forgot it looked like one that decided.

        `subject_files.exclusions` records why a fixture run passes nothing --
        a red fixture *is* the broken code -- and that is a different sentence
        from an omission.
        """
        with self.assertRaises(TypeError):
            runner.subject_params()
        self.assertEqual(runner.subject_params(derive_exclude=())["derive_exclude"],
                         [])

    def test_a_detector_subject_has_the_shape_a_checker_subject_has(self):
        root = _git_repo(self)
        (root / "d.py").write_text(
            "import argparse, json, pathlib\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out')\n"
            "a = p.parse_args()\n"
            "s = json.loads(pathlib.Path(a.subject).read_text())\n"
            "print('KEYS ' + ','.join(sorted(s)))\n")
        rc, out, err, _payload, _ms = detector_protocol.run_detector(
            root, root / "d.py", ["a.py"], diff_base="HEAD",
            params={"derive_exclude": ["x/**"]})
        self.assertEqual(rc, 0, err)
        keys = [l[5:] for l in out.splitlines() if l.startswith("KEYS ")][0]
        self.assertEqual(sorted(keys.split(",")),
                         sorted(runner.Subject(repo_root="/x").as_dict()))


class ATestIsNeverAConfigFileWhateverItIsCalled(unittest.TestCase):
    """`_is_config`'s docstring said that and the guard did not hold it.

    `CONFIG_PATH` matches the substring `conf`, and `conftest.py` outside a
    `tests/` directory -- pytest's own standard position for one -- was not a
    test to `subject_files.is_test`. So a fixture stating a threshold became
    the binding every module in the repo was judged as restating.
    """

    def test_a_repo_root_conftest_is_not_a_source_of_config_bindings(self):
        self.assertFalse(structural_lint._is_config("conftest.py"))
        self.assertFalse(structural_lint._is_config("app/conftest.py"))

    def test_because_the_owner_calls_it_a_test(self):
        self.assertTrue(subject_files.is_test("conftest.py"))
        self.assertTrue(subject_files.is_test("app/pkg/conftest.py"))

    def test_and_a_real_config_module_still_is_one(self):
        self.assertTrue(structural_lint._is_config("kernel/config.py"))
        self.assertTrue(structural_lint._is_config("app/settings.py"))
        self.assertFalse(subject_files.is_test("kernel/config.py"))


if __name__ == "__main__":
    unittest.main()
