"""Repairs in `kernel/cli.py`, `kernel/derive.py` and
`kernel/analysis/external_write.py`.

    python3 -m unittest tests.test_what_the_command_line_hands_back -v

Four of these are about a report that stated one number for a group, printed a
raw dict at a person, named a directory the repo does not have, or gave an exit
code no command could name.  Two are about a scan that stopped looking, and one
is about the same list living in more places than agree.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, derive, ledger, lifecycle, runner  # noqa: E402
from kernel.analysis import external_write, subject_files  # noqa: E402


def _repo(case, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / ".v4" / "checkers.json").write_text("{}")
    return tmp


def _args(root, **kw):
    a = types.SimpleNamespace(repo=str(root), acceptance=".v4/acceptance.json")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _say(fn, *a, **kw):
    """Run a command and give back (exit code, everything it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fn(*a, **kw)
    return code, out.getvalue()


class AnExitCodeTheReaderCanName(unittest.TestCase):
    """`exit 7` was the whole message about a subject that moved.

    The kernel diagnoses 6, 7 and 8 itself and appends its own sentence to
    stderr -- "the working tree changed while the checker ran; this answer
    describes neither state" -- while `cmd_check` printed
    `res.stdout or res.stderr`, so the checker's partial output won and the
    kernel's explanation was dropped. No command anywhere mapped the digits to
    the names `runner` gives them.
    """

    def _fake_check(self, exit_code, stdout, stderr):
        row = {"id": "c1", "kind": "scope", "file": None, "symbol": None}
        res = types.SimpleNamespace(exit_code=exit_code, stdout=stdout,
                                    stderr=stderr, duration_ms=12)

        def check(conn, cfg, task, only=None, run_expensive=False):
            yield row, None, res
        return check

    def test_the_kernels_sentence_survives_the_checkers_output(self):
        root = _repo(self)
        real = lifecycle.check
        lifecycle.check = self._fake_check(
            runner.SUBJECT_MOVED, "checker got this far",
            "the working tree changed while the checker ran")
        try:
            code, said = _say(cli.cmd_check, _args(root, task="t", claim=None,
                                                   all=False))
        finally:
            lifecycle.check = real
        self.assertEqual(code, 0)
        self.assertIn("checker got this far", said)
        self.assertIn("the working tree changed while the checker ran", said)

    def test_and_the_number_arrives_with_its_name(self):
        root = _repo(self)
        real = lifecycle.check
        lifecycle.check = self._fake_check(runner.TIMEOUT, "", "over time")
        try:
            _, said = _say(cli.cmd_check, _args(root, task="t", claim=None,
                                                all=False))
        finally:
            lifecycle.check = real
        self.assertIn("TIMEOUT", said)

    def test_every_code_the_kernel_can_return_has_one(self):
        for code in (runner.PASS, runner.FAIL, runner.UNSUPPORTED, runner.ERROR,
                     runner.CHECKER_TAMPERED, runner.SUBJECT_MOVED,
                     runner.TIMEOUT):
            self.assertIn(str(code), runner.exit_name(code))
            self.assertTrue(runner.exit_name(code).split()[1:], code)
        self.assertEqual(runner.exit_name(99), "99")


class TheReportAPersonReadsBeforeShipping(unittest.TestCase):
    """`detectors: {...}` printed a dict repr of ~28 name-to-bool pairs.

    And a detector that raised claims and had every one of them dropped for
    scope recorded `claims: 0` -- the same row as one that scanned and found
    nothing -- while the in-memory `refused` list that knew better was
    discarded by `ship`.
    """

    def _report(self, **over):
        rep = {"converged": True, "rounds": [1], "claims": [], "blocked": [],
               "chain_ok": True, "chain_problems": [], "facts_unconfirmed": [],
               "deferred": [], "detectors": {f"d{i}.py": True for i in range(28)},
               "detectors_not_run": [], "nothing_seen": [], "lenses_run": [],
               "hook_seen": 1, "claim_origins": {}}
        rep.update(over)
        return rep

    def _ship(self, root, rep):
        real = lifecycle.ship
        lifecycle.ship = lambda conn, cfg, task: (True, rep)
        try:
            return _say(cli.cmd_ship, _args(root, task="t", json=False))
        finally:
            lifecycle.ship = real

    def test_the_line_counts_instead_of_pasting_a_dict(self):
        root = _repo(self)
        _, said = self._ship(root, self._report())
        self.assertIn("28/28 ran", said)
        self.assertNotIn("'d1.py': True", said)

    def test_a_detector_whose_every_claim_was_dropped_is_named(self):
        root = _repo(self)
        _, said = self._ship(root, self._report(
            detectors_all_dropped=["route_auth.py"]))
        self.assertIn("route_auth.py", said)
        self.assertIn("ALL DROPPED", said)



class PrintingABriefIsNotReviewing(unittest.TestCase):
    """`reviewed by:` read `lenses_run`, which `v4 review lens` writes.

    Printing a brief is free. So the line said `reviewed by: near-miss` on the
    two ships that built that lens, when no reviewer had read a diff with it
    and it had raised nothing -- the state its own comment said it existed to
    separate.

    Three states now, and the middle one had no words at all: somebody took the
    brief and never came back.
    """

    def _report(self, **over):
        rep = {"converged": True, "rounds": [1], "claims": [], "blocked": [],
               "chain_ok": True, "chain_problems": [], "facts_unconfirmed": [],
               "deferred": [], "detectors": {}, "detectors_not_run": [],
               "nothing_seen": [], "lenses_run": [], "lenses_reviewed": {},
               "hook_seen": 1, "claim_origins": {}}
        rep.update(over)
        return rep

    def _ship(self, root, rep):
        real = lifecycle.ship
        lifecycle.ship = lambda conn, cfg, task: (True, rep)
        try:
            return _say(cli.cmd_ship, _args(root, task="t", json=False))
        finally:
            lifecycle.ship = real

    def test_a_brief_that_printed_is_not_reported_as_a_review(self):
        root = _repo(self)
        _, said = self._ship(root, self._report(lenses_run=["near-miss"]))
        self.assertIn("briefed and never reported back", said)
        self.assertIn("near-miss", said)
        self.assertNotIn("reviewed by: near-miss", said)

    def test_a_review_that_found_nothing_is_not_silence(self):
        """The reason `--findings 0` is required: this state has to exist."""
        root = _repo(self)
        _, said = self._ship(root, self._report(
            lenses_run=["near-miss"], lenses_reviewed={"near-miss": 0}))
        self.assertIn("reviewed by: near-miss (0 finding(s))", said)
        self.assertNotIn("never reported back", said)

    def test_nothing_at_all_still_says_so(self):
        root = _repo(self)
        _, said = self._ship(root, self._report())
        self.assertIn("no lens has reported on this task", said)
        self.assertNotIn("never reported back", said)


class TheDirectoriesThisRepoActuallyHas(unittest.TestCase):
    """The sweep told its reviewers to read `app/ and tests/`, hardcoded.

    This repo has no `app/`, so a reviewer obeying the command that started it
    read a directory that does not exist and never reached the kernel.
    """

    def test_the_instruction_names_what_is_there(self):
        root = _repo(self, lens_sweep={"every_days": 0})
        for d in ("kernel", "checkers"):
            (root / d).mkdir()
            (root / d / "x.py").write_text("x = 1\n")
        (root / ".v4" / "lenses").mkdir()
        (root / ".v4" / "lenses" / "one.json").write_text(json.dumps(
            {"name": "one", "source": "fixture", "anti_patterns": ["a shape"],
             "checks": [{"id": "a", "ask": "does it?"}]}))
        ledger.connect(root).close()
        code, said = _say(cli.cmd_sweep, _args(
            root, history=False, done=False, if_due=False, findings=None,
            note=None))
        self.assertEqual(code, 0, said)
        self.assertIn("Review checkers/, kernel/", said)
        self.assertNotIn("app/", said)


class WhatDecidesThisClaimsVerdict(unittest.TestCase):
    """Four hops, two of them into JSON files no command printed.

    `v4 status --json` for the checker id, `.v4/claim_kinds.json` for the
    detector, `.v4/checkers.json` for the program, and that program's imports
    for the module holding the rule. 28 subcommands and none of them answered
    it, while `config.checker_for` and `doctor` already walk the chain.
    """

    def test_one_command_walks_the_chain(self):
        code, said = _say(cli.main, ["--repo", str(ROOT), "explain",
                                     "--kind", "fail-closed"])
        self.assertEqual(code, 0, said)
        self.assertIn("checkers/fail_closed.py", said)
        self.assertIn("kernel/analysis/fail_closed.py", said)
        self.assertIn("raised by", said)

    def test_and_says_so_when_the_kind_is_not_one(self):
        code, said = _say(cli.main, ["--repo", str(ROOT), "explain",
                                     "--kind", "no-such-kind"])
        self.assertEqual(code, 1)
        self.assertTrue(said.strip())


class WhatADetectorRaisedAndWhatWasThrownAway(unittest.TestCase):
    """`claims: 0` meant two different things.

    A detector that scanned and found nothing, and one whose every claim was
    dropped for scope, wrote the same `detector_run` row -- and `ship` reads
    that table, not the `refused` list `derive` returns to its caller.
    """

    def _repo_with_detector(self, body):
        root = _repo(self, derive_exclude=[])
        (root / "detectors").mkdir()
        (root / "detectors" / "always_probe.py").write_text(body)
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"scope": {"checker": "scope", "question_template": "is {file} in scope?",
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
        cfg = config_mod.RepoConfig(root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=scope_globs,
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        res = derive.derive(conn, cfg, task_id="t1", scope_globs=scope_globs,
                            subject_files=["app.py"], phase="open",
                            detectors_dir=root / "detectors")
        rows = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'detector_run' ORDER BY id")]
        return res, {r["detector"]: r for r in rows}

    def test_a_dropped_claim_is_counted_where_ship_can_see_it(self):
        root = self._repo_with_detector(
            'print("V4-CLAIM: kind=scope file=elsewhere/x.py symbol=f")\n')
        res, rows = self._derive(root, ["app.py"])
        self.assertTrue(res["refused"], res)
        row = rows["always_probe.py"]
        self.assertEqual(row["claims"], 0)
        self.assertEqual(row["dropped"], 1,
                         "found-nothing and everything-was-dropped wrote the "
                         "same row")

    def test_and_a_detector_that_found_nothing_says_zero(self):
        root = self._repo_with_detector('pass\n')
        _res, rows = self._derive(root, ["app.py"])
        row = rows["always_probe.py"]
        self.assertEqual((row["claims"], row["dropped"]), (0, 0))

    def test_a_detectors_stderr_goes_through_the_same_redaction_as_a_checkers(self):
        """It was written into the event payload as it came, and those events
        are exported into `.v4/ledger_export.jsonl`, which is committed and
        scanned."""
        root = self._repo_with_detector(
            'import sys\n'
            # The password half has to look live: each assertion below is that redaction
            # blanks it, or that the engagement gate refuses a sentence carrying it.
            # pragma: allow-secret
            'print("postgres://u:hunter2isnotreal@db.prod/app", file=sys.stderr)\n'
            'sys.exit(3)\n')
        _res, rows = self._derive(root, ["app.py"])
        said = rows["always_probe.py"]["stderr"]
        self.assertNotIn("hunter2isnotreal", said)
        self.assertTrue(said.strip(), "redacted is not the same as dropped")


class AnObjectsOwnFieldIsNotTheParsedTable(unittest.TestCase):
    """`self.facts` made `kernel/config.py` count as reading the table.

    The attribute this rule is about is the one argparse put on a namespace;
    a method reading its own field is a different sentence with the same
    spelling. The verdict decides whether a broken facts table can quietly
    switch a program off, so a false yes is a guard watching the wrong thing.
    """

    def test_a_method_reading_its_own_field_does_not_count(self):
        self.assertFalse(derive._touches_facts(
            "class C:\n"
            "    def __init__(self):\n"
            "        self.facts = 1\n"
            "    def go(self):\n"
            "        return self.facts.get('x')\n"))

    def test_and_the_parsed_namespace_still_does(self):
        self.assertTrue(derive._touches_facts(
            "def main(a):\n"
            "    return a.facts.get('ui_globs')\n"))

    def test_and_a_table_arriving_as_a_parameter_still_does(self):
        self.assertTrue(derive._touches_facts(
            "def scan(root, facts):\n"
            "    return facts['ui_globs']\n"))


class OneLocalReadDoesNotAnswerForANetworkWrite(unittest.TestCase):
    """The write side filtered by `LOCAL_KINDS` and the read side did not.

    So one `Path(p).read_text()` -- kind `fs` -- or one `SELECT` anywhere in a
    function silenced every unconfirmed publish, send and upload in it, while
    `_blind_readbacks` twenty lines down filtered its reads exactly this way.
    """

    def _findings(self, src):
        return external_write.analyse_source(
            src, path="x.py", table=external_write.default_table())

    def test_an_unconfirmed_publish_is_reported(self):
        got = self._findings("def publish_it(client, body, p):\n"
                             "    client.publish(body)\n")
        self.assertTrue([f for f in got if f.shape == external_write.SHAPE_UNOBSERVED])

    def test_and_a_local_read_beside_it_does_not_silence_it(self):
        got = self._findings("def publish_it(client, body, p):\n"
                             "    Path(p).read_text()\n"
                             "    client.publish(body)\n")
        self.assertTrue([f for f in got if f.shape == external_write.SHAPE_UNOBSERVED],
                        "a local read cannot answer 'did it land' across a "
                        "network boundary -- which is the write side's own "
                        "stated reason for its filter")


class OneOwnerForTheProtectedSet(unittest.TestCase):
    """The list had four homes and the fallback had already drifted.

    `_DEFAULT_TABLE["protected_paths"]` held three globs and omitted
    `.github/**` -- the directory holding `.github/monitor/PROMPT.md` and
    `.github/workflows/v4.yml`, which is why it is protected at all -- and
    `default_table()` is what a repo with no facts file is judged by, which is
    the path `register` takes when it runs a checker's fixtures.
    """

    def test_the_built_in_table_carries_the_whole_set(self):
        table = external_write.default_table()
        self.assertEqual(set(table.protected_paths),
                         set(subject_files.PROTECTED_DEFAULT))

    def test_a_repo_with_no_facts_file_is_judged_by_that_same_set(self):
        got = external_write.table_from(None)
        self.assertEqual(set(got.protected_paths),
                         set(subject_files.PROTECTED_DEFAULT))

    def test_and_the_drafted_table_an_adopter_gets_does_too(self):
        from kernel import facts as facts_mod
        root = _repo(self)
        (root / "app.py").write_text("x = 1\n")
        drafted = facts_mod.propose(root)
        self.assertEqual(set(drafted["protected_paths"]),
                         set(subject_files.PROTECTED_DEFAULT))



class OneStrandedClaimDoesNotTakeTheTaskDown(unittest.TestCase):
    """`v4` can remove a claim kind, and the rows it already raised stay.

    `cfg.checker_for` refuses an unregistered kind, correctly, because a
    detector emitting one must not be guessed at. `check` calls the same
    function on rows read out of the ledger, where the kind can have been
    removed since -- and the `ConfigError` left the per-claim loop, passed
    `cmd_check`, and reached the top-level handler. Measured on this repo: four
    such rows on `repo-review` made all 257 of its review findings
    unverifiable, and answering any one of them meant `--claim` by hand.

    Retracting is not the alternative. `_retract_orphans` refuses to retract a
    claim that failed, so that deleting a rule cannot delete a finding; passing
    it here would be that move through another door. Exit 4 already means "no
    one can verify this", and is deliberately not terminal.
    """

    def _repo_with_two_claims(self):
        root = _repo(self)
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "review-finding": {"checker": "review-finding",
                               "question_template": "q",
                               "staleness": "subject"}}))
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for cid, kind in (("gone1", "a-kind-nobody-registers"),
                          ("live1", "review-finding")):
            ledger.insert(conn, "claim", id=cid, task_id="t", kind=kind,
                          question="q", subject_refs=json.dumps(
                              [{"kind": "file", "path": "x.py"}]),
                          checker=kind, origin="derive", created_at="2026",
                          file="x.py", symbol="f", detector="d.py")
        conn.close()
        return root

    def test_the_other_claims_are_still_reached(self):
        root = self._repo_with_two_claims()
        _code, said = _say(cli.cmd_check, _args(root, task="t",
                                           claim=None, all=True))
        self.assertIn("live1", said,
                      "one stranded row took every other claim on the task "
                      "down with it")
        self.assertIn("gone1", said, "and the stranded one is not silent")

    def test_it_is_reported_as_unverifiable_and_not_as_answered(self):
        """The whole point of not retracting it: it keeps holding."""
        root = self._repo_with_two_claims()
        _say(cli.cmd_check, _args(root, task="t", claim=None, all=True))
        conn = ledger.connect(root)
        try:
            row = conn.execute(
                "SELECT exit_code, stderr FROM attempt WHERE claim_id = 'gone1' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "no attempt was recorded at all")
        self.assertEqual(row["exit_code"], runner.UNSUPPORTED)

    def test_the_reason_is_about_this_call_site(self):
        """`cfg.kind` says "a detector that emits an unregistered kind" -- true
        where it is raised, false where it is caught. Nothing is emitting; a
        row weeks old is being read."""
        root = self._repo_with_two_claims()
        _say(cli.cmd_check, _args(root, task="t", claim=None, all=True))
        conn = ledger.connect(root)
        try:
            row = conn.execute(
                "SELECT stderr FROM attempt WHERE claim_id = 'gone1' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertNotIn("A detector that emits", row["stderr"] or "")
        self.assertIn("was registered when this claim was raised",
                      row["stderr"] or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
