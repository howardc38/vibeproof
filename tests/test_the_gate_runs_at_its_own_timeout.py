"""The gate runs at the timeout it records.

    python3 -m unittest tests.test_the_gate_runs_at_its_own_timeout -v

Its own file because every case here is *meant* to be cut off, so the module
costs a few seconds of wall clock and nothing else in the suite should pay
that. `register` wrote `timeout_sec` into `.v4/checkers.json` and called
`verify_checker` without it, so a checker declaring 1800 was graded at the
120-second default -- judged under conditions it never runs under.

Fails against 0ad6b61.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import register  # noqa: E402


def _set(case, cases):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for colour, members in cases.items():
        (tmp / colour).mkdir(parents=True)
        for name, src in members.items():
            (tmp / colour / name).write_text(src)
    return tmp


class TheGateRunsAtTheTimeoutItRecords(unittest.TestCase):
    """`register` wrote `timeout_sec` into the registry and graded at 120.

    So a checker declaring 1800 was gated at a limit it never meets in
    production -- the gate judged it under conditions it does not run under.
    """

    #: Slow, and *correct*. A checker that always exits 1 is refused whether
    #: or not the timeout reached the gate, so it proves nothing: this one
    #: answers each case properly after two seconds, so the only thing that can
    #: refuse it is the limit.
    SLOW = ("#!/usr/bin/env python3\n"
            "import argparse, json, sys, time\n"
            "from pathlib import Path\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject', required=True)\n"
            "p.add_argument('--facts'); p.add_argument('--out')\n"
            "a = p.parse_args()\n"
            "s = json.loads(Path(a.subject).read_text())\n"
            "root = Path(s['repo_root'])\n"
            "time.sleep(2)\n"
            "bad = any('BOOM' in (root / r['path']).read_text()\n"
            "          for r in s.get('subject_refs', []))\n"
            "sys.exit(1 if bad else 0)\n")

    #: One red, one green, three bypass -- the smallest set the gate accepts
    #: with `min_cases=1`. Every case here times out by design, so the count is
    #: wall-clock: thirteen cases run twice at a one-second limit is a minute
    #: of the suite spent proving one number reached one call.
    def _fixtures(self):
        return _set(self, {"red": {"r.py": "# BOOM r\n"},
                           "green": {"g.py": "# g\n"},
                           "bypass": {f"b{i}.py": f"# BOOM evasion {i}\n"
                                      for i in range(3)}})

    def _checker(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "slow.py"
        path.write_text(self.SLOW)
        return path

    def test_a_short_timeout_reaches_the_gate(self):
        fixtures = self._fixtures()
        ok, report = register.verify_checker(
            repo_root=fixtures, checker_path=self._checker(),
            fixtures_dir=fixtures, kind="k", min_cases=1, timeout_sec=1)
        self.assertFalse(ok, "a two-second checker passed a one-second limit")
        self.assertTrue(report["failures"])

    def test_and_the_same_checker_answers_when_given_time(self):
        """The control. Without this the test above passes for a checker that
        is simply wrong, which is what the first version of it did."""
        fixtures = self._fixtures()
        ok, report = register.verify_checker(
            repo_root=fixtures, checker_path=self._checker(),
            fixtures_dir=fixtures, kind="k", min_cases=1, timeout_sec=30)
        self.assertTrue(ok, report["failures"])

    def _full_set(self):
        """Enough cases to satisfy the gate's own minimum.

        `register` takes no `min_cases`, so a short set is refused for being
        short -- which is the same verdict the timeout produces and proves
        nothing about which one fired. At the parent commit this set registers
        cleanly in 120 seconds; the point is that it does not in one.
        """
        return _set(self, {
            "red": {f"r{i}.py": f"# BOOM {i}\n" for i in range(5)},
            "green": {f"g{i}.py": f"# fine {i}\n" for i in range(5)},
            "bypass": {f"b{i}.py": f"# BOOM evasion {i}\n" for i in range(3)}})

    def test_register_hands_the_gate_the_number_it_records(self):
        """Through `register`, which is the symbol the finding names: it wrote
        the timeout into the registry and called the gate without it."""
        import tempfile as _t
        from kernel import ledger
        root = Path(_t.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "checkers.json").write_text("{}")
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        fixtures = self._full_set()
        good, report = register.register(
            conn, checkers_json=root / ".v4" / "checkers.json", checker_id="slow",
            checker_path=self._checker(), kinds=["k"],
            fixtures_dir=fixtures, repo_root=fixtures, timeout_sec=1)
        # `register` uses the gate's default `min_cases`, so this set is short
        # by design: the point is that the run was cut off at one second, and
        # a set that satisfies the count would spend the same minute proving
        # it.
        self.assertFalse(good, "a checker that cannot answer inside its own "
                               "recorded timeout was registered")
        self.assertTrue(report["failures"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
