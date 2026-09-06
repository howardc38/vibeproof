"""Every registered checker, judged by its own fixture set.

    python3 -m unittest tests.test_every_checker_holds_its_fixtures -v

The fixture sets are the only thing that exercises a checker's verdict logic,
and nothing under `tests/` ran them: they execute under `v4 verify`, which the
suite never called. Measured -- injecting `sys.exit(0)` as the first statement
of `main()` in `webhook_replay`, `route_auth`, `dal_write`,
`registry_consistency`, `control_plane_budget`, `secret_chain`, `test_weakened`
and `design_pins` -- eight checkers that always PASS, judging nothing -- left
the whole suite green.

The `bypass/` colour was darker still. `test_fail_closed.py`,
`test_external_write.py`, `test_secret_scan.py`, `test_dependency_audit.py` and
`test_structural_lint.py` each assert `>= 5` red and `>= 5` green and never
read `bypass/` at all, so the colour that carries the most information -- the
registration gate's first run found 13 real evasions across 20 checkers -- was
being run for 2 of 29.

One test rather than one per checker, and driven off `.v4/checkers.json` rather
than a list: a checker registered tomorrow is covered without anybody
remembering, which is the property a hand-written list cannot have. That is
also why this is not simply "run the CI step locally" -- the CI step is a shell
loop over the same registry, and a loop nobody runs before pushing is a gate
that reports after the fact.

118 seconds for 31 checkers measured serially, so the cases are run in a small
pool. `v4 verify` is a subprocess per checker and they share nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _registered():
    """(id, checker path, fixtures dir, kind) for everything with a fixture set."""
    reg = json.loads((ROOT / ".v4" / "checkers.json").read_text())
    out = []
    for cid, entry in sorted(reg.items()):
        path, fixtures = entry.get("path"), entry.get("fixtures")
        kinds = entry.get("kinds") or []
        if path and fixtures and kinds:
            out.append((cid, path, fixtures, kinds[0]))
    return out


def _registered_detectors():
    """(name, detector path, fixtures dir) for everything with a fixture set.

    The counterpart `_registered` had none. A detector decides what gets
    checked at all, so a detector that emits nothing looks exactly like one
    that scanned and found a clean repo -- and a traced full-suite run entered
    `main()` in 4 of the 20 files under `detectors/`, leaving `sys.exit(0)` at
    the top of any of the others invisible to the suite.

    Driven off `.v4/detectors.json` for the reason the module docstring gives
    about the checker half: a detector registered tomorrow is covered without
    anybody remembering.
    """
    reg = json.loads((ROOT / ".v4" / "detectors.json").read_text())
    reg = reg.get("detectors", reg)
    out = []
    for name, entry in sorted(reg.items()):
        path, fixtures = entry.get("path"), entry.get("fixtures")
        if path and fixtures:
            out.append((name, path, fixtures))
    return out


def _verify_detector(spec):
    name, path, fixtures = spec
    r = subprocess.run(
        [sys.executable, "-m", "kernel.cli", "--repo", ".", "verify-detector",
         "--detector", path, "--fixtures", fixtures],
        cwd=ROOT, capture_output=True, text=True)
    return name, r.returncode, r.stdout, r.stderr


def _verify(spec):
    cid, path, fixtures, kind = spec
    r = subprocess.run(
        [sys.executable, "-m", "kernel.cli", "--repo", ".", "verify",
         "--checker", path, "--fixtures", fixtures, "--kind", kind],
        cwd=ROOT, capture_output=True, text=True)
    return cid, r.returncode, r.stdout, r.stderr


class EveryRegisteredCheckerStillHoldsItsFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = _registered()
        with ThreadPoolExecutor(max_workers=6) as pool:
            cls.results = list(pool.map(_verify, cls.specs))

    def test_there_is_something_to_run(self):
        """A registry that lost its fixture references would make every other
        assertion here vacuously true."""
        self.assertGreaterEqual(len(self.specs), 20,
                                "the registry names fewer checkers than this "
                                "repo ships -- see .v4/checkers.json")

    def test_every_one_of_them_is_registrable(self):
        bad = []
        for cid, code, out, err in self.results:
            if code != 0:
                lines = [l.strip() for l in out.splitlines()
                         if l.strip().startswith("- ")][:3]
                bad.append(f"{cid}: exit {code}\n      "
                           + "\n      ".join(lines or [err.strip()[:200]]))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_every_colour_actually_ran(self):
        """`registrable` with no cases would be the same silence one level in."""
        thin = []
        for cid, _code, out, _err in self.results:
            ran = {c: out.count(f"  ok   {c} ") + out.count(f"  FAIL {c} ")
                   for c in ("red", "green", "bypass")}
            if ran["red"] < 5 or ran["green"] < 5 or ran["bypass"] < 3:
                thin.append(f"{cid}: {ran}")
        self.assertEqual(thin, [], "\n".join(
            ["the gate needs 5 red, 5 green and 3 bypass and these ran fewer:"]
            + thin))


class OneOfThemRunInThisProcess(unittest.TestCase):
    """`verify_checker` runs each case as a subprocess, which is right for the
    gate and means the verdict logic runs somewhere this process cannot see.

    So one of the eight is also called here, on its own fixtures, with the
    subject its `.v4/fixture.json` declares -- red goes to 1, green to 0.
    """

    FIXTURES = ROOT / "tests" / "fixtures" / "dangling_ref"

    def _run(self, case_dir: Path):
        import contextlib
        import io
        import json
        import runpy
        import tempfile
        spec_path = case_dir / ".v4" / "fixture.json"
        spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subj = tmp / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(case_dir), **spec}))
        argv = sys.argv
        sys.argv = ["dangling_ref.py", "--subject", str(subj)]
        said, code = io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                try:
                    runpy.run_path(str(ROOT / "checkers" / "dangling_ref.py"),
                                   run_name="__main__")
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 0
        finally:
            sys.argv = argv
        return code, said.getvalue()

    def test_a_reference_to_nothing_is_a_finding(self):
        code, said = self._run(self.FIXTURES / "red" / "deep_module_missing")
        self.assertEqual(code, 1, said)
        self.assertIn("name something the module does not define", said)

    def test_and_one_that_resolves_is_not(self):
        greens = sorted(p for p in (self.FIXTURES / "green").iterdir()
                        if p.is_dir())
        code, said = self._run(greens[0])
        self.assertEqual(code, 0, said)


class EveryRegisteredDetectorHoldsItsFixtures(unittest.TestCase):
    """The half of the registry nobody ran, for the reason the other half is run.

    This file's own docstring argues that a CI shell loop over the registry "is
    a gate that reports after the fact", and made that argument for checkers
    only. `detectors/` had the same shell loop and the same silence, and it
    matters more there: a checker that always passes judges nothing, and a
    detector that raises nothing means no claim is ever put.

    Measured before this class existed: a traced run of all 106 test modules
    entered `main()` in 4 of the 20 files under `detectors/`. Seven registered
    ones and all nine `always_*.py` were never executed at all, so `sys.exit(0)`
    as the first line of any of them left the suite green.
    """

    @classmethod
    def setUpClass(cls):
        cls.specs = _registered_detectors()
        with ThreadPoolExecutor(max_workers=6) as pool:
            cls.results = list(pool.map(_verify_detector, cls.specs))

    def test_the_detector_registry_names_something(self):
        """A registry that lost its fixture references would make every other
        assertion here vacuously true.

        Named apart from the checker gate's sibling above. Three methods called
        `test_there_is_something_to_run` in one file read, to a detector
        comparing expectations by symbol, as one expectation moving from 20 to
        9 -- which `test-expectation` duly raised. The numbers did not move; the
        names were ambiguous, and to a reader as much as to the detector.
        """
        self.assertGreaterEqual(
            len(self.specs), 10,
            "the detector registry names fewer than this repo ships -- see "
            ".v4/detectors.json")

    def test_the_two_registry_readers_agree_on_shape(self):
        """The sentence this file was missing, and the one the finding is about.

        `_registered` reads `.v4/checkers.json` and `_registered_detectors`
        reads `.v4/detectors.json`, and until this class existed only the first
        had a gate behind it. What makes them a pair rather than two unrelated
        functions is that they answer the same question about two registries --
        every entry names a program that is here and a fixture set that is
        here -- and neither reader said so.

        Cheap on purpose: two JSON reads and a stat each. The verification above
        is what costs, and this does not repeat it.
        """
        for label, rows, arity in (("checkers", _registered(), 4),
                                   ("detectors", _registered_detectors(), 3)):
            self.assertTrue(rows, label)
            for row in rows:
                self.assertEqual(len(row), arity, row)
                name, path, fixtures = row[0], row[1], row[2]
                self.assertTrue(name, row)
                self.assertTrue((ROOT / path).is_file(),
                                f"{label}: {name} names {path}, which is not here")
                self.assertTrue((ROOT / fixtures).is_dir(),
                                f"{label}: {name} names fixtures {fixtures}, "
                                f"which is not a directory here")

    def test_every_one_of_them_is_registrable(self):
        bad = []
        for name, code, out, err in self.results:
            if code != 0:
                lines = [l.strip() for l in out.splitlines()
                         if l.strip().startswith("- ")][:3]
                bad.append(f"{name}: exit {code}\n      "
                           + "\n      ".join(lines or [err.strip()[:200]]))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_each_one_ran_both_colours(self):
        """`registrable` with no cases is the same silence one level in.

        Fewer than the checker gate asks for: `verify_detector` takes
        `min_cases` and the registry's own `cases` counts run from 6 upward, so
        the floor here is what the thinnest registered set actually holds
        rather than a number this test would like it to.
        """
        thin = []
        for name, _code, out, _err in self.results:
            ran = {c: out.count(f"  ok   {c} ") + out.count(f"  FAIL {c} ")
                   for c in ("red", "green")}
            if ran["red"] < 3 or ran["green"] < 3:
                thin.append(f"{name}: {ran}")
        self.assertEqual(thin, [], "\n".join(
            ["the gate needs 3 red and 3 green and these ran fewer:"] + thin))


class TheUnconditionalDetectorsRunToo(unittest.TestCase):
    """The nine the fixture gate cannot reach, run against what they declare.

    `always_*.py` raise one claim per task unconditionally, so they have no
    fixture set and could not have one: red and green cases exist to show a
    detector telling two situations apart, and these are not trying to. That is
    a reason they are ungated, not a reason they are unrun -- the same trace
    that found seven registered detectors never entered found these nine never
    entered either, and one of them silently exiting 0 means a kind stops being
    claimed on every task in this repo, with `v4 ship` still printing the
    detector as having run.

    So they are run, and asked the one question they answer: does this print
    the claim it says it prints, for a kind this repo registers.
    """

    FAMILY = sorted((ROOT / "detectors").glob("always_*.py"))

    def test_the_always_family_is_all_here(self):
        self.assertGreaterEqual(len(self.FAMILY), 9, self.FAMILY)

    def _run(self, path):
        with tempfile.TemporaryDirectory() as td:
            subj = Path(td) / "subject.json"
            subj.write_text(json.dumps({"repo_root": str(ROOT),
                                        "subject_refs": [], "params": {}}))
            r = subprocess.run(
                [sys.executable, str(path), "--subject", str(subj),
                 "--out", str(Path(td) / "out.json")],
                cwd=ROOT, capture_output=True, text=True)
        return r

    def test_each_one_raises_exactly_the_claim_it_declares(self):
        kinds = set(json.loads(
            (ROOT / ".v4" / "claim_kinds.json").read_text()))
        bad = []
        for path in self.FAMILY:
            r = self._run(path)
            claims = [l for l in r.stdout.splitlines()
                      if l.startswith("V4-CLAIM:")]
            if r.returncode != 0:
                bad.append(f"{path.name}: exit {r.returncode} {r.stderr[:120]}")
                continue
            if len(claims) != 1:
                bad.append(f"{path.name}: raised {len(claims)} claim(s), "
                           f"and one per task is the whole of what it does")
                continue
            kind = claims[0].split("kind=", 1)[1].split()[0]
            if kind not in kinds:
                bad.append(f"{path.name}: raises kind {kind!r}, which this "
                           f"repo does not register -- `derive` drops it")
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
