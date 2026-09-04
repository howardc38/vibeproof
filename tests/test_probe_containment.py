"""The install probe runs a checker the same way a real check does.

    python3 -m unittest tests.test_probe_containment -v

``runner._run_contained`` was written against two measured failures, and its
docstring names both:

    A checker that exits 0 while a background process it spawned still holds
    the stdout pipe makes `capture_output` wait for the pipe rather than the
    process.

    A timeout kills the direct child only. Its descendants survive, keep
    writing, keep holding locks, and the next run inherits them.

``register.probe_repo_subject`` was the one call site that did not use it.  It
ran a checker under ``subprocess.run(capture_output=True, timeout=…)`` -- the
exact shape ``_run_contained`` replaced.

Found by running ``v4 install`` against the reference adopter.  The probe
reached ``checkers/test.py``, which shells out to that repo's real ``pytest -q``;
the 60-second timeout fired, the checker was killed, and the orphaned pytest
kept the pipe open.  The install passed twenty minutes without finishing and
left the adopter half-copied -- two checkers and two fixture directories short.

Two things pinned here:

* **A checker that orphans a child is not charged for it.**  It exits 0 in
  milliseconds and the probe returns in milliseconds.
* **The old shape is still measurably wrong.**  The same fixture under
  ``subprocess.run`` raises ``TimeoutExpired``, so this is a real difference and
  not a rewrite that happens to still pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import register, runner  # noqa: E402

#: Exits 0 at once, leaving a child alive that inherits stdout.
ORPHANS_A_CHILD = '''#!/usr/bin/env python3
import argparse, subprocess, sys
p = argparse.ArgumentParser()
p.add_argument("--subject"); p.add_argument("--facts"); p.add_argument("--out")
p.parse_args()
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("probe: I am done")
sys.exit(0)
'''

TIMEOUT_SEC = 6


def _repo(tmp: Path) -> Path:
    (tmp / ".v4").mkdir(parents=True, exist_ok=True)
    (tmp / ".v4" / "config.json").write_text('{"test_command": "true"}')
    (tmp / "slow.py").write_text(ORPHANS_A_CHILD)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


class TheProbeIsNotHeldByADescendant(unittest.TestCase):
    def test_a_checker_that_orphans_a_child_returns_at_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = _repo(Path(td))
            started = time.time()
            code, line = register.probe_repo_subject(
                repo_root=str(root), checker_path=str(root / "slow.py"),
                kind="x", timeout_sec=TIMEOUT_SEC)
            elapsed = time.time() - started
        self.assertEqual(code, 0, line)
        self.assertLess(elapsed, TIMEOUT_SEC,
                        f"the probe took {elapsed:.1f}s for a checker that exits "
                        f"immediately -- it is waiting on the pipe, not the process")

    def test_the_shape_it_replaced_is_still_wrong(self):
        """Without this, the fix above could be a rewrite that changed nothing.

        The same fixture under `subprocess.run(capture_output=True, timeout=)`
        must still raise, or the two implementations were never different and
        the twenty-minute install had another cause.
        """
        with tempfile.TemporaryDirectory() as td:
            root = _repo(Path(td))
            payload = {"claim_id": "p", "claim_kind": "x", "task_id": "probe",
                       "repo_root": str(root), "subject_refs": [], "symbol": "",
                       "variant": "", "params": {}}
            subject = Path(td) / "subject.json"
            subject.write_text(json.dumps(payload))
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run([sys.executable, str(root / "slow.py"),
                                "--subject", str(subject)],
                               cwd=root, capture_output=True, text=True,
                               env=runner.child_env(), timeout=TIMEOUT_SEC)


class OneOwnerForRunningACheckerOutOfProcess(unittest.TestCase):
    """`register` runs its probe through the same containment as everything
    else, asked by running one rather than by reading for the call.

    The source-text version passed on a file that mentions the name in a
    comment, and would have kept passing if the call moved to plain
    `subprocess.run` beside it.
    """

    def test_the_probe_goes_through_the_contained_runner(self):
        calls = []
        real = runner._run_contained

        def counting(argv, cwd, timeout_sec, tmpdir):
            calls.append(argv)
            return real(argv, cwd, timeout_sec, tmpdir)

        runner._run_contained = counting
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
                (root / ".v4").mkdir()
                (root / ".v4" / "config.json").write_text(json.dumps(
                    {"test_command": "true", "policy": "allow_accepted_risk"}))
                checker = root / "probe_checker.py"
                checker.write_text(
                    "import argparse, sys\n"
                    "p = argparse.ArgumentParser()\n"
                    "p.add_argument('--subject'); p.add_argument('--facts')\n"
                    "p.add_argument('--out'); p.parse_args()\n"
                    "print('nothing to say'); sys.exit(0)\n")
                register.probe_repo_subject(
                    repo_root=root, checker_path=checker, kind="probe",
                    timeout_sec=30)
        finally:
            runner._run_contained = real
        self.assertTrue(calls, "the probe did not go through the contained runner")



class ANeedsPatternCanNameAPerRepoFile(unittest.TestCase):
    """`needs` was a literal path, and one of the files it has to name is not.

    The facts table is `.v4/facts.<root-name>.json`, so `facts-coverage` could
    not express what it needs and `v4 install` held it back on a repo that had
    one -- reported once, on the install that copied it, and then invisible: a
    held-back kind does not raise, so nothing says it is not raising. That is
    the failure that kind exists to catch, one level out.
    """

    def test_a_literal_path_still_works(self):
        from kernel.install import _declared
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".v4").mkdir()
            self.assertFalse(_declared(root, ".v4/layers.json"))
            (root / ".v4" / "layers.json").write_text("{}")
            self.assertTrue(_declared(root, ".v4/layers.json"))

    def test_a_repo_named_file_is_found_by_pattern(self):
        from kernel.install import _declared
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".v4").mkdir()
            self.assertFalse(_declared(root, ".v4/facts.<repo>.json"))
            (root / ".v4" / "facts.whatever_this_repo_is_called.json").write_text("{}")
            self.assertTrue(_declared(root, ".v4/facts.<repo>.json"))

    def test_an_empty_needs_is_not_satisfied(self):
        from kernel.install import _declared
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(_declared(Path(td), ""))

    # `facts-coverage` was the one kind whose `needs` carried the `<repo>`
    # placeholder, and it was cut. The two kinds that still declare `needs`
    # name a literal file (`.v4/layers.json`, `.v4/control_plane_budget.json`),
    # so the placeholder has no user in this repo today.
    #
    # `_declared` still supports it and the two methods above still drive it
    # directly -- with the pattern, and with an empty one. What is gone is a
    # registered kind demonstrating it, not the capability.

if __name__ == "__main__":
    unittest.main()
