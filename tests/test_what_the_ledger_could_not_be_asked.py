"""Three things that happened and left the ledger unable to answer for them.

    python3 -m unittest tests.test_what_the_ledger_could_not_be_asked -v

Not one fact -- three, from the same family. Something ran (or deliberately did
not), and afterwards the table nobody can edit could not be asked about it.

  7d32ced  a claim `v4 check` skipped on purpose wrote no attempt, no cost
           observation and no event, so "held back" and "never reached" were
           the same row: none
  fc5a572  a detector run recorded no time at all, so the timeout it runs under
           had nothing to be held against, and 20,062 rows carried 951 distinct
           timestamps -- one per `derive`, every detector of a round stamped
           identically
  96bab8f  the correlation id for the one real external write this framework
           makes existed nowhere durable, so the rows it created in the truth
           owner could not be named afterwards

Each has its own class and its own red step.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import config, detector_protocol, ledger, lifecycle  # noqa: E402

CHECKER = """#!/usr/bin/env python3
import sys
sys.exit({code})
"""


def _repo(case) -> Path:
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


class AClaimTheKernelChoseNotToRun(unittest.TestCase):
    """7d32ced -- the decision is a fact, and facts go in the table.

    `derive._did_not_run` is this same fact one layer down, and its docstring
    says what the silence cost there: a refused detector "appeared in neither
    rep[detectors] nor rep[detectors_not_run], and `v4 ship` printed no line
    about it at all". Here it is worse -- the skip is deliberate, so the row
    that was missing is the record of a choice.
    """

    def setUp(self):
        self.root = _repo(self)
        (self.root / ".v4").mkdir()
        (self.root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"cheap": {"checker": "cheap", "staleness": "subject",
                       "question_template": "q"},
             "slow": {"checker": "slow", "staleness": "subject",
                      "question_template": "q"}}))
        (self.root / "checkers").mkdir()
        registry = {}
        for name, code in (("cheap", 1), ("slow", 0)):
            src = CHECKER.format(code=code)
            path = self.root / "checkers" / f"{name}.py"
            path.write_text(src)
            path.chmod(0o755)
            registry[name] = {
                "path": f"checkers/{name}.py", "kinds": [name],
                "reads": ["**/*.py"], "timeout_sec": 60,
                "sha256": hashlib.sha256(src.encode()).hexdigest(),
                "fixtures": ""}
        (self.root / ".v4" / "checkers.json").write_text(json.dumps(registry))
        (self.root / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)
        # `kind_cost` is a median over what this repo has actually paid, so the
        # only way to make a kind expensive is for it to have cost the time.
        ledger.insert(self.conn, "task", id="t-old", request="r" * 80,
                      scope_globs=["**"], base_commit="x", created_at="2026")
        for i in range(3):
            ledger.insert(self.conn, "claim", id=f"old{i}", task_id="t-old",
                          kind="slow", file="a.py", symbol="", variant="",
                          question="q", subject_refs="[]", checker="slow",
                          detector="d", origin="derive", created_at="2026")
            ledger.append_attempt(
                self.conn, claim_id=f"old{i}", subject_digest="{}",
                checker_sha="s", config_sha="cf", head_commit="hc",
                worktree="w", argv="[]", exit_code=0, stdout="", stderr="",
                started_at="2026", ended_at="2026",
                duration_ms=lifecycle.EXPENSIVE_MS * 2,
                claim_digest=ledger.claim_digest(self.conn, f"old{i}"),
                facts_sha="")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(self.conn, "task", id="t", request="r" * 80,
                      scope_globs=["**"], base_commit=head, created_at="2026")
        for cid, kind in (("c-cheap", "cheap"), ("c-slow", "slow")):
            ledger.insert(self.conn, "claim", id=cid, task_id="t", kind=kind,
                          file="a.py", symbol="", variant="", question="q",
                          subject_refs=json.dumps(
                              [{"kind": "file", "path": "a.py"}]),
                          checker=kind, detector="d", origin="derive",
                          created_at="2026")
        self.conn.commit()

    def _rows(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT claim_id, payload FROM event WHERE kind = ?",
            (lifecycle.NOT_RUN_KIND,))]

    def test_the_skip_leaves_a_row(self):
        list(lifecycle.check(self.conn, self.cfg, "t", run_expensive=False))
        self.assertEqual([r["claim_id"] for r in self._rows()], ["c-slow"])

    def test_and_the_row_says_what_held_it(self):
        list(lifecycle.check(self.conn, self.cfg, "t", run_expensive=False))
        payload = json.loads(self._rows()[0]["payload"])
        self.assertEqual(payload["held_by"], ["cheap"])
        self.assertEqual(payload["reason"], "expensive")
        self.assertEqual(payload["kind"], "slow")
        self.assertGreaterEqual(payload["cost_ms"], lifecycle.EXPENSIVE_MS,
                                "the measurement the decision was made on")

    def test_a_claim_that_ran_leaves_no_such_row(self):
        """The control: this row means skipped, not merely expensive."""
        list(lifecycle.check(self.conn, self.cfg, "t", run_expensive=True))
        self.assertEqual(self._rows(), [])

    def test_the_kind_is_in_the_ledger_vocabulary(self):
        """`insert` refuses an undeclared kind, so that declaration is what
        makes the row queryable rather than one nothing can look up."""
        self.assertIn(lifecycle.NOT_RUN_KIND, ledger.EVENT_KINDS)


class ADetectorRunThatTookNoTime(unittest.TestCase):
    """fc5a572 -- the checker half has measured this since it was written."""

    def _detector(self, body):
        root = _repo(self)
        (root / "a.py").write_text("x = 1\n")
        det = root / "d.py"
        det.write_text(body)
        return root, det

    def test_a_run_comes_back_with_what_it_cost(self):
        root, det = self._detector(
            "import time\ntime.sleep(0.2)\nprint('ok')\n")
        run = detector_protocol.run_detector(root, det, ["a.py"], timeout=30)
        self.assertGreaterEqual(run.duration_ms, 150,
                                "a 200ms sleep cannot measure as free")
        self.assertLess(run.duration_ms, 30_000)

    def test_the_tuple_still_unpacks_the_way_callers_write_it(self):
        """A `NamedTuple`, so both spellings reach the same values."""
        root, det = self._detector("print('ok')\n")
        run = detector_protocol.run_detector(root, det, ["a.py"], timeout=30)
        rc, out, err, payload, ms = run
        self.assertEqual((rc, out.strip(), payload),
                         (run.exit_code, "ok", run.out))
        self.assertEqual((err, ms), (run.stderr, run.duration_ms))

    def _repo_with_detectors(self, bodies):
        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text("{}")
        (root / ".v4" / "checkers.json").write_text("{}")
        (root / "detectors").mkdir()
        reg = {}
        for name, src in bodies.items():
            (root / "detectors" / f"{name}.py").write_text(src)
            reg[f"{name}.py"] = {
                "path": f"detectors/{name}.py", "cases": 0, "fixtures": "",
                "sha256": hashlib.sha256(src.encode()).hexdigest()}
        (root / ".v4" / "detectors.json").write_text(json.dumps(reg))
        (root / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=root,
                       capture_output=True)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(conn, "task", id="t", request="r" * 80,
                      scope_globs=["**"], base_commit=head, created_at="2026")
        conn.commit()
        from kernel import derive as derive_mod
        derive_mod.derive(conn, config.RepoConfig(root), task_id="t",
                          scope_globs=["**"], subject_files=["a.py"],
                          phase="open", detectors_dir=root / "detectors")
        return conn

    @staticmethod
    def _sleeper(seconds):
        return ("import argparse, time\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--subject'); p.add_argument('--facts')\n"
                "p.add_argument('--out'); p.parse_args()\n"
                f"time.sleep({seconds})\n")

    def test_derive_writes_it_on_the_row(self):
        """Through `derive`, which is the caller that owns the ledger."""
        conn = self._repo_with_detectors({"slowpoke": self._sleeper(0.2)})
        ran = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'detector_run'")]
        ran = [r for r in ran if r.get("ran")]
        self.assertTrue(ran, "the detector has to have run at all")
        self.assertGreaterEqual(ran[0]["duration_ms"], 150,
                                "the row has to carry what the run cost")

    def test_each_row_carries_its_own_instant(self):
        """The other half: 951 timestamps over 20,062 rows could not say which
        detector ran first, or where in a round the time went."""
        conn = self._repo_with_detectors({"one": self._sleeper(0.05),
                                          "two": self._sleeper(0.05)})
        stamps = [r["created_at"] for r in conn.execute(
            "SELECT created_at FROM event WHERE kind = 'detector_run' "
            "AND json_extract(payload, '$.ran') = 1")]
        self.assertEqual(len(stamps), 2, stamps)
        self.assertEqual(len(set(stamps)), 2,
                         "two runs 50ms apart, stamped once")


class TheIdThatNamesAnExternalWrite(unittest.TestCase):
    """96bab8f -- the one real external write, and the row it created.

    `run_id` is minted per invocation and substituted into both the trigger and
    the truth query, so it is the only thing tying an invocation to the row it
    made. It is off stdout on purpose (the registration gate compares stdout
    across two runs); `--out` is not compared, and `runner.record` keeps it as
    a `checker_out` event in the append-only ledger.
    """

    @staticmethod
    def _config(root):
        """The declaration this checker reads, and a truth owner it can ask.

        `truth_command: cat` answers with the query it was given, which is what
        makes this runnable anywhere: the proof's `truth` carries `{run_id}` and
        a marker, `cat` echoes it, and `contains:` finds the marker. The control
        query (`empty_query`, stdin closed) comes back empty, so this is not a
        command that answers everything the same way -- the refusal this checker
        makes first.
        """
        (root / ".v4").mkdir(exist_ok=True)
        (root / ".v4" / "config.json").write_text(json.dumps({
            "test_command": "true", "policy": "allow_accepted_risk",
            "truth_command": "cat",
            "runtime_proof": [{
                "name": "a write the truth owner can be asked about",
                "trigger": "true",
                "truth": "select 'probe-marker' where id = '{run_id}'",
                "expect": "contains:probe-marker"}]}))

    def _run(self, root, out):
        """The checker as `runner` runs it: a subject on disk and `--out`.

        `truth_command: cat` is a truth owner that answers with the query it was
        given, which is what makes this runnable anywhere: the proof's `truth`
        carries `{run_id}` and a marker, `cat` echoes it, and `contains:` finds
        the marker. The control query (`empty_query`, stdin closed) comes back
        empty, so the command is not one that answers everything the same way --
        the refusal this checker makes first.
        """
        self._config(root)
        subj = root / "subject.json"
        subj.write_text(json.dumps({
            "repo_root": str(root), "claim_id": "c1",
            "claim_kind": "runtime-proof", "subject_refs": [], "params": {}}))
        return subprocess.run(
            [sys.executable, str(ROOT / "checkers/runtime_proof.py"),
             "--subject", str(subj), "--out", str(out)],
            cwd=root, capture_output=True, text=True)

    def test_the_out_payload_names_the_run(self):
        root = _repo(self)
        out = root / "out.json"
        proc = self._run(root, out)
        self.assertTrue(out.is_file(), proc.stderr)
        payload = json.loads(out.read_text())
        self.assertIn("run_id", payload,
                      "without it the rows this checker created in the truth "
                      "owner cannot be named afterwards")
        self.assertTrue(payload["run_id"].startswith("rt"))

    def test_it_stays_off_stdout(self):
        """The constraint that kept it out in the first place: the registration
        gate compares stdout across two runs, so an id there makes every case
        non-deterministic for a reason unrelated to the checker."""
        root = _repo(self)
        out = root / "out.json"
        proc = self._run(root, out)
        run_id = json.loads(out.read_text())["run_id"]
        self.assertNotIn(run_id, proc.stdout)

    def test_main_itself_puts_the_id_in_the_payload(self):
        """The same assertion with `main` on the stack.

        The three above run the checker the way `runner` does -- a subprocess,
        which is the production shape, and the only shape in which "two runs
        agree on stdout" can be asked at all. It is also a shape no in-process
        tracer can see: `redgreen` watches frames, and a frame in another
        process is not one. So this enters `main` directly, and the finding
        this file closes is about `main`.
        """
        import importlib.util

        root = _repo(self)
        out = root / "out.json"
        self._config(root)
        subj = root / "subject.json"
        subj.write_text(json.dumps({
            "repo_root": str(root), "claim_id": "c1",
            "claim_kind": "runtime-proof", "subject_refs": [], "params": {}}))

        spec = importlib.util.spec_from_file_location(
            "runtime_proof_under_test", ROOT / "checkers/runtime_proof.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        argv = sys.argv
        sys.argv = ["runtime_proof.py", "--subject", str(subj),
                    "--out", str(out)]
        self.addCleanup(setattr, sys, "argv", argv)
        code = mod.main()

        self.assertEqual(code, 0, out.read_text() if out.is_file() else "")
        payload = json.loads(out.read_text())
        self.assertTrue(payload["run_id"].startswith("rt"))
        self.assertEqual(sorted(payload), ["results", "run_id", "scope"])
        self.assertEqual(payload["scope"]["status"], "unknown")

    def test_two_runs_differ_in_out_and_agree_on_stdout(self):
        """Said the way the gate asks it, rather than as a claim about one run."""
        root = _repo(self)
        a, b = root / "a.json", root / "b.json"
        first, second = self._run(root, a), self._run(root, b)
        self.assertEqual((first.returncode, first.stdout),
                         (second.returncode, second.stdout))
        self.assertNotEqual(json.loads(a.read_text())["run_id"],
                            json.loads(b.read_text())["run_id"])


if __name__ == "__main__":
    unittest.main()
