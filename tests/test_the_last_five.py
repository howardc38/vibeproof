"""Records that described a tree, a schedule or a call graph that had moved.

    python3 -m unittest tests.test_the_last_five -v

  192c83a3  a facts note counted three call sites of `cfg.protected` and said
            one of them denies. Two do -- `bash_guard` and `write_block` -- and
            it cited two line numbers that had moved, in a note `facts verify`
            never reads.
  d1daacce  `kernel_sha` pinned six kernel files at `frozen_at` and nothing read
            it; all six values had moved.
  7c72c51c  the sweep reminder polled weekly for a four-day interval, so the one
            automated trigger the lens layer has could stay silent through a
            whole overdue window.
  37f902d5  one assertion in `test_what_guards_the_guards.py` pinned a wording
            and nothing else.
  6c01d02e  already repaired -- see below.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import test_shape                          # noqa: E402


class TheNoteThatCountedTheWrongThing(unittest.TestCase):
    """192c83a3 -- and the two call sites that actually deny."""

    def setUp(self):
        self.facts = json.loads(
            (ROOT / ".v4" / "facts.vibeproof.json").read_text(encoding="utf-8"))
        self.row = next(r for r in self.facts["auth_decision"]
                        if r["pattern"] == ".protected")

    def test_two_call_sites_deny_and_the_note_says_two(self):
        self.assertIn("兩個", self.row["note"])
        self.assertIn("write_block", self.row["note"])

    def test_and_both_of_them_are_here(self):
        """The claim the note makes, run rather than read.

        The first version looked for the words `protected` and `deny` in the
        two hook files, which a comment would have satisfied. Each half is
        callable, so each is called: `write_block.is_protected` is the second
        denying site, and the decision `bash_guard` reaches is
        `shell_command.writes_to_protected`.
        """
        import importlib.util

        from kernel.analysis.shell_command import writes_to_protected

        spec = importlib.util.spec_from_file_location(
            "write_block_under_test", ROOT / "hooks" / "write_block.py")
        wb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wb)
        self.assertTrue(wb.is_protected(".v4/config.json", [".v4/**"]))
        self.assertFalse(wb.is_protected("app.py", [".v4/**"]))

        self.assertTrue(writes_to_protected("echo x > .v4/config.json",
                                            [".v4/**"]))
        self.assertFalse(writes_to_protected("cat .v4/config.json",
                                             [".v4/**"]))

    def test_the_note_cites_no_line_numbers(self):
        """`facts verify` re-checks `seen_at` and reads nothing inside a note,
        so a `file:line` written there goes stale with nothing to say so. Module
        names move loudly; line numbers do not."""
        self.assertIsNone(re.search(r"\.py:\d+", self.row["note"]),
                          self.row["note"])

    def test_and_seen_at_still_points_at_something(self):
        """The half that *is* re-checked, so this case is not asserting the
        absence of the only thing anybody verifies."""
        rel, _, line = self.row["seen_at"].partition(":")
        self.assertTrue((ROOT / rel).is_file(), rel)
        self.assertTrue(line.isdigit(), self.row["seen_at"])


class ARecordThatReadAsAPin(unittest.TestCase):
    """d1daacce -- six shas of a tree that no longer exists."""

    def setUp(self):
        self.acc = json.loads(
            (ROOT / ".v4" / "acceptance.json").read_text(encoding="utf-8"))

    def test_the_key_says_it_is_a_record(self):
        self.assertNotIn("kernel_sha", self.acc)
        self.assertIn("kernel_sha_when_frozen", self.acc)
        self.assertIn("not a pin", self.acc["kernel_sha_when_frozen"]["note"])

    def test_the_shas_are_kept(self):
        """Renamed, not deleted: with `frozen_at` they say which kernel the
        criteria below were measured on, and nothing else carries that."""
        shas = self.acc["kernel_sha_when_frozen"]["shas"]
        self.assertEqual(len(shas), 6)
        self.assertIn("ledger.py", shas)
        self.assertTrue(self.acc.get("frozen_at"))

    def test_nothing_reads_it_and_the_note_says_so(self):
        hits = subprocess.run(["git", "grep", "-l", "kernel_sha"], cwd=ROOT,
                              capture_output=True, text=True).stdout.split()
        # A record *of* a decision is not a reader of the thing it decided
        # about. The ledger export carries every note verbatim, a signature
        # record carries the `why` somebody wrote, and this repo's own
        # `checkers/review_finding.py` draws the same line for the same reason
        # ("a record of the fact is not one of those places"). Found by this
        # cut's own signature quoting the key name in its reasoning.
        records = (".v4/ledger_export.jsonl", ".v4/risks/", ".v4/deferred/")
        hits = [h for h in hits
                if not h.startswith(records) and not h.startswith("tests/")]
        self.assertEqual(hits, [".v4/acceptance.json"], hits)

    def test_what_does_cover_this_file(self):
        """The mechanism that takes over, made to fire.

        Naming it is not checking it: `register.assert_ruler_unmoved` compares
        the whole file's sha against the round that is open, so a round is
        opened, the file is edited, and it refuses. The first version read its
        source for the string `_acceptance_sha`.
        """
        import shutil
        import subprocess
        import tempfile

        from kernel import ledger, register

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        acc = tmp / ".v4" / "acceptance.json"
        acc.write_text(json.dumps({"criteria": ["a"], "frozen_at": "2026"}))
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)

        register.open_round(conn, acc, "r")
        self.assertIsNotNone(register.assert_ruler_unmoved(conn, acc))

        acc.write_text(json.dumps({"criteria": ["a", "b"],
                                   "frozen_at": "2026"}))
        with self.assertRaises(register.RulerMoved):
            register.assert_ruler_unmoved(conn, acc)


class APollLongerThanTheIntervalItPolls(unittest.TestCase):
    """7c72c51c -- the one automated trigger the lens layer has."""

    def setUp(self):
        self.wf = (ROOT / ".github" / "workflows" / "v4.yml").read_text(
            encoding="utf-8")
        self.cfg = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8"))

    def _cron_days(self):
        m = re.search(r'cron:\s*"([^"]+)"', self.wf)
        self.assertIsNotNone(m, "no cron in the workflow")
        fields = m.group(1).split()
        self.assertEqual(len(fields), 5, m.group(1))
        _minute, _hour, dom, _mon, dow = fields
        if dom == "*" and dow == "*":
            return 1
        if dow != "*" and "," not in dow:
            return 7
        return 7

    def test_the_poll_matches_the_history_available_in_this_distribution(self):
        from kernel import sweep

        if (ROOT / ".public-release.json").is_file():
            # A public snapshot intentionally carries no private sweep export.
            # Scheduling its reader here would invent an operational history.
            self.assertFalse((ROOT / ".v4/ledger_export.jsonl").exists())
            self.assertNotIn("due_from_export", self.wf)
            self.assertNotRegex(self.wf, r"(?m)^\s*-\s*cron:")
            return
        every = (self.cfg.get("lens_sweep") or sweep.DEFAULT)["every_days"]
        self.assertLessEqual(self._cron_days(), every,
                             "a poll longer than the interval can stay silent "
                             "through a whole overdue window")

    def test_the_interval_is_the_one_the_repo_declares(self):
        """The control: reading a default and calling it the repo's answer
        would make the case above pass on any cron."""
        from kernel import sweep

        every = (self.cfg.get("lens_sweep") or sweep.DEFAULT)["every_days"]
        self.assertEqual(every, 4)


class TheAssertionThatPinnedAWording(unittest.TestCase):
    """37f902d5 -- what is left in that file, and why."""

    PATH = ROOT / "tests" / "test_what_guards_the_guards.py"

    def test_only_the_accepted_source_read_remains(self):
        src = self.PATH.read_text(encoding="utf-8")
        found = test_shape.source_assertions(ast.parse(src), src)
        self.assertEqual(len(found), 1, found)

    def test_and_it_is_the_one_the_baseline_accepts(self):
        baseline = json.loads(
            (ROOT / ".v4" / "test-shape_baseline.json").read_text(
                encoding="utf-8"))
        whys = " ".join(a["why"] if isinstance(a, dict) else a
                        for a in baseline["accepted"])
        self.assertIn("test_the_size_is_asked_before_an_unbounded_write", whys)

    def test_the_decision_it_pinned_is_still_enforced(self):
        """What goes with a deleted assertion has to be named. The decision --
        that an opt-out stays out of the environment class -- is run by the two
        cases above where the wording test used to be."""
        import tests.run_without_silent_skips as oracle

        for reason in ("V4_ADOPTER_REPO names no checkout",
                       "no facts table can be identified in a checkout named x",
                       "commit a9ae5fb not in adopter_a any more"):
            self.assertIsNone(oracle.ENVIRONMENT.search(reason), reason)
        self.assertIsNotNone(oracle.ENVIRONMENT.search("go is not installed"))


class TheCiStepThatDerivedTheTableName(unittest.TestCase):
    """6c01d02e -- already repaired, and this is what it looks like repaired.

    The finding says the step derives the table name from `Path.cwd().name`, so
    a checkout named after the GitHub repository rather than the table exits 1
    -- fourteen consecutive push runs, with the two `v4 accept` gates below it
    never running once. It calls `config.facts_path_for` now, which asks
    `layout.declared_repo_name` first.
    """

    def test_the_step_asks_the_owner(self):
        """What the step runs, not what it explains.

        The comment above that step quotes `Path.cwd().name` while recording
        why it stopped using it -- so asking the whole file whether the string
        appears is asking about prose. `doctor._run_lines` strips comments and
        keeps what would run, and it exists because a `doctor` row flipped to
        green on a comment that quoted the command it was looking for.
        """
        from kernel import doctor

        wf = (ROOT / ".github" / "workflows" / "v4.yml").read_text(
            encoding="utf-8")
        runs = doctor._run_lines(wf)
        self.assertIn("from kernel.config import facts_path_for", runs)
        self.assertNotIn("Path.cwd().name", runs)

    def test_and_the_owner_answers_for_this_repo(self):
        """Run, not read: whatever the checkout is called, the table this repo
        declares is the one that comes back."""
        from kernel.config import facts_path_for

        table = facts_path_for(ROOT)
        self.assertIsNotNone(table)
        self.assertEqual(table.name, "facts.vibeproof.json")


if __name__ == "__main__":
    unittest.main()
