"""Repairs in `checkers/scope.py`, `checkers/facts_coverage.py`,
`checkers/request_coverage.py`, `checkers/review_finding.py`,
`checkers/bundle_secret.py` and `kernel/review.py`.

    python3 -m unittest tests.test_the_escape_a_gate_prints -v

A gate that prints a way out has to print one the CLI takes; a checker that
could not read the diff is not a pass; and one config value has one floor.

All of them fail against 0ad6b61.
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

from kernel import review, risk  # noqa: E402

import review_finding  # noqa: E402


def _repo(case, files=None, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    for rel, text in (files or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "in"], cwd=tmp, capture_output=True)
    return tmp


def _run(checker, root, subject_extra=None):
    """Run one checker the way the kernel does, in this process.

    `runpy`, not `subprocess`: a checker is a script, and running it here is
    what lets a test say the checker's own functions ran rather than that a
    child process printed something.
    """
    import contextlib
    import io
    import runpy
    subj = root / "subject.json"
    body = {"repo_root": str(root)}
    body.update(subject_extra or {})
    subj.write_text(json.dumps(body))
    argv = sys.argv
    sys.argv = [checker, "--subject", str(subj)]
    said, code = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            try:
                runpy.run_path(str(ROOT / "checkers" / checker),
                               run_name="__main__")
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = argv
    return code, said.getvalue()


class ADiffNobodyCouldReadIsNotAPass(unittest.TestCase):
    """`git()` returned `[]` on any non-zero exit, and empty meant "no changes".

    `git diff --name-only <unresolvable>` exits 128 with empty stdout, so a
    rewritten history, a shallow clone or a missing base made the one program
    that issues the protected-path verdict print "no changes" and exit 0.
    """

    def test_an_unresolvable_base_cannot_verify(self):
        root = _repo(self, {"a.py": "x = 1\n"})
        code, said = _run("scope.py", root, {
            "diff_base": "0000000000000000000000000000000000000000",
            "scope_globs": ["**/*.py"]})
        self.assertEqual(code, 4, said)
        self.assertIn("CANNOT VERIFY", said)

    def test_a_readable_one_still_answers(self):
        root = _repo(self, {"a.py": "x = 1\n"})
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        code, said = _run("scope.py", root,
                          {"diff_base": base, "scope_globs": ["**/*.py"]})
        self.assertEqual(code, 0, said)


class OneMatcherForWhatIsInScope(unittest.TestCase):
    """A fourth glob matcher, disagreeing with the one that owns the rule.

    It spelled the wildcard `g.replace("**", "*")` where `subject_files` spells
    it `g.replace("/**", "/*")` plus a leading `**/` case, so for a task scoped
    `["**/*.py"]` a root-level `x.py` was out of scope to the program whose
    answer counts and in scope to everything else.
    """

    def test_a_root_level_file_is_inside_a_recursive_glob(self):
        root = _repo(self, {"x.py": "x = 1\n"})
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        (root / "x.py").write_text("x = 2\n")
        code, said = _run("scope.py", root,
                          {"diff_base": base, "scope_globs": ["**/*.py"]})
        self.assertEqual(code, 0, said)
        self.assertNotIn("outside", said)


class TheWayOutHasToBeOneTheCliTakes(unittest.TestCase):
    """`--kind facts-coverage` exits 2 with "invalid choice".

    `risk.KINDS` is four fixed values and `cli` declares `--kind` with
    `choices=list(risk.KINDS)`, so a worker who has just been told a call is
    undeclared was handed two ways out and the second one did not exist.
    """

    def test_every_kind_this_checker_prints_is_one_the_cli_declares(self):
        root = _repo(self, {"app.py": "import requests\n\n\ndef go(u):\n"
                                      "    requests.post(u)\n"})
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(
            {"repo": root.name, "outbound_write": [], "absent": {}}))
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        (root / "app.py").write_text("import requests\n\n\ndef go(u):\n"
                                     "    requests.post(u)\n    requests.put(u)\n")
        code, said = _run("scope.py", root, {"diff_base": base})
        import re
        printed = re.findall(r"--kind[\s'\"]+([A-Za-z_][\w-]*)", said)
        self.assertTrue(printed, said)
        for kind in printed:
            self.assertIn(kind, risk.KINDS, said)


class OneConfigValueOneFloor(unittest.TestCase):
    """`measure()` re-ran the fault check against the code default.

    `cmd_cover` records an entry with `min_chars` from this repo's thresholds
    and the checker graded the same field against 40, so a repo that lowers
    the floor has entries accepted at write time and discarded at read time.
    """

    def test_the_checker_grades_by_the_repos_own_floor(self):
        from kernel import request_cover
        request = "make the gate report what it measured"
        # A piece somebody decided not to do, with a short reason: allowed
        # under a floor of 10 and refused under 40, which is the whole point.
        entry = {"quote": "what it measured", "not_done": True,
                 "why": "the probe is next"}
        self.assertTrue(request_cover.fault(request, entry, min_chars=40))
        self.assertIsNone(request_cover.fault(request, entry, min_chars=10))
        got = request_cover.measure(request, [entry], min_chars=10)
        self.assertEqual(got["faults"], [],
                         "accepted at write time, discarded at read time")

    # The second half of this recorded an entry the way `v4 cover` does -- under
    # this repo's own floor of ten -- and then ran `request_coverage.py` to prove
    # the checker graded it by the same number rather than the code default of
    # forty. That kind was cut: 404 runs, 78 FAIL->PASS transitions, and every
    # one of them a citation reformat, on a checker that never opened a source
    # file. Its symbol half -- 346 refusals of a name no file defines -- moves
    # into `v4 cover` as input validation.
    #
    # The arithmetic it pinned is `kernel/request_cover.py`, which is still here
    # and still drives both sides above: `fault()` refuses at forty and accepts
    # at ten, and `measure()` agrees with the write path. What is gone is a
    # second reader of the same field, which is what made disagreement possible.


class WhatATextClosureRefuses(unittest.TestCase):
    """Never executed by the suite: `return 0` as its first statement left
    every test green.

    Nothing asserted its refusals -- a marker absent at the parent, one already
    present at the parent, one shorter than the floor, or the one it was
    written for: text still present in another tracked file, which is a finding
    repaired in one place only.
    """

    def _repo_with_history(self, before, after=None):
        root = _repo(self, before)
        parent = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
        for rel, text in (after or {}).items():
            (root / rel).write_text(text)
        return root, parent

    def test_a_marker_that_was_never_there_cannot_have_been_removed(self):
        root, parent = self._repo_with_history({"a.py": "x = 1\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "a sentence that was never in this file", "",
            subject={}), 1)

    def test_a_marker_shorter_than_the_floor_proves_nothing(self):
        root, parent = self._repo_with_history({"a.py": "x = 1\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "", "short", subject={}), 1)

    def test_a_marker_already_there_at_the_parent_was_not_put_there_by_this(self):
        root, parent = self._repo_with_history(
            {"a.py": "# a sentence that was already written here\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "",
            "a sentence that was already written here", subject={}), 1)

    def test_text_still_present_in_another_file_is_repaired_in_one_place_only(self):
        root, parent = self._repo_with_history(
            {"a.py": "# the sentence that was wrong everywhere\n",
             "b.py": "# the sentence that was wrong everywhere\n"},
            {"a.py": "# repaired here and nowhere else\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "the sentence that was wrong everywhere", "",
            subject={}), 1)

    def test_a_note_quoting_the_wrong_sentence_is_not_a_place_it_lives(self):
        """`.v4/ledger_export.jsonl` carries every finding's note verbatim and
        is committed, so a finding *about* a wrong sentence puts that sentence
        into a tracked file forever -- and the sweep read that as "repaired in
        one place only" for every marker anybody offers."""
        root, parent = self._repo_with_history(
            {"a.py": "# the sentence that was wrong everywhere\n",
             ".v4/ledger_export.jsonl": json.dumps(
                 {"note": "a.py says 'the sentence that was wrong everywhere'"})
             + "\n"},
            {"a.py": "# repaired, and the record still quotes the old one\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "the sentence that was wrong everywhere", "",
            subject={}), 0)

    def test_and_a_repair_in_every_place_it_appears_passes(self):
        root, parent = self._repo_with_history(
            {"a.py": "# the sentence that was wrong everywhere\n",
             "b.py": "# the sentence that was wrong everywhere\n"},
            {"a.py": "# repaired here and in b.py as well\n",
             "b.py": "# repaired here and in b.py as well\n"})
        self.assertEqual(review_finding._text_closure(
            root, "a.py", parent, "the sentence that was wrong everywhere",
            "repaired here and in b.py as well", subject={}), 0)


class ADeclarationOfAbsenceIsNotAMissingTable(unittest.TestCase):
    """`facts.get("ui_globs") or [four guesses]`.

    A repo declaring `"ui_globs": []` -- a statement that it has no client
    surface, which `MAY_BE_EMPTY` exists to make sayable -- was treated as a
    repo with no table at all. Its own detector branches on `in facts` for
    exactly this reason, with a different fallback list.
    """

    def test_an_empty_declaration_is_answered_not_guessed_past(self):
        from kernel.analysis import facts_grammar
        self.assertEqual(facts_grammar.ui_globs({"ui_globs": []}), [])
        self.assertTrue(facts_grammar.ui_globs({}))

    # The second half of this ran `bundle_secret.py` against a repo that
    # declared `"ui_globs": []`, to prove the checker read the declaration
    # rather than four guessed globs. That kind was cut -- 665 runs, 0 claims,
    # because `kernel/facts.py` writes `ui_globs` as a hardcoded `[]` and no
    # repo ever filled it -- and nothing registered reads `ui_globs` now.
    #
    # What it pinned survives above: `facts_grammar.ui_globs({"ui_globs": []})`
    # returns `[]` and `ui_globs({})` returns the fallback. That is the
    # distinction, reached directly instead of through a checker.


class EveryWayOfSpellingDoNothing(unittest.TestCase):
    """A four-item list of no-op commands, and four ways around it.

    `/usr/bin/true`, `bash -c true`, `sh -c :` and `python3 -c pass` all walked
    past `("true", ":", "exit 0", "/bin/true")` into a permanently green
    surface claim. SPEC.md §10 records the same shape being reversed in
    `control-plane-budget`: "all whitelists end up dying this way".
    """

    def _surface(self, cmd):
        root = _repo(self, {"a.py": "x = 1\n"}, surface_command=cmd,
                     surface_cwd=".")
        return _run("surface_proof.py", root)

    def test_a_command_that_ran_nothing_is_refused_however_it_is_spelled(self):
        for cmd in ("true", "/usr/bin/true", "bash -c true", "sh -c :",
                    "python3 -c pass"):
            with self.subTest(cmd=cmd):
                code, said = self._surface(cmd)
                self.assertEqual(code, 1, said)
                self.assertIn("execution receipt", said)

    def test_printing_test_counts_is_still_not_execution_evidence(self):
        code, said = self._surface("echo 3 specs, 3 passed")
        self.assertEqual(code, 1, said)
        self.assertIn("execution receipt", said)


class ALensBriefWithNothingInItsLastBullet(unittest.TestCase):
    """The last `anti_patterns` entry was `""`, rendered as a bare `-`.

    `unusable()` checked that the keys were present and `checks` non-empty,
    and said nothing about a blank entry that `lens_brief` prints verbatim
    under "Shapes worth flagging on sight".
    """

    def test_a_blank_anti_pattern_makes_a_lens_unusable(self):
        why = review.unusable({"name": "x", "source": "s",
                               "checks": [{"id": "a", "ask": "does it?"}],
                               "anti_patterns": ["a real one", "  "]})
        self.assertTrue(why)

    def test_and_this_repos_own_lenses_are_all_usable(self):
        good, bad = review.lens_files(ROOT)
        self.assertEqual(bad, {})
        self.assertGreater(len(good), 1)

    def test_no_brief_this_repo_hands_out_ends_in_an_empty_bullet(self):
        good, _bad = review.lens_files(ROOT)
        for slug, lens in good.items():
            text = review.lens_brief(lens, slug)
            for line in text.splitlines():
                self.assertNotEqual(line.strip(), "-", (slug, text[-200:]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
