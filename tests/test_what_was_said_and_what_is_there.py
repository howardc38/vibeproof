"""Five sentences pointing at something that is not there, or no longer true.

    python3 -m unittest tests.test_what_was_said_and_what_is_there -v

  783a8eb8 this repo's own config restated two blocks of framework defaults
           verbatim -- the shape `CONFIG_TEMPLATE` refuses to write for an
           adopter, one level in
  a1b0dac4 `v4 facts` handed the reader a usage block naming `python -m
           kernel.facts`, the invocation `docs/USING.md` says not to use and
           the one `cmd_facts` quotes as the reason it exists
  bcace499 a docstring said its rules were "shared with the checker", and the
           checker was removed
  9cad9837 fifteen `dead_wiring` fixture cases, every one carrying a
           `kernel/ledger.py`, so half the checker was judged by nothing
  f1f8c849 "Twenty-six siblings already have this shape", copied into nine
           files, wrong under every reading

Each has its own class and its own mutation.
"""

from __future__ import annotations

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

from kernel import config as config_mod                         # noqa: E402
from kernel import facts as facts_mod                           # noqa: E402
from kernel import request_cover                                # noqa: E402
from kernel import sweep                                        # noqa: E402


class WhichCopyTheTemplateRefuses(unittest.TestCase):
    """783a8eb8 -- and the sentence it quotes, read at its source.

    The finding says this repo's config restates framework defaults verbatim
    and quotes `CONFIG_TEMPLATE`: "a copy here is a line an adopter can delete
    with no effect at all ... the write-a-thing-nobody-reads shape one level
    in". It names `thresholds`, `lens_sweep` and `protected_paths` together.

    Read at the template, that sentence is about `protected_paths` alone, and
    the same comment says why the other two are different **in the next
    clause**: "unlike `thresholds`, where deleting a key means 'use the
    default' and the file still says what it does". `scope.protected_for`
    unions `PROTECTED_DEFAULT` in on every read, so a copied glob cannot be
    removed and cannot do anything; a threshold can be edited and read back.

    So the repair was made and reverted. `.v4/config.json` carries `thresholds`
    and `lens_sweep` because the template ships them on purpose, and it does
    *not* restate `protected_paths` -- measured: its list differs from
    `PROTECTED_DEFAULT`, so it is this repo's own declaration. The rule the
    finding is about was already being followed.

    What is left is that nothing checked any of it, which is what these cases
    are.
    """

    def setUp(self):
        self.declared = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8"))

    def test_the_template_ships_the_two_that_can_be_edited(self):
        """`scaffold` is the owner: what a new adopter starts with."""
        from kernel import init as init_mod

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertTrue(init_mod.scaffold(tmp))
        fresh = json.loads(
            (tmp / ".v4" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(fresh["thresholds"], config_mod.DEFAULT_THRESHOLDS)
        self.assertEqual(fresh["lens_sweep"], sweep.DEFAULT)

    def test_and_refuses_the_one_that_cannot_be(self):
        """The distinction the quoted sentence is actually drawing."""
        from kernel import init as init_mod

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        init_mod.scaffold(tmp)
        fresh = json.loads(
            (tmp / ".v4" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(fresh["protected_paths"], [],
                         "a copied glob is one an adopter can delete with no "
                         "effect: `protected_for` unions the defaults in")

    def test_this_repo_does_not_restate_the_one_that_is_refused(self):
        from kernel.analysis import subject_files

        mine = self.declared.get("protected_paths") or []
        self.assertNotEqual(sorted(mine),
                            sorted(subject_files.PROTECTED_DEFAULT),
                            "these are this repo's own additions, not a copy")
        self.assertTrue(set(mine) - set(subject_files.PROTECTED_DEFAULT))

    def test_and_the_two_it_does_carry_are_editable_and_read(self):
        """Why the template ships them: deleting a key means "use the default",
        and editing one changes what this repo is judged by."""
        cfg = config_mod.RepoConfig(ROOT)
        self.assertEqual(cfg.thresholds["min_chars"],
                         self.declared["thresholds"]["min_chars"])
        self.assertEqual(
            config_mod.RepoConfig(ROOT).thresholds,
            {**config_mod.DEFAULT_THRESHOLDS, **self.declared["thresholds"]})


class TheUsageAReaderIsHanded(unittest.TestCase):
    """a1b0dac4 -- one entry surface's vocabulary reaching through another."""

    def _facts(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = facts_mod.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_the_usage_names_the_command_this_repo_documents(self):
        code, said = self._facts()
        self.assertEqual(code, 0)
        self.assertIn("v4 facts propose", said)

    def test_a_command_short_of_arguments_says_the_same(self):
        """The path measured: `v4 facts scan <one path>` reached the usage
        block through an IndexError, so the vocabulary that surfaced was the
        inner one."""
        code, said = self._facts("scan", str(ROOT / ".v4" / "facts.vibeproof.json"))
        self.assertEqual(code, 2)
        self.assertIn("v4 facts scan", said)

    def test_and_the_module_form_is_still_named(self):
        """Not withheld: the module is runnable that way and the tests use it.
        Both are named, in the order a reader should try them."""
        _code, said = self._facts()
        self.assertIn("python -m kernel.facts", said)
        self.assertLess(said.index("v4 facts propose"),
                        said.index("python -m kernel.facts"))


class ADocstringAboutACheckerThatWasRemoved(unittest.TestCase):
    """bcace499 -- and the repo agreeing with itself about it."""

    def test_no_program_answers_for_request_coverage(self):
        self.assertEqual(list((ROOT / "checkers").glob("*request*")), [])
        kinds = json.loads(
            (ROOT / ".v4" / "claim_kinds.json").read_text(encoding="utf-8"))
        self.assertNotIn("request-coverage", kinds)

    def test_the_rule_is_still_shared_by_the_two_readers_that_remain(self):
        """What the repair kept: one function answers "why does this entry
        account for nothing", and both surviving readers ask it.

        Asked by running them rather than by reading them. The first version
        used `inspect.getsource` and `test-shape` refused it, correctly: a test
        that reads source text passes whatever the code does, and what is being
        claimed here is that a bad entry is refused, not that a name appears.
        `record` needs a ledger, so it is asked through the refusal it raises
        before touching one.
        """
        request = "a request with words in it"
        bad = {"quote": "", "symbol": None, "test": None, "not_done": False,
               "why": "", "acceptance": ""}

        # `measure`: an entry that does not survive `fault` counts for nothing
        # and is reported.
        m = request_cover.measure(request, [bad])
        self.assertEqual(m["ratio"], 0)
        self.assertTrue(m.get("bad") or m.get("faults") or m.get("refused"), m)

        # `record`: the same rule, before anything reaches the ledger.
        with self.assertRaises(Exception) as caught:
            request_cover.record(None, task_id="t", request=request, quote="")
        self.assertIn("accounts for nothing", str(caught.exception))

    def test_and_it_answers(self):
        """Run, not read: an entry with no quote accounts for nothing."""
        why = request_cover.fault("a request with words in it", {"quote": ""})
        self.assertIn("accounts for nothing", why)
        self.assertIsNone(request_cover.fault(
            "a request with words in it",
            {"quote": "a request with words", "symbol": "somewhere",
             "test": "tests/t.py", "acceptance": "a" * 60}))


class TheHalfOfDeadWiringNobodyJudged(unittest.TestCase):
    """9cad9837 -- every case was the framework, so the adopter branch idled."""

    FX = ROOT / "tests" / "fixtures" / "dead_wiring"

    def _cases(self):
        return [d for colour in ("red", "green", "bypass")
                if (self.FX / colour).is_dir()
                for d in sorted((self.FX / colour).iterdir()) if d.is_dir()]

    def test_some_cases_are_not_this_framework(self):
        adopter = [d for d in self._cases()
                   if not (d / "kernel" / "ledger.py").is_file()]
        self.assertGreaterEqual(len(adopter), 2, [d.name for d in self._cases()])

    def test_and_they_declare_where_their_data_layer_lives(self):
        """The adopter branch is reached through `dal_globs`; a case without one
        would take it and find nothing, which is not the same as being judged."""
        for d in self._cases():
            if (d / "kernel" / "ledger.py").is_file():
                continue
            tables = list((d / ".v4").glob("facts*.json"))
            self.assertTrue(tables, d.name)
            facts = json.loads(tables[0].read_text(encoding="utf-8"))
            self.assertTrue(facts.get("dal_globs"), d.name)

    def test_the_checker_tells_them_apart(self):
        """Both new cases, through the checker, red and green."""
        want = {"red": 1, "green": 0}
        for colour, expect in want.items():
            case = next(d for d in (self.FX / colour).iterdir()
                        if d.is_dir()
                        and not (d / "kernel" / "ledger.py").is_file())
            with self.subTest(case=case.name):
                self.assertEqual(self._run(case), expect, case.name)

    def _run(self, case):
        import os

        with tempfile.TemporaryDirectory() as td:
            subj = Path(td) / "subject.json"
            subj.write_text(json.dumps({"repo_root": str(case),
                                        "subject_refs": [], "params": {}}))
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT) + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            return subprocess.run(
                [sys.executable, str(ROOT / "checkers/dead_wiring.py"),
                 "--subject", str(subj)],
                cwd=case, env=env, capture_output=True, text=True).returncode

    def test_sources_takes_the_other_branch_for_them(self):
        """In-process, so the branch itself is observed rather than its verdict."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "dead_wiring_under_test", ROOT / "checkers/dead_wiring.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        case = next(d for d in (self.FX / "green").iterdir()
                    if d.is_dir() and not (d / "kernel" / "ledger.py").is_file())
        dal, rest = mod.sources(case)
        self.assertTrue(dal, "the declared data layer")
        self.assertTrue(any(p.name == "store.py" for p in dal))
        self.assertTrue(any(p.name == "views.py" for p in rest))


class ACountCopiedIntoNinePlaces(unittest.TestCase):
    """f1f8c849 -- a number that had to track a population, hardcoded."""

    FAMILY = sorted((ROOT / "detectors").glob("always_*.py"))

    def test_the_family_is_here(self):
        self.assertGreaterEqual(len(self.FAMILY), 9)

    def test_none_of_them_carries_the_count(self):
        said = [p.name for p in self.FAMILY
                if "Twenty-six siblings" in p.read_text(encoding="utf-8")]
        self.assertEqual(said, [])

    def test_the_detector_still_raises_what_it_always_did(self):
        """Nine docstrings changed and no behaviour did -- said by running one.

        Also the only case here with `main` on the stack, which is the symbol
        this finding names.
        """
        import contextlib
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "always_budget_under_test", ROOT / "detectors" / "always_budget.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subj = tmp / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(ROOT), "subject_refs": [],
                                    "params": {}}))
        argv = sys.argv
        sys.argv = ["always_budget.py", "--subject", str(subj)]
        self.addCleanup(setattr, sys, "argv", argv)
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = mod.main()

        self.assertEqual(code, 0)
        claims = [l for l in said.getvalue().splitlines()
                  if l.startswith("V4-CLAIM:")]
        self.assertEqual(len(claims), 1, said.getvalue())
        self.assertIn("kind=control-plane-budget", claims[0])

    def test_and_no_stale_number_replaced_it(self):
        """The repair is the absence of a count, not a fresher one: nothing in
        this repo reads a docstring, so a new number would go stale the same
        way. Measured when the finding was raised: 42 files under `checkers/`
        and `detectors/` define `main`, against a docstring saying twenty-six.
        """
        import re

        defining = sum(
            1 for d in ("checkers", "detectors")
            for p in sorted((ROOT / d).glob("*.py"))
            if "\ndef main(" in p.read_text(encoding="utf-8"))
        self.assertGreater(defining, 26)
        for p in self.FAMILY:
            head = p.read_text(encoding="utf-8").split('"""')[1:2]
            for chunk in head:
                self.assertIsNone(
                    re.search(r"\b(twenty|thirty|forty|\d+)\s+siblings",
                              chunk, re.I), p.name)


if __name__ == "__main__":
    unittest.main()
