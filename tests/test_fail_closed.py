"""Acceptance for the ``fail-closed`` detector and checker.

Run either way, no third-party anything::

    python3 -m unittest discover -s tests -t .
    python3 tests/test_fail_closed.py

Both executables are driven as **subprocesses**, never imported.  The exit code is
the product -- an in-process call would test the analysis and quietly skip the
only thing the kernel actually reads (DESIGN.md §6: the exit code comes from the
OS, never from a claim).

What is asserted:

* every file under ``red/`` exits 1, and names a real line of itself on stdout
* every file under ``green/`` exits 0, individually and all together
* ``known_miss/`` exits 0 -- a documented blind spot, asserted so it cannot rot
  into an unexamined belief
* unverifiable input is 4, never 0; a broken checker is 5, never 1
* the detector is byte-identical across runs, including order
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETECTOR = ROOT / "detectors" / "fail_closed.py"
CHECKER = ROOT / "checkers" / "fail_closed.py"
FIXTURES = ROOT / "tests" / "fixtures" / "fail_closed"

RED = sorted((FIXTURES / "red").glob("*.py"))
GREEN = sorted((FIXTURES / "green").glob("*.py"))
KNOWN_MISS = sorted((FIXTURES / "known_miss").glob("*.py"))
#: The same defect rewritten to look like it evades. The gate demands
#: three and its first run found 13 real evasions across 20 checkers, so
#: this is the colour that carries the most information -- and it was the
#: one no test in this file read.
BYPASS = sorted((FIXTURES / "bypass").glob("*.py"))

EXIT_PASS, EXIT_FAIL, EXIT_CANNOT_VERIFY, EXIT_BROKEN = 0, 1, 4, 5


def subject(paths, *, repo_root=ROOT, symbol="", variant="", extra_refs=()):
    return {
        "claim_id": "test-claim",
        "claim_kind": "fail-closed",
        "task_id": "t-test",
        "repo_root": str(repo_root),
        "diff_base": "HEAD",
        "subject_refs": [
            {"kind": "file", "path": str(Path(p))} for p in paths
        ] + list(extra_refs),
        "symbol": symbol,
        "variant": variant,
        "params": {},
    }


def run(tool: Path, payload) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh)
        path = fh.name
    try:
        return subprocess.run(
            [sys.executable, str(tool), "--subject", path],
            capture_output=True,
            text=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class FixtureLayout(unittest.TestCase):
    def test_enough_fixtures_to_mean_something(self):
        self.assertGreaterEqual(len(RED), 5, "need >=5 red fixtures")
        self.assertGreaterEqual(len(GREEN), 5, "need >=5 green fixtures")
        self.assertGreaterEqual(len(BYPASS), 3, "need >=3 bypass fixtures")

    def test_a_bypass_is_caught_like_the_red_case_it_rewrites(self):
        """The colour this file never read.

        A bypass is the same defect written to look like it evades -- an
        aliased import, a wrapper, a renamed symbol -- so it has to fail
        exactly as red does. Running them here is what stops the registration
        gate being the only place they are ever executed.
        """
        for path in BYPASS:
            with self.subTest(fixture=rel(path)):
                result = run(CHECKER, subject([rel(path)]))
                self.assertEqual(result.returncode, EXIT_FAIL, result.stdout)

    def test_every_fixture_parses(self):
        import ast

        for path in RED + GREEN + KNOWN_MISS:
            with self.subTest(fixture=rel(path)):
                ast.parse(path.read_text(encoding="utf-8"))


class CheckerVerdicts(unittest.TestCase):
    def test_red_fixtures_fail(self):
        for path in RED:
            with self.subTest(fixture=rel(path)):
                result = run(CHECKER, subject([rel(path)]))
                self.assertEqual(result.returncode, EXIT_FAIL, result.stdout)

    def test_red_fixtures_name_a_real_line(self):
        """stdout has to point somewhere a person can go."""
        for path in RED:
            with self.subTest(fixture=rel(path)):
                result = run(CHECKER, subject([rel(path)]))
                total = len(path.read_text(encoding="utf-8").splitlines())
                cited = [
                    line for line in result.stdout.splitlines()
                    if rel(path) in line and ":" in line
                ]
                self.assertTrue(cited, f"no file:line in stdout:\n{result.stdout}")
                for line in cited:
                    number = line.split(rel(path) + ":", 1)[1].split()[0]
                    self.assertTrue(number.isdigit(), line)
                    self.assertLessEqual(int(number), total, line)

    def test_green_fixtures_pass(self):
        for path in GREEN:
            with self.subTest(fixture=rel(path)):
                result = run(CHECKER, subject([rel(path)]))
                self.assertEqual(result.returncode, EXIT_PASS, result.stdout)

    def test_whole_directories(self):
        self.assertEqual(run(CHECKER, subject([rel(p) for p in RED])).returncode, EXIT_FAIL)
        self.assertEqual(run(CHECKER, subject([rel(p) for p in GREEN])).returncode, EXIT_PASS)

    def test_one_red_among_greens_still_fails(self):
        paths = [rel(p) for p in GREEN] + [rel(RED[0])]
        self.assertEqual(run(CHECKER, subject(paths)).returncode, EXIT_FAIL)

    def test_non_file_refs_are_ignored(self):
        extra = [{"kind": "symbol", "path": "does/not/exist.py"},
                 {"kind": "commit", "path": "deadbeef"}]
        result = run(CHECKER, subject([rel(GREEN[0])], extra_refs=extra))
        self.assertEqual(result.returncode, EXIT_PASS, result.stdout)


class ClaimScoping(unittest.TestCase):
    """A claim is (file, symbol, variant); the checker must answer that claim."""

    def test_symbol_narrows_the_verdict(self):
        path = FIXTURES / "red" / "durable_write_swallowed.py"
        both = run(CHECKER, subject([rel(path)]))
        self.assertEqual(both.returncode, EXIT_FAIL)

        one = run(CHECKER, subject([rel(path)], symbol="save_checkpoint"))
        self.assertEqual(one.returncode, EXIT_FAIL)
        self.assertIn("save_checkpoint", one.stdout)
        self.assertNotIn("record_receipt", one.stdout)

        absent = run(CHECKER, subject([rel(path)], symbol="no_such_function"))
        self.assertEqual(absent.returncode, EXIT_PASS, absent.stdout)

    def test_variant_narrows_the_verdict(self):
        path = FIXTURES / "red" / "durable_write_swallowed.py"
        falsey = run(CHECKER, subject([rel(path)], variant="falsey"))
        self.assertEqual(falsey.returncode, EXIT_FAIL)
        self.assertIn("record_receipt", falsey.stdout)
        self.assertNotIn("save_checkpoint", falsey.stdout)


class CannotVerify(unittest.TestCase):
    """4 is not 0.  "I could not read it" must never be recorded as "it is clean"."""

    def test_non_python_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "notes.md"
            note.write_text("not python\n")
            result = run(CHECKER, subject(["notes.md"], repo_root=tmp))
            self.assertEqual(result.returncode, EXIT_CANNOT_VERIFY, result.stdout)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(CHECKER, subject(["gone.py"], repo_root=tmp))
            self.assertEqual(result.returncode, EXIT_CANNOT_VERIFY, result.stdout)

    def test_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "broken.py").write_text("def f(:\n    pass\n")
            result = run(CHECKER, subject(["broken.py"], repo_root=tmp))
            self.assertEqual(result.returncode, EXIT_CANNOT_VERIFY, result.stdout)

    def test_no_files_at_all(self):
        result = run(CHECKER, subject([]))
        self.assertEqual(result.returncode, EXIT_CANNOT_VERIFY, result.stdout)

    def test_a_found_defect_outranks_an_unreadable_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "broken.py").write_text("def f(:\n")
            (Path(tmp) / "bad.py").write_text(
                (FIXTURES / "red" / "swallowed_http_post.py").read_text(encoding="utf-8")
            )
            result = run(CHECKER, subject(["broken.py", "bad.py"], repo_root=tmp))
            self.assertEqual(result.returncode, EXIT_FAIL, result.stdout)
            self.assertIn("bad.py", result.stdout)


class CheckerBroke(unittest.TestCase):
    """>=5 must be distinguishable from 1, or a tool bug reads as a code defect."""

    def test_unparseable_subject(self):
        self.assertEqual(run(CHECKER, "{not json").returncode, EXIT_BROKEN)

    def test_subject_without_repo_root(self):
        self.assertEqual(run(CHECKER, {"claim_kind": "fail-closed"}).returncode, EXIT_BROKEN)


class Detector(unittest.TestCase):
    CLAIM = "V4-CLAIM: kind=fail-closed file="

    def test_exit_zero_even_with_no_claims(self):
        result = run(DETECTOR, subject([rel(p) for p in GREEN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_claim_line_format(self):
        result = run(DETECTOR, subject([rel(p) for p in RED]))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith(self.CLAIM), line)
            fields = dict(part.split("=", 1) for part in line.split(": ", 1)[1].split(" "))
            self.assertEqual(set(fields), {"kind", "file", "symbol", "variant", "line"})
            self.assertEqual(fields["kind"], "fail-closed")
            self.assertIn(fields["variant"], {"swallow", "falsey"})
            self.assertTrue(fields["line"].isdigit())
            self.assertTrue(fields["symbol"])

    def test_symbol_is_the_enclosing_definition(self):
        path = FIXTURES / "red" / "durable_write_swallowed.py"
        out = run(DETECTOR, subject([rel(path)])).stdout
        symbols = {
            dict(p.split("=", 1) for p in line.split(": ", 1)[1].split(" "))["symbol"]
            for line in out.splitlines()
        }
        self.assertEqual(symbols, {"save_checkpoint", "record_receipt"})

    def test_byte_identical_across_runs(self):
        """Re-derivation must not invent new claim ids for unchanged files."""
        payload = subject([rel(p) for p in RED + GREEN])
        first = run(DETECTOR, payload)
        second = run(DETECTOR, payload)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout.splitlines(), sorted(first.stdout.splitlines()))

    def test_order_does_not_follow_the_subject(self):
        forward = run(DETECTOR, subject([rel(p) for p in RED]))
        backward = run(DETECTOR, subject([rel(p) for p in reversed(RED)]))
        self.assertEqual(forward.stdout, backward.stdout)

    def test_survives_a_file_it_cannot_parse(self):
        """A syntax error is a fact about the repo, not a broken detector."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "broken.py").write_text("def f(:\n")
            (Path(tmp) / "bad.py").write_text(
                (FIXTURES / "red" / "swallowed_http_post.py").read_text(encoding="utf-8")
            )
            result = run(DETECTOR, subject(["broken.py", "bad.py"], repo_root=tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("file=bad.py", result.stdout)
            self.assertIn("broken.py", result.stderr)

    def test_detector_and_checker_agree(self):
        """A claim the checker cannot reproduce would stay OPEN forever."""
        for path in RED:
            with self.subTest(fixture=rel(path)):
                claims = run(DETECTOR, subject([rel(path)])).stdout.splitlines()
                self.assertTrue(claims)
                for line in claims:
                    fields = dict(
                        p.split("=", 1) for p in line.split(": ", 1)[1].split(" ")
                    )
                    verdict = run(
                        CHECKER,
                        subject([fields["file"]], symbol=fields["symbol"],
                                variant=fields["variant"]),
                    )
                    self.assertEqual(verdict.returncode, EXIT_FAIL, verdict.stdout)


class KnownMiss(unittest.TestCase):
    """The blind spot, asserted rather than described.

    ``known_miss/exception_allowlist_predicate.py`` is the ``adopter_a``
    ``f0060ebb^`` defect with everything else stripped out: a closed-world
    ``isinstance`` allowlist over exception types, default-answering "known", read
    by a handler that re-raises.  Every structural test in this rule says the code
    fails closed, and it does -- while dropping the reconciliation baseline that
    stops the next attempt republishing.

    If this test starts failing, the rule got wider: move the fixture to ``red/``.
    """

    def test_checker_misses_the_exception_allowlist_defect(self):
        for path in KNOWN_MISS:
            with self.subTest(fixture=rel(path)):
                result = run(CHECKER, subject([rel(path)]))
                self.assertEqual(
                    result.returncode,
                    EXIT_PASS,
                    "this fixture is documented as a miss; if the checker now "
                    "catches it, move it into red/ and delete this test",
                )

    def test_detector_raises_no_claim_for_it(self):
        for path in KNOWN_MISS:
            with self.subTest(fixture=rel(path)):
                result = run(DETECTOR, subject([rel(path)]))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), "")


DEBT_SRC = """import requests


def send(url):
    try:
        requests.post(url, timeout=5)
    except Exception:
        pass
"""


class StandingDebtHasSomewhereToGo(unittest.TestCase):
    """`fail-closed` had no baseline, so a verified false positive was re-signed.

    Measured before this was built: `kernel/analysis/gosource.py` twice and `kernel/analysis/secret_patterns.py` once, nine signatures each. SPEC's Baseline section forbids
    exactly that -- `ACCEPTED_RISK` expires on the same key a PASS does, so a
    signature carrying standing debt is re-bought every time any file moves.

    A temp repo, because the point is the file the checker reads and not this
    repo's own debt.
    """

    def _repo(self, red_src, baseline_doc=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name)
        (tmp / ".v4").mkdir()
        (tmp / "a.py").write_text(red_src, encoding="utf-8")
        if baseline_doc is not None:
            (tmp / ".v4" / "fail-closed_baseline.json").write_text(
                baseline_doc, encoding="utf-8")
        return tmp

    def _run(self, tmp):
        return run(CHECKER, subject(["a.py"], repo_root=tmp))

    def _first_id(self, tmp):
        r = self._run(tmp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for line in r.stdout.splitlines():
            if line.strip().startswith("id "):
                return line.split()[1]
        self.fail("the checker failed and printed no id to accept it with:\n"
                  + r.stdout)

    def test_an_absent_file_forgives_nothing(self):
        """Fail-closed, and the direction matters: no file must not read as
        everything is accepted."""
        tmp = self._repo(DEBT_SRC)
        self.assertEqual(self._run(tmp).returncode, 1)

    def test_an_accepted_id_carries_instead_of_failing(self):
        tmp = self._repo(DEBT_SRC)
        fid = self._first_id(tmp)
        (tmp / ".v4" / "fail-closed_baseline.json").write_text(
            json.dumps({"accepted": [fid]}), encoding="utf-8")
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("carrying 1 accepted finding(s)", r.stdout,
                      "debt carried in silence is the record nobody reads")

    def test_an_unreadable_file_is_cannot_verify_and_not_a_pass(self):
        """A list of what is forgiven that cannot be read leaves the checker not
        knowing what is forgiven. 4 says that; 0 would be the hollow scan."""
        tmp = self._repo(DEBT_SRC, baseline_doc="{not json")
        self.assertEqual(self._run(tmp).returncode, 4)

    def test_the_id_survives_an_edit_above_it(self):
        """SPEC: finding id 唔准含行號. A baseline of line numbers forgives the
        wrong finding on the next commit."""
        tmp = self._repo(DEBT_SRC)
        before = self._first_id(tmp)
        (tmp / "a.py").write_text("# pushed down\n# by two lines\n" + DEBT_SRC,
                                  encoding="utf-8")
        self.assertEqual(self._first_id(tmp), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
