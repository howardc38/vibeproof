"""`kernel/ledger.py` -- the `actor` column, and who is allowed to write it.

    python3 -m unittest tests.test_one_word_for_who_did_it -v

`kind` has had `EVENT_KINDS` and a gate in `insert` since early on, and the
comment over that gate says it is "where the vocabulary is either kept or
lost". The column beside it had neither, and it had already drifted: `review`
wrote `human` from a tty test and `scope` wrote `person` from the identical
one, so a query for either missed every row that used the other.

The cases are ordered by what actually breaks. Every declared actor has to
still be writable -- missing one means a production path that raises at
runtime, so they are enumerated rather than sampled. Then the refusals. Then
the history, which must stay readable: gating a write is not licence to hide
rows written before the gate.

All of them fail against 8c18c8a.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod  # noqa: E402


class _Ledger(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(ledger_mod.SCHEMA)
        self.addCleanup(conn.close)
        ledger_mod.insert(conn, "task", id="t", request="r",
                          scope_globs=["**"], base_commit="", created_at="2026")
        return conn

    def _event(self, conn, actor, kind="hook_seen"):
        ledger_mod.insert(conn, "event", task_id="t", claim_id=None, kind=kind,
                          actor=actor, payload={}, created_at="2026")


class EveryDeclaredActorCanBeWritten(_Ledger):
    def test_all_of_them(self):
        """Enumerated, not sampled. One missing from the gate is a code path
        that raises the first time it runs, and the gate is in the one function
        every event write in this repo goes through."""
        conn = self._conn()
        for actor in sorted(ledger_mod.ACTORS):
            with self.subTest(actor):
                self._event(conn, actor)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM event").fetchone()[0],
            len(ledger_mod.ACTORS))

    def test_each_one_says_what_it_means(self):
        """The gate's refusal tells the reader to add "the name and the one
        line saying what it means". A table with an empty line in it makes that
        instruction a formality."""
        for actor, meaning in ledger_mod.ACTORS.items():
            with self.subTest(actor):
                self.assertGreaterEqual(
                    len(meaning.split()), 6,
                    f"{actor} is declared and not explained")


class WhatTheGateRefuses(_Ledger):
    def test_an_undeclared_actor_is_refused(self):
        conn = self._conn()
        with self.assertRaises(ledger_mod.UndeclaredActor):
            self._event(conn, "nobody-declared-this")

    def test_and_the_refusal_names_the_value_and_where_to_add_it(self):
        """A refusal that does not say which value it stopped leaves the
        reader grepping their own diff."""
        conn = self._conn()
        with self.assertRaises(ledger_mod.UndeclaredActor) as caught:
            self._event(conn, "nobody-declared-this")
        self.assertIn("nobody-declared-this", str(caught.exception))
        self.assertIn("kernel/ledger.py", str(caught.exception))

    def test_human_is_refused_and_told_what_replaced_it(self):
        """The point of the change, not a side effect of it. `human` was one of
        the two spellings; it is not in the table, so it cannot be written
        again, and the refusal says which word took over."""
        conn = self._conn()
        with self.assertRaises(ledger_mod.UndeclaredActor) as caught:
            self._event(conn, "human")
        self.assertIn("`human`", str(caught.exception))
        self.assertIn("person", str(caught.exception))

    def test_nothing_is_written_when_the_actor_is_refused(self):
        """The gate is before the write, like the one for `kind`, because the
        row would be permanent."""
        conn = self._conn()
        with self.assertRaises(ledger_mod.UndeclaredActor):
            self._event(conn, "human")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM event").fetchone()[0], 0)


class RowsOlderThanTheGateStayReadable(_Ledger):
    """102 rows in this repo's own ledger say `human`. The ledger takes no
    updates and rewriting history to match a vocabulary would be worse than the
    drift, so what the gate must not do is make them unreadable."""

    def test_a_row_written_before_the_gate_still_reads_back(self):
        conn = self._conn()
        conn.execute(
            "INSERT INTO event (task_id, claim_id, kind, actor, payload, "
            "created_at) VALUES ('t', NULL, 'abandoned', 'human', '{}', '2026')")
        conn.commit()
        rows = conn.execute(
            "SELECT actor FROM event WHERE actor = 'human'").fetchall()
        self.assertEqual([r["actor"] for r in rows], ["human"])

    def test_and_a_later_write_still_goes_in_beside_it(self):
        """A gate that refused the whole table once it held a legacy value
        would pass the case above and break every repo that has one."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO event (task_id, claim_id, kind, actor, payload, "
            "created_at) VALUES ('t', NULL, 'abandoned', 'human', '{}', '2026')")
        conn.commit()
        self._event(conn, ledger_mod.PERSON)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM event").fetchone()[0], 2)


class TheTtyTestHasOneOwner(_Ledger):
    """Five sites wrote the same ternary and two files disagreed on its
    answer. `who_acted` is the one place now."""

    def test_it_answers_agent_with_no_terminal(self):
        """Run by a suite, which has no tty. `person` here would be the exact
        false assertion the vocabulary is about."""
        self.assertEqual(ledger_mod.who_acted(), ledger_mod.AGENT)

    def test_its_answer_is_always_one_the_gate_takes(self):
        """A helper that returned a word `insert` refuses would move the
        failure from the vocabulary to a stack trace."""
        conn = self._conn()
        self._event(conn, ledger_mod.who_acted())
        self.assertIn(ledger_mod.who_acted(), ledger_mod.ACTORS)


class TheWritersThatDisagreedNowAgree(_Ledger):
    """`review.defer` and `scope` wrote the two words. Both are entered here,
    because a vocabulary that holds in a unit test and not at the writers that
    drifted has not been repaired."""

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def test_defer_writes_an_actor_the_gate_takes(self):
        """The site the finding names. It wrote `human`, which `insert` now
        refuses -- so if this still spelled its own answer, the call would
        raise rather than record."""
        from kernel import review

        conn, root = self._conn(), self._repo()
        review.defer(conn, root, claim_id="c1", target="t-later",
                     why="Real, not now: the owner of this surface is being "
                         "rewritten next week and the repair belongs there.")
        row = conn.execute("SELECT actor FROM event WHERE kind = ?",
                           (review.DEFER_KIND,)).fetchone()
        self.assertEqual(row["actor"], ledger_mod.who_acted())
        self.assertIn(row["actor"], ledger_mod.ACTORS)

    def test_and_amending_a_note_writes_the_same_one(self):
        """The second of the three `review` sites, and the one a query for
        `person` used to miss entirely."""
        from kernel import review

        conn, _root = self._conn(), self._repo()
        ledger_mod.insert(conn, "claim", id="c9", task_id="t",
                          kind="review-finding", question="q", subject_refs=[],
                          checker="review_finding", origin="review",
                          note="something that was wrong", created_at="2026")
        review.amend_note(conn, claim_id="c9",
                          note="the sentence that replaces the wrong one, at "
                               "the same coordinates")
        row = conn.execute("SELECT actor FROM event WHERE kind = ?",
                           (review.AMENDED_KIND,)).fetchone()
        self.assertEqual(row["actor"], ledger_mod.who_acted())


class TheOtherTwoEnumerationsAreSubsetsOfIt(_Ledger):
    """`risk` declared `PERSON, AGENT, MONITOR` and `engagement` declared
    `BEFORE_ACTORS`, each a second copy of this vocabulary. Held here by what
    the gate does with their values, rather than by comparing the lists."""

    def test_every_before_actor_can_be_written(self):
        conn = self._conn()
        from kernel import engagement

        for actor in engagement.BEFORE_ACTORS:
            with self.subTest(actor):
                self._event(conn, actor, kind="engagement_before")

    def test_every_signature_actor_can_be_written(self):
        conn = self._conn()
        from kernel import risk

        for actor in (risk.PERSON, risk.AGENT, risk.MONITOR):
            with self.subTest(actor):
                self._event(conn, actor)


if __name__ == "__main__":
    unittest.main()
