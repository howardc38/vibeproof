"""Four mechanisms whose repairs happened to land on the same day.

    python3 -m unittest tests.test_repairs_that_arrived_together -v

The file was called `test_sweep_repairs.py` and its docstring was "Closing
tests for the 2026-08-14 sweep findings" -- named after a calendar event rather
than a module or a contract, and nothing in it is about sweeping. A reader
looking for the deferral-reconciliation test has no reason to open a file named
after a date, and the next sweep's closures have nowhere obvious to go but here.

What it holds, and why each is here rather than beside its subject: every one
of the four is a *reader* that was missing for a writer that already existed,
so the test has to run both halves and neither half's own test file is the
natural home for that.

  `ledger.reconcile_deferrals`      -- `.v4/deferred/` had no reconciliation
  `engagement.before_the_work`      -- written, and read by no production caller
  `lifecycle.continues` / `continued_by` -- same, for the task-continuation edge
  `doctor`'s symbol-refs row        -- `SYMBOL_REF_KEYS` had no reader
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel import config as config_mod          # noqa: E402
from kernel import engagement, ledger, lifecycle, review  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _repo(td):
    """A repo with one open task and enough config to load."""
    root = Path(td)
    (root / ".v4").mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    src = json.loads((ROOT / ".v4" / "config.json").read_text())
    (root / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": src["policy"],
         "thresholds": src["thresholds"]}))
    (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"lint": {"question_template": "q", "checker": "lint",
                  "staleness": "repo", "engagement": True,
                  "rule": [{"text": "r" * 50, "source": "s"}]}}))
    (root / ".v4" / "checkers.json").write_text("{}")
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    conn = ledger.connect(root)
    ledger.insert(conn, "task", id="t", request="r" * 80,
                  scope_globs=json.dumps(["**"]), base_commit=head,
                  created_at="2026-01-01T00:00:00+00:00")
    return root, conn


class ADeferralIsReconciledAgainstItsCommittedRecord(unittest.TestCase):
    """`kernel/review.py::defer` writes two records and nothing compared them.

    Its own comment says the file is there "like a signature record", and the
    thing it is like is reconciled in both directions. This one was not, so a
    hand-written or deleted deferral was invisible to `v4 audit`, to staleness
    and to `scope` -- `.v4/deferred/` is in KERNEL_WRITTEN, which is what makes
    the last one true.
    """

    def _deferred_claim(self, conn, root):
        ledger.insert(conn, "claim", id="cx", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="review", created_at="2026")
        review.defer(conn, root, claim_id="cx",
                     why="this finding is real and the repair belongs in the "
                         "task that owns that module, not in this one",
                     target="t-later", actor="agent")
        # Committed, because "commit it" is half of what the record is for: the
        # ledger lives in .git/ and a clone carries the file instead.
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "defer"], cwd=root, check=True)

    def test_a_deleted_record_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            self._deferred_claim(conn, root)
            self.assertEqual(review.reconcile_deferrals(conn, root), [],
                             "a freshly written deferral should reconcile")
            (root / ".v4" / "deferred" / "cx.json").unlink()
            problems = review.reconcile_deferrals(conn, root)
            self.assertTrue(problems, "the record was deleted and nothing said so")
            self.assertIn("cx", problems[0])
            conn.close()

    def test_an_edited_record_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            self._deferred_claim(conn, root)
            p = root / ".v4" / "deferred" / "cx.json"
            rec = json.loads(p.read_text())
            rec["target"] = "somewhere else entirely"
            p.write_text(json.dumps(rec))
            problems = review.reconcile_deferrals(conn, root)
            self.assertTrue(any("target" in x for x in problems),
                            f"the file and the ledger disagree and nothing "
                            f"noticed: {problems}")
            conn.close()

    def test_a_record_the_ledger_never_made_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            d = root / ".v4" / "deferred"
            d.mkdir(parents=True)
            (d / "invented.json").write_text(json.dumps(
                {"claim": "invented", "why": "w", "target": "t"}))
            problems = review.reconcile_deferrals(conn, root)
            self.assertTrue(any("invented" in x for x in problems))
            conn.close()

    def test_it_does_not_hold_the_chain(self):
        """A deferral makes no claim terminal, so a stale one must not block.

        `reconcile_signatures` feeds `audit_chain`, which gates `ship`. Putting
        this there would say a repo with an out-of-date deferral file cannot
        ship, which is untrue and is how a gate earns being routed around.
        """
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            self._deferred_claim(conn, root)
            (root / ".v4" / "deferred" / "cx.json").unlink()
            ok, problems = ledger.audit_chain(conn, root)
            self.assertTrue(review.reconcile_deferrals(conn, root))
            self.assertNotIn("cx", " ".join(problems),
                             "a missing deferral record reached the chain verdict")
            conn.close()


class ThePreWorkSentenceReachesWhoeverWritesTheNextOne(unittest.TestCase):
    """`kernel/engagement.py::before_the_work` had no production caller.

    `v4 engage --task X --kind Y` writes a sentence and the kernel says it will
    be "readable against" the claim when one arrives. The reader existed and
    nothing called it, so the sentence was collected and never shown to the one
    person it was collected for.
    """

    def test_engage_shows_what_was_said_before_the_claim_existed(self):
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            cfg = config_mod.RepoConfig(root)
            said = ("the lint rule is about imports crossing a package boundary, "
                    "and this task moves two modules between packages")
            engagement.judge_before(conn, cfg, task_id="t", kind="lint",
                                    sentence=said, actor="worker")
            self.assertEqual(engagement.before_the_work(conn, "t", "lint")[1],
                             said, "the writer half is not the thing under test")
            ledger.insert(conn, "claim", id="cy", task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
            conn.close()

            r = subprocess.run(
                [sys.executable, "-m", "kernel.cli", "--repo", str(root),
                 "engage", "--claim", "cy"],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
                env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
            self.assertIn(
                said, r.stdout,
                f"the sentence written before the work was not shown to whoever "
                f"is writing the one this claim asks for:\n{r.stdout}\n{r.stderr}")


class TheContinuationEdgeIsQueryable(unittest.TestCase):
    """`continues` / `continued_by` had no production caller.

    `open_task` writes a `task_continues` event specifically so that "what
    continued t-dead-tests" stops living inside `task.request`, where only a
    regex could find it. Both accessors existed and nothing called them, so the
    query still had no answer.
    """

    def test_status_carries_both_directions(self):
        with tempfile.TemporaryDirectory() as td:
            root, conn = _repo(td)
            ledger.insert(conn, "task", id="t2", request="r" * 80,
                          scope_globs=json.dumps(["**"]), base_commit="x",
                          created_at="2026-01-02T00:00:00+00:00")
            ledger.insert(conn, "event", task_id="t2", claim_id=None,
                          kind=lifecycle.CONTINUES_KIND, actor="person",
                          payload={"after": "t"}, created_at="2026")
            self.assertEqual(lifecycle.continues(conn, "t2"), "t")
            self.assertEqual(lifecycle.continued_by(conn, "t"), ["t2"])
            conn.close()

            r = subprocess.run(
                [sys.executable, "-m", "kernel.cli", "--repo", str(root),
                 "status", "--task", "t2", "--json"],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
                env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
            out = json.loads(r.stdout)
            self.assertEqual(
                out.get("continues"), "t",
                f"the edge the open_task event exists to record is not in the "
                f"one report an orchestrator reads:\n{r.stdout}")


class AStaleSymbolReferenceIsReported(unittest.TestCase):
    """`SYMBOL_REF_KEYS` had no reader while its sibling has two.

    Its own comment says "reported rather than acted on, for the same reason as
    protected_paths" -- and `protected_paths` is read by `doctor.run` and this
    was read by nothing. `public_routes` is where a repo writes down which
    handlers are deliberately unauthenticated, so a stale entry reads as a
    decision about code that is not here.
    """

    def _repo_with_facts(self, td, public_routes):
        """Seed a temp repo with this repo's own table, minus the routes.

        The source path was spelled `.v4/facts.{ROOT.name}.json`, which is only
        this repo's table while the checkout happens to be named `vibeproof`
        -- copy it anywhere else and both tests here died on a bare
        `FileNotFoundError`. `RepoConfig` already owns that resolution and
        handles the case the literal cannot (`_load_facts` prefers the named
        table and falls back to the first `.v4/facts*.json`), so it is asked
        rather than re-spelled.
        """
        root, conn = _repo(td)
        conn.close()
        src = json.loads(config_mod.RepoConfig(ROOT).facts_path.read_text())
        src["public_routes"] = public_routes
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(src))
        return root

    def test_a_reference_to_a_file_that_is_gone_is_named(self):
        from kernel import doctor
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_facts(td, ["a.py::handler"])
            rows = {r["what"]: r for r in doctor.run(root)}
            self.assertEqual(rows["symbol refs"]["status"], "ok",
                             "a.py is tracked, so this reference resolves")
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_with_facts(td, ["core/gone.py::handler"])
            rows = {r["what"]: r for r in doctor.run(root)}
            self.assertEqual(
                rows["symbol refs"]["status"], "warn",
                "a public_routes entry names a file this repo does not have, "
                "and the report said everything resolves")
            self.assertIn("core/gone.py::handler", rows["symbol refs"]["detail"])


if __name__ == "__main__":
    unittest.main()


class ABaselineWarningIsAboutALiveTask(unittest.TestCase):
    """`doctor`'s baseline row read the last attempt of every claim ever.

    A task that shipped or was abandoned took its failures with it: the claim
    will never be attempted again, so its exit code is a fact about a day.
    Measured -- `lint` was reported as needing a baseline from an attempt on
    2026-08-12, on a task long since ended, after the cause had been repaired
    and `lint` exited 0. The sentence it printed, "this task fails on work it
    did not cause", was about no task at all.
    """

    def _repo(self, *, ended):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"lint": {"checker": "lint", "question_template": "q",
                      "staleness": "repo", "baseline": True}}))
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        ledger.append_attempt(conn, claim_id="c1", subject_digest="d",
                              checker_sha="s", config_sha="c", head_commit="h",
                              worktree="w", argv="[]", exit_code=1, stdout="",
                              stderr="", started_at="2026", ended_at="2026",
                              duration_ms=1)
        if ended:
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="abandoned", actor="kernel", payload={},
                          created_at="2026")
        return root

    def _baselines(self, root):
        from kernel import doctor
        rows = {r["what"]: r for r in doctor.run(root)}
        self.assertIn("baselines", rows)
        return rows["baselines"]

    def test_a_live_task_still_asks_for_the_baseline(self):
        row = self._baselines(self._repo(ended=False))
        self.assertIn("missing", row["detail"])

    def test_an_ended_task_does_not(self):
        """The control on the other side: a failure on a task nobody will run
        again is not a baseline this repo is missing."""
        row = self._baselines(self._repo(ended=True))
        self.assertNotIn("missing", row["detail"])


class ABrowserBundleWithNoUiGlobs(unittest.TestCase):
    """A fifth reader for a writer that existed: the repo says browser, facts say nothing.

    `facts.propose` finds client code by view-file suffix, which is right where
    `.tsx` exists and silent where it does not. Measured on the eval corpus:
    `KaTeX` and `csstree` both build a browser bundle -- webpack, rollup,
    esbuild -- out of plain `.ts`, so no view file exists to find and
    `ui_globs` came back empty for both. Every rule about what reaches a
    browser is then off in exactly the repos that have one, and off reads the
    same as clean.

    This says so rather than guessing a directory, and the reason it does not
    guess is the last guess: proposing the top-level directory produced a
    finding against a build-time Node script.
    """

    def _repo(self, manifest=None, ui_globs=None):
        import json as _json
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        if manifest is not None:
            (tmp / "package.json").write_text(_json.dumps(manifest))
        (tmp / ".v4" / "facts.x.json").write_text(
            _json.dumps({"ui_globs": ui_globs or []}))
        return tmp

    def _row(self, root):
        from kernel import doctor
        out = []
        doctor._check_a_browser_bundle_with_no_ui_globs(root, out)
        return out[0] if out else None

    def test_a_declared_browser_entry_with_no_ui_globs_is_reported(self):
        row = self._row(self._repo({"browser": {"a": "b"}, "unpkg": "d.js"}))
        self.assertIsNotNone(row, "csstree's shape, and it says nothing today")
        self.assertIn("browser", row["detail"])
        self.assertIn("unpkg", row["detail"])

    def test_and_it_stops_once_the_repo_has_said_where(self):
        self.assertIsNone(
            self._row(self._repo({"unpkg": "d.js"}, ui_globs=["src/**"])))

    def test_a_package_with_no_browser_field_is_not_asked(self):
        """`main` alone is a Node package. Asking every package.json is noise."""
        self.assertIsNone(self._row(self._repo({"main": "index.js"})))

    def test_a_repo_with_no_package_json_is_not_asked(self):
        self.assertIsNone(self._row(self._repo(manifest=None)))
