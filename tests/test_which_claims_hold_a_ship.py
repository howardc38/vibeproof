"""Gate tier: what defers, what does not, and what stops deferring.

    python3 -m unittest tests.test_which_claims_hold_a_ship -v

The axis is not importance. A kind blocks when deferring it costs more later --
a credential already in history, a charge already sent, a revert instead of an
edit -- or when something goes on running against the unanswered question. Every
other kind reports: unanswered, listed, and not holding the task.

The case that matters most here is the default. A kind whose registry entry
says nothing about `gate` blocks, because the failure this could introduce is
one-directional: a gate that quietly stopped gating is one nobody installed and
nothing says so, while a kind that blocks when it need not is visible on the
first task somebody runs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger, state   # noqa: E402

OPEN = "OPEN"


def a_repo(test):
    root = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, root, True)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
    conn = ledger.connect(root)
    ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                  base_commit="", created_at="2026-08-01T00:00:00+00:00")
    return root, conn


def a_claim(conn, cid, kind, *, created_at="2026-08-01T00:00:00+00:00",
            fails=0):
    """A claim, optionally having failed `fails` times **in distinct states**.

    Distinct on purpose. This helper first wrote every attempt with the same
    subject and worktree, which is a claim re-checked rather than one the work
    re-opened -- so the escalation test that used it was passing on the bug it
    was meant to guard, and went red the moment the count was corrected. A test
    fixture that cannot tell the two apart cannot test the rule that separates
    them.
    """
    ledger.insert(conn, "claim", id=cid, task_id="t", kind=kind, question="q",
                  subject_refs="[]", checker=kind, origin="derive",
                  created_at=created_at)
    for i in range(fails):
        ledger.append_attempt(conn, claim_id=cid, exit_code=1, stdout="",
                              stderr="", argv="[]", duration_ms=1,
                              subject_digest=f"s{i}", checker_sha="k",
                              config_sha="c", worktree=f"/w{i}",
                              head_commit="d", started_at="n", ended_at="n")
    return conn.execute("SELECT * FROM claim WHERE id = ?", (cid,)).fetchone()


class TheDefaultIsToBlock(unittest.TestCase):
    """A registry that says nothing must not quietly let go."""

    def test_a_kind_with_no_gate_field_blocks(self):
        self.assertEqual(state.gate_for({"whatever": {}}, "whatever"),
                         state.GATE_SHIP)

    def test_a_kind_not_in_the_registry_at_all_blocks(self):
        self.assertEqual(state.gate_for({}, "never-heard-of-it"),
                         state.GATE_SHIP)

    def test_only_the_exact_word_defers(self):
        for value in ("Report", "REPORT", "reports", "", None, True):
            with self.subTest(value=value):
                self.assertEqual(
                    state.gate_for({"k": {"gate": value}}, "k"),
                    state.GATE_SHIP,
                    "anything but the exact word has to keep blocking")
        self.assertEqual(state.gate_for({"k": {"gate": "report"}}, "k"),
                         state.GATE_REPORT)


class AReportedClaimIsUnansweredAndNotHolding(unittest.TestCase):

    CFG = {"a": {"gate": "report", "gate_why": "推遲成本唔變"},
           "b": {"gate": "ship", "gate_why": "推遲成本會變大"}}

    def test_it_is_out_of_blocked_and_into_reported(self):
        _root, conn = a_repo(self)
        ra = a_claim(conn, "c-a", "a")
        rb = a_claim(conn, "c-b", "b")
        blocked, reported = state.split_open(
            conn, [(ra, OPEN), (rb, OPEN)], self.CFG, {})
        self.assertEqual([r["id"] for r, _ in blocked], ["c-b"])
        self.assertEqual([r["id"] for r, _, _ in reported], ["c-a"])

    def test_it_carries_the_reason_its_kind_defers(self):
        """The list is the point. A claim that stopped blocking and stopped
        being mentioned was deleted, not deferred."""
        _root, conn = a_repo(self)
        ra = a_claim(conn, "c-a", "a")
        _blocked, reported = state.split_open(conn, [(ra, OPEN)], self.CFG, {})
        self.assertEqual(reported[0][2], "推遲成本唔變")

    def test_an_answered_claim_is_in_neither(self):
        _root, conn = a_repo(self)
        ra = a_claim(conn, "c-a", "a")
        blocked, reported = state.split_open(
            conn, [(ra, state.ANSWERED)], self.CFG, {})
        self.assertEqual((blocked, reported), ([], []))


class DeferralStopsAtThreeLines(unittest.TestCase):
    """可以唔理，唔可以永遠唔理 -- and it rots three different ways."""

    CFG = {"a": {"gate": "report", "gate_why": "w"}}

    def test_a_pile_bigger_than_the_limit_blocks(self):
        _root, conn = a_repo(self)
        rows = [(a_claim(conn, f"c{i}", "a"), OPEN) for i in range(4)]
        blocked, reported = state.split_open(
            conn, rows, self.CFG, {"report_max_open": 3})
        self.assertEqual(len(blocked), 4)
        self.assertTrue(all(str(w).startswith("ESCALATED")
                            for _r, _s, w in reported))
        self.assertIn("4 open", reported[0][2])

    def test_the_same_pile_under_the_limit_does_not(self):
        _root, conn = a_repo(self)
        rows = [(a_claim(conn, f"c{i}", "a"), OPEN) for i in range(3)]
        blocked, _reported = state.split_open(
            conn, rows, self.CFG, {"report_max_open": 3})
        self.assertEqual(blocked, [])

    def test_one_older_than_the_limit_blocks(self):
        _root, conn = a_repo(self)
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        r = a_claim(conn, "c-old", "a", created_at=old)
        blocked, reported = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_days": 14})
        self.assertEqual(len(blocked), 1)
        self.assertIn("14-day", reported[0][2])

    def test_a_fresh_one_does_not(self):
        _root, conn = a_repo(self)
        now = datetime.now(timezone.utc).isoformat()
        r = a_claim(conn, "c-new", "a", created_at=now)
        blocked, _ = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_days": 14})
        self.assertEqual(blocked, [])

    def test_one_the_work_keeps_reopening_blocks(self):
        """The condition the other two miss. Six failures on one claim is not
        a backlog -- waiting does not fix it."""
        _root, conn = a_repo(self)
        r = a_claim(conn, "c-loop", "a", fails=6)
        blocked, reported = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_repeat": 5})
        self.assertEqual(len(blocked), 1)
        self.assertIn("failed 6 times", reported[0][2])

    def test_running_the_same_check_again_is_not_the_work_reopening(self):
        """The one that decides whether this threshold is usable at all.

        Counting attempt rows counts how many times somebody ran `v4 check`.
        Measured on this repo's ledger when it did: `test-weakened` had 257
        failing attempts across 50 distinct states -- 81% the same question
        asked again, one claim failing ten times in a single state, three of
        them inside one minute. At a limit of five that escalated 52% of its
        claims: the tier taking back with one rule what it granted with
        another, on the kind it was written for.
        """
        _root, conn = a_repo(self)
        r = a_claim(conn, "c-rerun", "a")
        for _ in range(9):
            ledger.append_attempt(conn, claim_id="c-rerun", exit_code=1,
                                  stdout="", stderr="", argv="[]",
                                  duration_ms=1, subject_digest="SAME",
                                  checker_sha="k", config_sha="c",
                                  worktree="/w", head_commit="d",
                                  started_at="n", ended_at="n")
        blocked, _ = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_repeat": 5})
        self.assertEqual(
            blocked, [],
            "nine re-runs of one unchanged check is one question, not nine")

    def test_but_failing_in_new_states_is(self):
        """The control. Without it the case above passes on the day somebody
        makes the count always return zero."""
        _root, conn = a_repo(self)
        r = a_claim(conn, "c-moving", "a")
        for i in range(7):
            ledger.append_attempt(conn, claim_id="c-moving", exit_code=1,
                                  stdout="", stderr="", argv="[]",
                                  duration_ms=1, subject_digest=f"s{i}",
                                  checker_sha="k", config_sha="c",
                                  worktree=f"/w{i}", head_commit="d",
                                  started_at="n", ended_at="n")
        blocked, reported = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_repeat": 5})
        self.assertEqual(len(blocked), 1)
        self.assertIn("failed 7 times", reported[0][2])

    def test_a_checker_that_cannot_judge_is_not_the_work_reopening(self):
        """Exit 4 is UNSUPPORTED. Counting it would escalate a kind for being
        unanswerable, which is a different problem with a different exit."""
        _root, conn = a_repo(self)
        r = a_claim(conn, "c-unsup", "a")
        for _ in range(9):
            ledger.append_attempt(conn, claim_id="c-unsup", exit_code=4,
                                  stdout="", stderr="", argv="[]",
                                  duration_ms=1, subject_digest="{}",
                                  checker_sha="k", config_sha="c",
                                  worktree="/", head_commit="d",
                                  started_at="n", ended_at="n")
        blocked, _ = state.split_open(
            conn, [(r, OPEN)], self.CFG, {"report_max_repeat": 5})
        self.assertEqual(blocked, [])

    def test_no_thresholds_configured_means_no_escalation(self):
        _root, conn = a_repo(self)
        rows = [(a_claim(conn, f"c{i}", "a", fails=20), OPEN) for i in range(30)]
        blocked, _ = state.split_open(conn, rows, self.CFG, {})
        self.assertEqual(blocked, [], "a repo that configured nothing is not "
                                      "escalated on a number nobody chose")


class TheRegistryAgreesWithTheAxis(unittest.TestCase):
    """The two lists, as they are on disk."""

    def setUp(self):
        import json
        d = json.loads((ROOT / ".v4" / "claim_kinds.json").read_text())
        self.kinds = d.get("kinds", d)

    def test_every_kind_has_a_gate(self):
        missing = [k for k, v in self.kinds.items() if "gate" not in v]
        self.assertEqual(missing, [])

    def test_every_kind_says_why(self):
        """`gate_why` is read by `split_open` and printed. Without a reader it
        would be the shape `dead-wiring` exists to find, in the registry that
        checker reads."""
        blank = [k for k, v in self.kinds.items()
                 if not str(v.get("gate_why", "")).strip()]
        self.assertEqual(blank, [])

    def test_the_ones_that_cannot_wait_still_block(self):
        """Named one by one rather than counted. A count passes on the day
        somebody moves `secret` to report and `lint` to ship."""
        for kind in ("secret", "external-write", "scope", "test",
                     "fail-closed", "runtime-proof", "surface-proof",
                     "review-finding"):
            with self.subTest(kind=kind):
                self.assertEqual(state.gate_for(self.kinds, kind),
                                 state.GATE_SHIP)

    def test_the_ones_that_can_wait_do_not(self):
        for kind in ("test-weakened", "test-expectation", "test-shape",
                     "test-token-shape", "signature-change", "dangling-ref",
                     "lint", "layer-boundary", "spec-coverage", "design-pins",
                     "registry-consistency", "dead-wiring",
                     "control-plane-budget"):
            with self.subTest(kind=kind):
                self.assertEqual(state.gate_for(self.kinds, kind),
                                 state.GATE_REPORT)

    def test_the_three_thresholds_have_defaults(self):
        """A configured value with no default raises on the first adopter whose
        report pile grows."""
        from kernel import config
        for key in state.ESCALATE_KEYS:
            with self.subTest(key=key):
                self.assertIsInstance(config.DEFAULT_THRESHOLDS.get(key), int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
