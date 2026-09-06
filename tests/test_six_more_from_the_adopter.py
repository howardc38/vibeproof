"""Six findings an adopter's A/B report left, each measured in their repo first.

    python3 -m unittest tests.test_six_more_from_the_adopter -v

The six share nothing but where they were found. What they do share is a shape:
each is a place where two halves of this framework were asked the same question
and gave different answers, and nothing was reading both.

  A4  the `design-pins` detector and its checker disagreed about which files
      this repo has opted out of, and the detector is the half that raises
  A9  the CI check read a workflow file as text, so a comment explaining why
      there was no chain job counted as the chain job
  A6  a signature settled a claim on the way out of `v4 ship` and was invisible
      to the `doctor` row that told the reader to write one
  A7  `raise_finding` refused a file that is not in the repo and accepted no
      file at all; `review add` took `--withdraw` and dropped it
  A8  `withdraw_deferral`'s docstring said a finding is un-deferred by closing
      it, and no code said so
  A5  the one line in a generated CLAUDE.md that says where to go next named
      two paths that exist only in the framework repo

Every test here runs the function. Nothing asserts on source text.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, config, doctor, doctrine, ledger, review  # noqa: E402


def _load(path: Path, name: str):
    """A detector is a script, not a package module."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_repo(case) -> Path:
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


def _adoptable_repo(case) -> Path:
    """A git repo with the least `.v4/` a `RepoConfig` will load."""
    tmp = _git_repo(case)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / ".v4" / "checkers.json").write_text("{}")
    return tmp


class DetectorReadsTheSameExclusionsAsItsChecker(unittest.TestCase):
    """A4 -- `derive_exclude` is the repo's declaration, and both halves read it.

    Measured on the adopter: the detector saw 20 pinned documents, 19 of them
    under a path their `derive_exclude` names, and the checker would look at
    one. The 19 are deliberately-broken fixtures, so every claim raised about
    them was a claim somebody had to judge by hand and dismiss.
    """

    def setUp(self):
        self.detector = _load(ROOT / "detectors/design_pins.py", "dp_under_test")

    def _repo(self, exclude):
        tmp = _git_repo(self)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(
            json.dumps({"derive_exclude": exclude}))
        (tmp / "fixtures").mkdir()
        (tmp / "fixtures" / "broken.md").write_text(
            "<!-- pinned: nothing/at/all.py::gone -->\n")
        (tmp / "real.md").write_text(
            "<!-- pinned: nothing/at/all.py::gone -->\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp,
                       capture_output=True)
        return tmp

    def test_an_excluded_document_is_not_offered_for_a_claim(self):
        seen = self.detector.pinned_docs(self._repo(["fixtures/**"]))
        self.assertIn("real.md", seen)
        self.assertNotIn("fixtures/broken.md", seen,
                         "the checker will not read this file, so a claim "
                         "raised about it can only ever be judged by hand")

    def test_without_the_exclusion_the_same_document_is_offered(self):
        """The control: it is the declaration doing this, not the path."""
        seen = self.detector.pinned_docs(self._repo([]))
        self.assertIn("fixtures/broken.md", seen)
        self.assertIn("real.md", seen)


class TheCiCheckReadsWhatWouldRun(unittest.TestCase):
    """A9 -- a paragraph about a command is not the command.

    The adopter wrote a comment in `.github/workflows/` explaining why they had
    no chain job. The comment quotes the command, as such a comment must, and
    `doctor` moved from `no job walks the chain` to `walks the exported chain`
    on the strength of it. Nothing in the repo had changed.
    """

    def test_a_comment_naming_the_command_is_not_a_job(self):
        text = ("jobs:\n  build:\n    steps:\n"
                "      # no `v4 audit --events` here: the ledger lives in .git/\n"
                "      - run: echo hello\n")
        self.assertNotIn("audit --events", doctor._run_lines(text))

    def test_a_step_that_runs_it_still_counts(self):
        """The control. Stripping too much would be the same defect mirrored."""
        text = ("jobs:\n  build:\n    steps:\n"
                "      - run: ./bin/v4 audit --events\n")
        self.assertIn("audit --events", doctor._run_lines(text))

    def test_a_hash_inside_a_quoted_string_is_not_a_comment(self):
        """Shell and YAML both quote, and a `#` inside quotes is data."""
        kept = doctor._run_lines('      - run: echo "issue #12 is fixed"')
        self.assertIn("#12", kept)

    def test_the_check_itself_moves_with_it(self):
        """Through `doctor`, not through `_run_lines` alone."""
        tmp = _git_repo(self)
        wf = tmp / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(
            "jobs:\n  b:\n    steps:\n"
            "      # we deliberately do not run v4 audit --events\n"
            "      - run: echo hi\n")
        out = []
        doctor._check_ci_can_actually_walk_the_chain(tmp, out)
        rows = [c for c in out if c["what"] == "CI"]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("no job walks the chain", rows[0]["detail"])


class OneYardstickForASignature(unittest.TestCase):
    """A6 -- a signed claim is settled in both reports or in neither.

    `unsettled_fails`, which decides whether `v4 ship` complains, asks only
    whether an `accepted_risk` row names the claim. The `unanswerable kinds`
    row asked whether a *repo-scoped* signature named the kind. So a
    claim-scoped signature settled the claim on the way out and left the report
    telling its reader to go and sign the thing they had signed.
    """

    def _ledger(self, *, scope):
        """One ledger, one kind, and one claim for each reader to look at.

        `unsettled_fails` reads a claim whose last attempt was exit 1; the
        `unanswerable kinds` row reads one that was exit 4. Both are here so
        that "the two agree" is measured on both, rather than on a query that
        would return nothing whatever the signature said.
        """
        tmp = _git_repo(self)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r" * 80,
                      scope_globs=["**"], base_commit="x", created_at="2026")
        for cid, code in (("c-unsupported", 4), ("c-failed", 1)):
            ledger.insert(conn, "claim", id=cid, task_id="t",
                          kind="spec-coverage", file="a.py", symbol="f",
                          variant="", question="q", subject_refs="[]",
                          checker="spec-coverage", detector="d",
                          origin="derive", created_at="2026")
            ledger.append_attempt(
                conn, claim_id=cid, subject_digest="{}", checker_sha="s",
                config_sha="cf", head_commit="hc", worktree="w", argv="[]",
                exit_code=code, stdout="", stderr="", started_at="2026",
                ended_at="2026", duration_ms=1,
                claim_digest=ledger.claim_digest(conn, cid), facts_sha="")
            if scope:
                ledger.insert(conn, "accepted_risk", claim_id=cid,
                              kind="unprovable", who="a@x", why="w" * 80,
                              was_tty=0, git_record=f".v4/risks/{cid}.json",
                              subject_digest="{}", created_at="2026",
                              scope=scope,
                              cover_key=json.dumps({"kind": "spec-coverage"})
                              if scope == "repo" else "")
        conn.commit()
        (tmp / ".v4").mkdir(exist_ok=True)
        (tmp / ".v4" / "claim_kinds.json").write_text(
            json.dumps({"spec-coverage": {"checker": "spec-coverage"}}))
        return tmp, conn

    def _said(self, root) -> str:
        """Everything the `unanswerable kinds` row says, or "" for no row.

        No row is one of the outcomes: with nothing stuck and nothing signed
        for the repo, this check appends nothing at all, which is the shape it
        has always had.
        """
        out = []
        doctor._check_kinds_this_repo_keeps_failing_to_answer(root, out)
        return " ".join(c["detail"] for c in out
                        if c["what"] == "unanswerable kinds")

    def test_a_claim_scoped_signature_settles_the_row_it_settles_at_ship(self):
        root, _conn = self._ledger(scope="claim")
        self.assertNotIn("spec-coverage blocked", self._said(root),
                         "`v4 ship` treats this claim as settled; this row "
                         "must not send the reader to sign it again")

    def test_an_unsigned_claim_is_still_reported(self):
        """The control: the row still does its job."""
        root, _conn = self._ledger(scope=None)
        self.assertIn("spec-coverage blocked", self._said(root))

    def test_the_two_readers_agree_when_it_is_signed(self):
        """Both readers, one ledger, both claims -- and both must say settled."""
        for scope in ("claim", "repo"):
            with self.subTest(scope=scope):
                root, conn = self._ledger(scope=scope)
                self.assertEqual(ledger.unsettled_fails(conn, "t"), [],
                                 "ship considers the failing claim settled")
                self.assertNotIn("spec-coverage blocked", self._said(root))

    def test_the_two_readers_agree_when_it_is_not(self):
        """The other direction, on the same fixture: neither may say settled."""
        root, conn = self._ledger(scope=None)
        self.assertEqual([r[0] for r in ledger.unsettled_fails(conn, "t")],
                         ["c-failed"])
        self.assertIn("spec-coverage blocked", self._said(root))


def _repo_that_takes_findings(case) -> Path:
    """A repo where `review add` would actually succeed.

    The flag test needs this as much as the filing tests do: under the revert
    it has to get all the way to a filed finding, because "the command wrote a
    claim for somebody who asked to cancel a deferral" is the failure, and a
    `ConfigError` on the way there would prove only that the fixture was thin.
    """
    root = _git_repo(case)
    (root / ".v4").mkdir()
    (root / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "thresholds": {"min_chars": 40}}))
    (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"review-finding": {"checker": "review-finding", "detector": None,
                            "staleness": "subject", "engagement": True,
                            "question_template": "is {file} closed"}}))
    (root / ".v4" / "checkers.json").write_text("{}")
    (root / "mod.py").write_text("def helper():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=root, capture_output=True)
    return root


class _Filing(unittest.TestCase):
    """A repo a finding can be filed into."""

    def setUp(self):
        self.root = _repo_that_takes_findings(self)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)


class AFindingNeedsCoordinates(_Filing):
    """A7 -- the write is where a coordinate rule belongs.

    `resolve_symbol` refuses a file that is not in the repo, and returns before
    looking at anything when the symbol is empty. So `--file` absent and
    `--symbol` absent passed every check. The adopter has one of these: a
    `review-finding` with no file, in an append-only table, closable by no test
    (nothing to run against) and by no `--gone` (no file to look in).
    """

    def test_no_file_is_refused(self):
        with self.assertRaises(review.BadCoordinates) as caught:
            review.raise_finding(self.conn, self.cfg, task_id=None, file="",
                                 symbol="", note="something is wrong here")
        self.assertIn("--file", str(caught.exception))

    def test_none_is_refused_too(self):
        """`review add` with no `--file` at all hands this `None`."""
        with self.assertRaises(review.BadCoordinates):
            review.raise_finding(self.conn, self.cfg, task_id=None, file=None,
                                 symbol=None, note="something is wrong here")

    def test_a_finding_with_a_file_is_still_filed(self):
        """The control."""
        cid, made, _sib = review.raise_finding(
            self.conn, self.cfg, task_id=None, file="mod.py", symbol="helper",
            note="this one has somewhere to be")
        self.assertTrue(made)
        self.assertTrue(cid)

    def test_a_document_finding_needs_no_symbol(self):
        """Refusing an empty symbol would break the closure route it has."""
        (self.root / "doc.md").write_text("a document\n")
        cid, made, _sib = review.raise_finding(
            self.conn, self.cfg, task_id=None, file="doc.md", symbol="",
            note="a document finding closes with --gone/--now")
        self.assertTrue(made and cid)


class AFlagThisActionDoesNotRead(unittest.TestCase):
    """A7b -- `review add --withdraw` filed a finding and dropped the flag.

    One parser serves seven actions, so argparse accepts every flag for every
    one of them. `--withdraw` is honoured in `defer` alone; the adopter typed it
    on `add`, got `raised <id>`, and read that as the cancellation they asked
    for. `--why`, `--gone`, `--findings` and the rest had the same hole.
    """

    def _args(self, action, **kw):
        ns = argparse.Namespace(
            action=action, task=None, file=None, symbol=None, note=None,
            lens=None, claim=None, test=None, findings=None, withdraw=False,
            command=None, parent=None, gone=None, now=None, why=None,
            target=None, name=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_withdraw_on_add_is_seen(self):
        stray = cli._stray_review_flags(
            self._args("add", file="a.py", note="n", withdraw=True))
        self.assertEqual([f for f, _ in stray], ["withdraw"])
        self.assertEqual(dict(stray)["withdraw"], ["defer"],
                         "the refusal has to say where the flag is read")

    def test_the_same_flags_on_the_action_that_reads_them_are_fine(self):
        """The control, for every action, against what its branch reads."""
        clean = {
            "add": dict(file="a.py", note="n", lens="l", task="t"),
            "amend": dict(claim=["c"], note="n"),
            "close": dict(claim=["c"], test="t.py", command="c", parent="p"),
            "defer": dict(claim=["c"], why="w", target="t", withdraw=True),
            "done": dict(lens="l", findings=0, task="t"),
            "group": dict(name="n", claim=["a", "b"], why="w"),
            "lens": dict(lens="l", task="t"),
        }
        for action, kw in clean.items():
            with self.subTest(action=action):
                self.assertEqual(
                    cli._stray_review_flags(self._args(action, **kw)), [])

    def test_findings_zero_is_a_value(self):
        """`--findings 0` is the only way `ran and found nothing` exists."""
        self.assertEqual([f for f, _ in cli._stray_review_flags(
            self._args("add", file="a.py", note="n", findings=0))],
            ["findings"])

    def test_every_flag_the_parser_takes_has_a_home(self):
        """A flag in no action's set would be refused everywhere, silently."""
        parser_flags = {"task", "file", "symbol", "note", "lens", "claim",
                        "test", "findings", "withdraw", "command", "parent",
                        "gone", "now", "why", "target", "name"}
        self.assertEqual(parser_flags - set(cli._REVIEW_FLAG_HOMES), set())

    def test_the_refusal_reaches_the_command(self):
        """Through `cmd_review`, in a repo where the finding would be filed.

        Without the check this returns 0 and prints `raised <id>` -- which is
        what the adopter saw, and read as the cancellation they had asked for.
        """
        args = self._args("add", file="mod.py", symbol="helper",
                          note="n" * 60, withdraw=True)
        args.repo = str(_repo_that_takes_findings(self))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_review(args)
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("--withdraw", err.getvalue())
        self.assertNotIn("raised", out.getvalue())


class AClosedFindingIsNoLongerDeferred(_Filing):
    """A8 -- `withdraw_deferral` says so in prose; now something says it in code.

    "A finding somebody genuinely put off is un-deferred by closing it" was the
    reason `withdraw_deferral` gives for refusing to cancel a live deferral,
    and nothing implemented it. So `v4 ship` printed a repaired finding as
    outstanding on every ship after the repair, and its committed record went on
    naming work that was finished.
    """

    def _deferred_finding(self):
        cid, _made, _sib = review.raise_finding(
            self.conn, self.cfg, task_id=None, file="mod.py", symbol="helper",
            note="a real finding, put off on purpose")
        review.defer(self.conn, self.root, cfg=self.cfg, claim_id=cid,
                     why="w" * 60, target="issue #7", actor="agent")
        return cid

    def _close(self, cid):
        ledger.append_attempt(
            self.conn, claim_id=cid, subject_digest="{}", checker_sha="s",
            config_sha="cf", head_commit="hc", worktree="w", argv="[]",
            exit_code=0, stdout="", stderr="", started_at="2026",
            ended_at="2026", duration_ms=1,
            claim_digest=ledger.claim_digest(self.conn, cid), facts_sha="")
        self.conn.commit()

    def test_a_deferral_still_stands_until_the_finding_closes(self):
        """The control, and the first half of the mechanism."""
        cid = self._deferred_finding()
        self.assertIn(cid, [c for c, _t in review.deferred(self.conn)])

    def test_closing_the_finding_takes_it_off_the_count(self):
        cid = self._deferred_finding()
        self._close(cid)
        self.assertNotIn(cid, [c for c, _t in review.deferred(self.conn)])

    def test_a_signature_settles_it_too(self):
        cid = self._deferred_finding()
        ledger.insert(self.conn, "accepted_risk", claim_id=cid,
                      kind="unprovable", who="a@x", why="w" * 80, was_tty=0,
                      git_record=f".v4/risks/{cid}.json", subject_digest="{}",
                      created_at="2026", scope="claim", cover_key="")
        self.conn.commit()
        self.assertNotIn(cid, [c for c, _t in review.deferred(self.conn)])

    def test_the_record_itself_is_untouched(self):
        """Accounting moved; the ledger and the committed file did not."""
        cid = self._deferred_finding()
        self._close(cid)
        self.assertIn(cid, [c for c, _t in review._standing_deferrals(self.conn)])
        self.assertTrue((self.root / review.DEFER_DIR / f"{cid}.json").is_file())

    def test_withdraw_still_refuses_for_the_right_reason(self):
        """The interaction: `withdraw_deferral` reads the record, not the count.

        It asks `deferred` whether there is anything to cancel, and a closed
        finding is no longer there -- so without the split it would refuse with
        "there is nothing to cancel" about a record sitting in the repo.
        """
        cid = self._deferred_finding()
        self._close(cid)
        with self.assertRaises(review.CannotDefer) as caught:
            review.withdraw_deferral(self.conn, self.root, cfg=self.cfg,
                                     claim_id=cid, why="w" * 60)
        self.assertIn("exists", str(caught.exception))
        self.assertNotIn("nothing to cancel", str(caught.exception))


class TheEscapeHatchPointsSomewhereReal(unittest.TestCase):
    """A5 -- the last line of a generated document, in a repo that is not this one.

    `CLAUDE.md` is generated into the adopter's repo and read by the agent
    working there. Its final line is the only sentence that says where to go
    when the document does not cover something, and it named `docs/SPEC.md` and
    `docs/RATIONALE.md` -- two files that exist in the framework and in no
    adopter.
    """

    def test_in_the_framework_it_names_the_relative_paths(self):
        line = doctrine.where_the_documents_are(ROOT)
        self.assertIn("docs/SPEC.md", line)
        self.assertTrue((ROOT / "docs" / "SPEC.md").is_file())

    def test_in_an_installed_adopter_it_names_a_path_that_opens(self):
        tmp = _git_repo(self)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "home").write_text(str(ROOT) + "\n")
        line = doctrine.where_the_documents_are(tmp)
        quoted = [p for p in line.replace("`", " ").split()
                  if p.endswith("SPEC.md")]
        self.assertTrue(quoted, line)
        self.assertTrue(Path(quoted[0]).is_file(),
                        f"{quoted[0]} is what the reader is told to open")

    def test_in_a_clone_it_says_where_the_documents_live(self):
        """`.v4/home` is gitignored, so a clone has none -- and is most lost."""
        tmp = _git_repo(self)
        line = doctrine.where_the_documents_are(tmp)
        self.assertIn(doctrine.PUBLIC_REPO, line)
        self.assertNotIn(f"`{tmp}", line, "no path this machine cannot resolve")

    def test_the_generated_document_ends_with_it(self):
        """Through `render`, which is what an adopter actually reads."""
        tmp = _adoptable_repo(self)
        text = doctrine.render(config.RepoConfig(tmp))
        self.assertTrue(text.rstrip().endswith(
            doctrine.where_the_documents_are(tmp)))


if __name__ == "__main__":
    unittest.main()
