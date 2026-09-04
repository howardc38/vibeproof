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


if __name__ == "__main__":
    unittest.main(verbosity=2)
