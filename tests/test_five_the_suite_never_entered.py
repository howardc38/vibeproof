"""Four gates the suite never entered, and one it entered a third of.

    python3 -m unittest tests.test_five_the_suite_never_entered -v

Each of these decides what may enter version control, and each was asserted by
nothing. Traced over all 106 test modules rather than grepped:

  2255437a  kernel/install.py::write_facts        never
  cfcfd7c6  kernel/register.py::register_detector never
  45cb3454  kernel/risk.py::closed_by_a_test      never
  dfb66458  kernel/accept.py::run                 entered -- see below

The last one is the reason this file says "traced, not grepped" twice. The
finding says no test enters `accept.run`, and that is measurably false:
`tests/test_the_half_that_was_silent.py:159` calls it. What that call passes is
`tests=True, fixtures=False, docs=False` -- one of three branches -- so the
three things the finding then names are all still true, and those are what is
asserted here. The overstated sentence is recorded rather than repeated.

Each finding has its own class and its own red step.
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

from kernel import accept as accept_mod  # noqa: E402
from kernel import install as install_mod  # noqa: E402
from kernel import ledger, register, review, risk  # noqa: E402


def _repo(case, name="somewhere") -> Path:
    """A git repo whose directory name is not its repo name.

    Deliberate: `write_facts` names its draft after the *repo*, and telling the
    two apart is half of what it was fixed for.
    """
    tmp = Path(tempfile.mkdtemp()) / name
    tmp.mkdir(parents=True)
    case.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    return tmp


class TheDraftThatMustNeverOverwrite(unittest.TestCase):
    """2255437a -- `install.write_facts`, the never-overwrite guarantee.

    "A table somebody pruned is exactly what this must never overwrite" is its
    own docstring, and nothing checked it. Neither did anything check the other
    half: the draft is named after the repo, not the checkout directory, a fix
    its docstring says was made in three call sites and missed in this fourth.
    """

    def test_a_draft_is_written_when_there_is_none(self):
        root = _repo(self)
        (root / "app.py").write_text("def handler():\n    return 1\n")
        path, n = install_mod.write_facts(root)
        self.assertIsNotNone(path, "an adopter with no table gets a draft")
        self.assertTrue(path.is_file())
        self.assertGreaterEqual(n, 0)
        json.loads(path.read_text())

    def test_a_table_somebody_pruned_is_left_alone(self):
        root = _repo(self)
        (root / ".v4").mkdir()
        mine = root / ".v4" / "facts.somewhere.json"
        mine.write_text('{"auth_decision": []}')
        before = mine.read_text()
        path, n = install_mod.write_facts(root)
        self.assertIsNone(path, "there is a table here already")
        self.assertEqual(n, 0)
        self.assertEqual(mine.read_text(), before)

    def test_a_draft_already_here_is_left_alone_too(self):
        """The other arm of the same guard: a draft is somebody's work too."""
        root = _repo(self)
        (root / ".v4").mkdir()
        draft = root / ".v4" / "facts.somewhere.json.draft"
        draft.write_text('{"mine": true}')
        path, _n = install_mod.write_facts(root)
        self.assertIsNone(path)
        self.assertEqual(json.loads(draft.read_text()), {"mine": True})

    def test_the_draft_is_named_for_the_repo_not_the_directory(self):
        """The repair its docstring says was missed here, measured in a worktree.

        The first version of this test asserted that a clone under a different
        directory name gets a draft named after the *remote*. That expectation
        was wrong as a requirement, and `layout.repo_name`'s own docstring says
        so: the git common dir's parent "is still a directory name; `git clone
        <url> my-name` and GitHub's Download ZIP ... both walk straight through
        it". A clone is genuinely a repo of that name as far as this project is
        concerned. The expectation was changed rather than the code, because
        the code is the owner here.

        What `repo_name` does answer is the worktree case, which is what it was
        written for: eight worktrees of one repo are one repo, and the name is
        the main checkout's. That is the difference `write_facts` was missing,
        so that is what this asserts.
        """
        main = _repo(self, name="the-real-name")
        (main / "app.py").write_text("def handler():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=main, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=main,
                       capture_output=True)
        wt = main.parent / "wt-d1"
        subprocess.run(["git", "worktree", "add", "-q", str(wt)],
                       cwd=main, capture_output=True)
        path, _n = install_mod.write_facts(wt)
        self.assertIsNotNone(path, "the worktree has no table of its own")
        self.assertEqual(path.name, "facts.the-real-name.json.draft")

    def test_a_declared_name_wins_over_both(self):
        """The control, and the rule `repo_name` actually states: a repo that
        has written a table has already said what it is called."""
        root = _repo(self, name="whatever-this-directory-is")
        (root / ".v4").mkdir()
        # The `repo` field, not the filename: `declared_repo_name` reads what
        # somebody wrote inside the table, because that is the half that does
        # not move when the tree is copied.
        (root / ".v4" / "facts.chosen.json").write_text(
            '{"repo": "chosen", "auth_decision": []}')
        from kernel import layout
        self.assertEqual(layout.repo_name(root), "chosen")
        path, _n = install_mod.write_facts(root)
        self.assertIsNone(path, "and that table is never overwritten")


class TheGateOnWhatMayJudgeYou(unittest.TestCase):
    """cfcfd7c6 -- `register.register_detector`, and the three things it does.

    `verify_detector`, its read-only twin, is covered; `register`, its checker
    counterpart, is covered. This one -- the half that *writes* -- was not, and
    `VerifyAndRegisterAskTheSameQuestion` exists in `test_kernel.py` because
    those two commands once disagreed.
    """

    DETECTOR = ("import argparse, json, sys\n"
                "from pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--subject'); p.add_argument('--facts')\n"
                "p.add_argument('--out')\n"
                "a = p.parse_args()\n"
                "s = json.loads(Path(a.subject).read_text())\n"
                "root = Path(s['repo_root'])\n"
                "if (root / 'trip.py').is_file():\n"
                "    print('V4-CLAIM: kind=k symbol=<module>')\n"
                "sys.exit(0)\n")

    def _repo_with_cases(self, red=3, green=3):
        root = _repo(self)
        fixtures = root / "fixtures"
        for colour, n in (("red", red), ("green", green)):
            for i in range(n):
                d = fixtures / colour / f"c{i}"
                (d / ".v4").mkdir(parents=True)
                (d / ".v4" / "config.json").write_text('{"test_command": "true"}')
                (d / ".v4" / "fixture.json").write_text(
                    json.dumps({"committed": ["mod.py"]}))
                (d / "mod.py").write_text("x = 1\n")
                if colour == "red":
                    (d / "trip.py").write_text("x = 1\n")
        det = root / "detectors" / "trip.py"
        det.parent.mkdir(parents=True)
        det.write_text(self.DETECTOR)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        return root, det, fixtures, conn

    def _events(self, conn):
        return [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'register_detector' "
            "ORDER BY id")]

    def test_a_detector_that_passes_enters_the_registry(self):
        root, det, fixtures, conn = self._repo_with_cases()
        reg = root / ".v4" / "detectors.json"
        ok, report = register.register_detector(
            conn, detectors_json=reg, detector_path=det,
            fixtures_dir=fixtures, repo_root=root)
        self.assertTrue(ok, report.get("failures"))
        entry = json.loads(reg.read_text())["trip.py"]
        self.assertEqual(entry["path"], "detectors/trip.py")
        self.assertEqual(entry["fixtures"], "fixtures")
        self.assertEqual(entry["cases"], report["total"])
        self.assertEqual(
            entry["sha256"],
            hashlib.sha256(det.read_bytes()).hexdigest())

    def test_and_leaves_the_event_that_makes_the_gate_requirable(self):
        """"Passing a gate has to leave a trace, or nothing can require it.\""""
        root, det, fixtures, conn = self._repo_with_cases()
        register.register_detector(
            conn, detectors_json=root / ".v4" / "detectors.json",
            detector_path=det, fixtures_dir=fixtures, repo_root=root)
        events = self._events(conn)
        self.assertEqual(len(events), 1, events)
        self.assertTrue(events[0]["accepted"])
        self.assertEqual(events[0]["detector"], "trip.py")

    def test_a_detector_that_fails_is_refused_and_still_recorded(self):
        """A green case that trips it: the gate says no, and says so on the
        record. The registry must not gain the entry."""
        root, det, fixtures, conn = self._repo_with_cases()
        (fixtures / "green" / "c0" / "trip.py").write_text("x = 1\n")
        reg = root / ".v4" / "detectors.json"
        ok, _report = register.register_detector(
            conn, detectors_json=reg, detector_path=det,
            fixtures_dir=fixtures, repo_root=root)
        self.assertFalse(ok)
        self.assertFalse(reg.is_file(), "a refused detector wrote itself in")
        events = self._events(conn)
        self.assertEqual([e["accepted"] for e in events], [False])

    def test_a_borrowed_fixture_set_is_refused_before_anything_runs(self):
        """The refusal that returns first, and therefore writes no event.

        It happened twice within ten minutes of the gate existing: registering
        a detector against its checker's fixtures passed silently, because the
        two ask different questions of the same files.
        """
        root, det, fixtures, conn = self._repo_with_cases()
        (root / ".v4").mkdir(exist_ok=True)
        (root / ".v4" / "checkers.json").write_text(json.dumps(
            {"k": {"path": "checkers/k.py", "fixtures": "fixtures",
                   "kinds": ["k"]}}))
        reg = root / ".v4" / "detectors.json"
        ok, report = register.register_detector(
            conn, detectors_json=reg, detector_path=det,
            fixtures_dir=fixtures, repo_root=root)
        self.assertFalse(ok)
        self.assertTrue(report["failures"])
        self.assertFalse(reg.is_file())
        self.assertEqual(self._events(conn), [],
                         "this refusal is before the gate, so there is no "
                         "gate result to record")


class TheExampleShownBeforeASignature(unittest.TestCase):
    """45cb3454 -- `risk.closed_by_a_test`, the comparison nobody exercised.

    It reads `closing_test` out of a `review_close` payload, and
    `review.bind_closing_test` is what writes that key. Renaming it on either
    side would leave `v4 risk waiting` silently no longer offering the example
    -- which is the whole of this function.
    """

    def setUp(self):
        self.root = _repo(self)
        (self.root / ".v4").mkdir()
        (self.root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding", "detector": None,
                                "staleness": "subject", "engagement": True,
                                "question_template": "is {file} closed"}}))
        (self.root / ".v4" / "checkers.json").write_text("{}")
        (self.root / "mod.py").write_text("def helper():\n    return 1\n")
        (self.root / "t.py").write_text("def test_it():\n    pass\n")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)
        from kernel import config
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)

    def _a_finding_closed_with_a_test(self):
        cid, _made, _sib = review.raise_finding(
            self.conn, self.cfg, task_id=None, file="mod.py", symbol="helper",
            note="somebody found this and somebody closed it")
        review.bind_closing_test(self.conn, claim_id=cid, test_path="t.py",
                                 command="python3 -m unittest t",
                                 parent_commit="HEAD", root=self.root)
        return cid

    def test_the_example_is_offered_with_the_test_that_closed_it(self):
        cid = self._a_finding_closed_with_a_test()
        got = risk.closed_by_a_test(self.conn, "review-finding")
        self.assertEqual([c for c, _w, _t in got], [cid])
        self.assertEqual(got[0][1], "mod.py::helper")
        self.assertEqual(got[0][2], "t.py",
                         "the key is `closing_test`, written by "
                         "`review.bind_closing_test` -- renaming either side "
                         "silently ends the comparison")

    def test_a_kind_nobody_closed_that_way_offers_nothing(self):
        """The control: this is an example, not a default."""
        self._a_finding_closed_with_a_test()
        self.assertEqual(risk.closed_by_a_test(self.conn, "test"), [])

    def test_the_newest_comes_first_and_the_limit_holds(self):
        """`v4 risk waiting` asks for one, and one is what a person reads."""
        first = self._a_finding_closed_with_a_test()
        (self.root / "other.py").write_text("def second():\n    return 2\n")
        cid, _made, _sib = review.raise_finding(
            self.conn, self.cfg, task_id=None, file="other.py",
            symbol="second", note="a second finding, closed later")
        review.bind_closing_test(self.conn, claim_id=cid, test_path="t.py",
                                 command="python3 -m unittest t",
                                 parent_commit="HEAD", root=self.root)
        got = risk.closed_by_a_test(self.conn, "review-finding")
        self.assertEqual([c for c, _w, _t in got], [cid])
        self.assertNotEqual(cid, first)
        self.assertEqual(
            len(risk.closed_by_a_test(self.conn, "review-finding", limit=2)), 2)


class TheTwoThirdsOfAcceptNobodyRan(unittest.TestCase):
    """dfb66458 -- the release gate's fixture and doc branches.

    Not "no test enters `accept.run`" -- one does, with `fixtures=False,
    docs=False`. What no test entered is the two branches that decide whether
    every checker and detector in the registry still passes its own fixtures,
    and whether the doc checkers answered; and nothing asserted the PYTHONPATH
    the children are given, which is what an adopter's checkers import `kernel`
    through.
    """

    def _repo(self, checker_exit=0, doc_exit=0):
        root = _repo(self)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text("{}")
        (root / "checkers").mkdir()
        # A checker whose `verify` outcome this test controls, and a fixture
        # directory `verify` will refuse or accept accordingly.
        (root / "checkers" / "c.py").write_text(
            "import argparse, sys\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); p.parse_args()\n"
            f"sys.exit({checker_exit})\n")
        (root / ".v4" / "checkers.json").write_text(json.dumps(
            {"c": {"path": "checkers/c.py", "fixtures": "fixtures/c",
                   "kinds": ["c"], "reads": ["**/*"], "sha256": "x",
                   "timeout_sec": 60}}))
        (root / ".v4" / "detectors.json").write_text("{}")
        return root

    def test_a_checker_that_cannot_be_verified_lands_in_the_bad_list(self):
        root = self._repo()
        rows = {step: (ok, detail) for step, ok, detail
                in accept_mod.run(root, tests=False, fixtures=True, docs=False)}
        self.assertIn("checkers", rows)
        ok, detail = rows["checkers"]
        self.assertFalse(ok, detail)
        self.assertIn("0/1 registrable", detail)

    def test_an_empty_registry_is_reported_as_such_rather_than_as_a_pass(self):
        """The control on the same row: `0/0` is what an empty registry says,
        and `not bad` is true of it -- so the count is what carries the fact."""
        root = self._repo()
        (root / ".v4" / "checkers.json").write_text("{}")
        rows = {step: (ok, detail) for step, ok, detail
                in accept_mod.run(root, tests=False, fixtures=True, docs=False)}
        self.assertEqual(rows["checkers"][1].split()[0], "0/0")
        self.assertEqual(rows["detectors"][1].split()[0], "0/0")

    def test_a_doc_checker_that_says_unsupported_is_not_a_failure(self):
        """Exit 4 is the checker saying it cannot answer here, which is an
        answer. Exit 1 is not, and the row has to tell them apart."""
        root = self._repo()
        reg = json.loads((root / ".v4" / "checkers.json").read_text())
        for cid, code in zip(sorted(accept_mod.DOC_CHECKERS), (4, 1)):
            (root / "checkers" / f"{cid}.py").write_text(
                "import argparse, sys\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--subject'); p.add_argument('--facts')\n"
                "p.add_argument('--out'); p.parse_args()\n"
                f"print('said something'); sys.exit({code})\n")
            reg[cid] = {"path": f"checkers/{cid}.py", "fixtures": "",
                        "kinds": [cid], "reads": ["**/*"], "sha256": "x",
                        "timeout_sec": 60}
        (root / ".v4" / "checkers.json").write_text(json.dumps(reg))
        rows = {step: ok for step, ok, _detail
                in accept_mod.run(root, tests=False, fixtures=False, docs=True)}
        by_code = dict(zip(sorted(accept_mod.DOC_CHECKERS), (4, 1)))
        for cid, code in by_code.items():
            self.assertEqual(rows[cid], code == 4, f"{cid} exited {code}")

    def test_a_doc_checker_this_repo_does_not_register_is_said_so(self):
        root = self._repo()
        rows = {step: (ok, detail) for step, ok, detail
                in accept_mod.run(root, tests=False, fixtures=False, docs=True)}
        for cid in accept_mod.DOC_CHECKERS:
            self.assertEqual(rows[cid], (True, "not registered in this repo"))

    def test_the_children_are_given_both_roots_on_the_pythonpath(self):
        """The line an adopter's whole `accept` run rests on.

        Every `checkers/*.py` imports `kernel`, and an adopter has no `kernel/`
        of its own. Measured when this was the adopter root alone: `accept
        --here --fixtures` printed 0/16 registrable in 0.4s because every
        checker died on import.

        Which half `accept.run` owns was measured rather than assumed, and the
        answer was not what the red step first predicted: removing
        `str(framework)` from this line changes nothing observable, because
        `runner.child_env` -- the declared owner of what a spawned program is
        entitled to -- already sets `PYTHONPATH` to the framework's own root.
        The half this function contributes is `str(root)`, the tree under test,
        which `child_env` cannot know about. So both are asserted and the red
        step removes the one this function owns.
        """
        root = self._repo()
        (root / "checkers" / "c.py").write_text(
            "import argparse, os, sys, json\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); p.parse_args()\n"
            "import kernel  # the import an adopter's checker makes\n"
            "print(json.dumps({'adopter': os.getcwd() in os.environ['PYTHONPATH'].split(os.pathsep), 'framework': os.path.dirname(kernel.__file__)[:-len('/kernel')] in os.environ['PYTHONPATH'].split(os.pathsep)}))\n"
            "sys.exit(0)\n")
        reg = json.loads((root / ".v4" / "checkers.json").read_text())
        for cid in accept_mod.DOC_CHECKERS:
            reg[cid] = {"path": "checkers/c.py", "fixtures": "",
                        "kinds": [cid], "reads": ["**/*"], "sha256": "x",
                        "timeout_sec": 60}
        (root / ".v4" / "checkers.json").write_text(json.dumps(reg))
        rows = {step: (ok, detail) for step, ok, detail
                in accept_mod.run(root, tests=False, fixtures=False, docs=True)}
        for cid in accept_mod.DOC_CHECKERS:
            ok, detail = rows[cid]
            self.assertTrue(ok, f"{cid}: {detail}")
            observed = json.loads(detail)
            self.assertTrue(observed["adopter"], "the tree under test")
            self.assertTrue(observed["framework"], "the imported framework judging it")


if __name__ == "__main__":
    unittest.main()
