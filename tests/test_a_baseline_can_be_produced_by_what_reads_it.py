"""`external-write` and `fail-closed` consumed a baseline nothing could produce.

    python3 -m unittest tests.test_a_baseline_can_be_produced_by_what_reads_it -v

Both checkers have called `kernel.baseline.forgive` since they were written, and
`--emit-baseline` existed on `structural_lint` alone. So the two kinds that
raise per-symbol findings across a whole file -- the two that turn "add sixteen
lines to a large module" into "answer everything that module has ever done" --
were the two with no way to carry anything.

Measured on the reference adopter, 2026-08-26: `app/chat/poller/poller.py`
took a 16-line addition and 26 claims came back, 23 of them on symbols the task
never touched. 24 of that task's 38 claims never ran at all, because both kinds
gate on a written sentence per claim first.

All of this fails against the commit before this file.
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
sys.path.insert(0, str(ROOT / "checkers"))

from kernel import baseline  # noqa: E402

FAIL_OPEN = '''\
import requests


def send(url):
    """An outbound write whose failure is swallowed."""
    try:
        requests.post(url, json={"a": 1})
    except Exception:
        pass
'''


def _repo(case, files):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    return tmp


def _run(root, checker, *extra, refs=()):
    """`refs` names the files a normal run judges.

    A subject naming none of them exits 4 -- "the subject named no files to
    analyse" -- so a scoped run and a whole-repo sweep are different questions
    to this checker, which is exactly why `--emit-baseline` cannot reuse the
    subject's list.
    """
    sub = root / "subject.json"
    sub.write_text(json.dumps({
        "repo_root": str(root),
        "subject_refs": [{"kind": "file", "path": r} for r in refs]}))
    return subprocess.run(
        [sys.executable, str(ROOT / "checkers" / checker),
         "--subject", str(sub), *extra],
        capture_output=True, text=True, cwd=str(ROOT))


class WhatIsEmittedIsWhatIsRead(unittest.TestCase):
    """The producer and the reader are the same module, so they cannot drift.

    `structural_lint` kept its own writer and `kernel.baseline` kept the reader;
    a second producer for two more kinds would have been a third opinion about
    one file format.
    """

    def test_block_is_exactly_what_load_accepts(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        text = baseline.block([{"id": "aaa", "path": "x.py", "note": ""},
                               {"id": "bbb", "path": "y.py", "note": ""}])
        baseline.path(tmp, "fail-closed").write_text(text)
        accepted, present = baseline.load(tmp, "fail-closed")
        self.assertTrue(present)
        self.assertEqual(accepted, {"aaa", "bbb"})

    def test_an_empty_repo_emits_a_file_that_forgives_nothing(self):
        """A baseline of no findings is a real answer and has to round-trip;
        an emitter that printed `null` for it would be discovered by whoever
        landed it, which is the worst place to find out."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        baseline.path(tmp, "fail-closed").write_text(baseline.block([]))
        accepted, present = baseline.load(tmp, "fail-closed")
        self.assertTrue(present)
        self.assertEqual(accepted, set())


class AFailClosedFindingCanBeCarried(unittest.TestCase):
    """The whole round trip, against the checker itself rather than a stub."""

    def setUp(self):
        self.root = _repo(self, {"app/send.py": FAIL_OPEN})

    def test_the_finding_is_reported_then_emitted_then_carried(self):
        first = _run(self.root, "fail_closed.py", refs=["app/send.py"])
        self.assertEqual(first.returncode, 1,
                         f"expected a finding first\n{first.stdout}{first.stderr}")

        emitted = _run(self.root, "fail_closed.py", "--emit-baseline")
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        rows = json.loads(emitted.stdout)["findings"]
        self.assertTrue(rows, "the emitter found nothing the checker had just failed on")
        self.assertIn("send", {r["symbol"] for r in rows})

        baseline.path(self.root, "fail-closed").write_text(emitted.stdout)
        after = _run(self.root, "fail_closed.py", refs=["app/send.py"])
        self.assertEqual(after.returncode, 0,
                         f"the emitted ids did not forgive the findings they came "
                         f"from\n{after.stdout}{after.stderr}")
        self.assertIn("carrying", after.stdout,
                      "carried debt has to be printed on the pass, or it is a "
                      "record nobody reads")

    def test_a_new_finding_is_not_forgiven_by_a_baseline_that_predates_it(self):
        """Otherwise this would be an off switch rather than a delta."""
        emitted = _run(self.root, "fail_closed.py", "--emit-baseline")
        baseline.path(self.root, "fail-closed").write_text(emitted.stdout)
        (self.root / "app" / "other.py").write_text(
            FAIL_OPEN.replace("def send(", "def send_again("))
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        after = _run(self.root, "fail_closed.py", refs=["app/other.py"])
        self.assertEqual(after.returncode, 1,
                         f"a finding written after the baseline was forgiven by "
                         f"it\n{after.stdout}{after.stderr}")


class TheEmitterSweepsTheRepoAndNotTheSubject(unittest.TestCase):
    """A baseline built from one task's files forgives that task's neighbours
    and leaves everyone else's, which is the shape this exists to end."""

    def test_a_file_the_subject_never_names_is_still_in_the_baseline(self):
        root = _repo(self, {"app/send.py": FAIL_OPEN,
                            "other/far.py": FAIL_OPEN.replace("def send(",
                                                              "def far_away(")})
        emitted = _run(root, "fail_closed.py", "--emit-baseline")
        self.assertEqual(emitted.returncode, 0, emitted.stderr)
        paths = {r["path"] for r in json.loads(emitted.stdout)["findings"]}
        self.assertIn("other/far.py", paths)


if __name__ == "__main__":
    unittest.main()
