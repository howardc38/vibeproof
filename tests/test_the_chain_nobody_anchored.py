"""`kernel/ledger.py` -- the chain that had no witness outside the database.

    python3 -m unittest tests.test_the_chain_nobody_anchored -v

`attempt` has had an anchor since a forgery was reproduced against it: three
rows deleted and re-appended with different content, and `audit_chain`
answering `(True, [])`. `event` was chained and never anchored, and it is the
table that holds `scope_widen` -- the only input to `current_scope` in both
`kernel/scope.py` and `hooks/write_block.py` -- along with engagement verdicts
and every `hook_seen` mark.

The tampering here is real tampering: the append-only triggers are dropped and
rows are deleted or re-inserted, which is the shape the finding measured and the
shape the `attempt` repair was reproduced with. A mocked walk would prove
nothing about a mechanism whose whole job is to survive a database that lies.

All of them fail against 42ff51d.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod  # noqa: E402


class _Anchored(unittest.TestCase):
    def _repo(self, events=6):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger_mod.connect(tmp)
        self.addCleanup(conn.close)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        for i in range(events):
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="scope_widen", actor="person",
                              payload={"path": f"p{i}.py", "why": "w" * 60},
                              created_at="2026")
        ledger_mod.write_chain_head(conn, tmp)
        return tmp, conn

    def _unlock(self, conn):
        """Drop the append-only triggers, which is what a forger does first."""
        for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"):
            conn.execute(f"DROP TRIGGER IF EXISTS {row[0]}")
        conn.commit()

    def _problems(self, conn, root):
        _ok, problems = ledger_mod.audit_chain(conn, root)
        return problems


class TheAnchorRecordsBothChains(_Anchored):
    def test_it_writes_the_event_count_and_head(self):
        root, conn = self._repo()
        anchor = json.loads(
            (root / ".v4" / "chain_head.json").read_text(encoding="utf-8"))
        self.assertEqual(anchor["events"], 6)
        self.assertNotEqual(anchor["event_head_hash"], ledger_mod.GENESIS)

    def test_and_an_untouched_ledger_is_clean(self):
        """The control. An anchor that reports on every run is the same as no
        anchor -- the shape this project exists to remove."""
        root, conn = self._repo()
        self.assertEqual(self._problems(conn, root), [])


class WhatTheEventAnchorCatches(_Anchored):
    """The same three questions the attempt anchor asks, put separately.
    Catching one of them says nothing about the other two -- the attempt side's
    own history is that the equal-count case went unchecked, and "matching the
    count was the whole forgery"."""

    def test_rows_deleted_off_the_end(self):
        root, conn = self._repo()
        self._unlock(conn)
        conn.execute("DELETE FROM event WHERE id > (SELECT MIN(id) + 2 FROM event)")
        conn.commit()
        problems = self._problems(conn, root)
        self.assertTrue(any("removed from the end" in p for p in problems),
                        f"a truncated event chain verified clean: {problems}")

    def test_a_chain_rebuilt_to_the_same_length(self):
        """The forgery. Every `row_hash` covers its own row, so a table deleted
        and re-appended is internally perfect and the walk sees nothing."""
        root, conn = self._repo()
        self._unlock(conn)
        conn.execute("DELETE FROM event")
        conn.commit()
        for i in range(6):
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="scope_widen", actor="person",
                              payload={"path": f"FORGED{i}.py",
                                       "why": "w" * 60},
                              created_at="2026")
        problems = self._problems(conn, root)
        self.assertTrue(
            any("same count, different chain" in p for p in problems),
            f"a rebuilt event chain of the same length verified clean: "
            f"{problems}")

    def test_and_ordinary_growth_stays_clean(self):
        """The other control. Growth is what a working repo does between ships,
        and an anchor that calls it tampering is one nobody keeps."""
        root, conn = self._repo()
        for i in range(3):
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="scope_widen", actor="person",
                              payload={"path": f"later{i}.py",
                                       "why": "w" * 60},
                              created_at="2026")
        self.assertEqual(self._problems(conn, root), [])

    def test_an_anchor_written_before_this_says_nothing(self):
        """Every adopter's committed `chain_head.json` predates these keys. A
        gate that goes red on everybody's first run after an upgrade is a gate
        that gets switched off."""
        root, conn = self._repo()
        path = root / ".v4" / "chain_head.json"
        old = json.loads(path.read_text(encoding="utf-8"))
        for k in ("events", "last_event_id", "event_head_hash"):
            old.pop(k, None)
        path.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
        self._unlock(conn)
        conn.execute("DELETE FROM event WHERE id > (SELECT MIN(id) + 2 FROM event)")
        conn.commit()
        problems = [p for p in self._problems(conn, root)
                    if "chain_head.json" in p and "event" in p]
        self.assertEqual(problems, [], f"an old anchor was read as a finding: "
                                       f"{problems}")


class TheExportedWalkReadsEventsToo(_Anchored):
    """`verify_exported` is the walk CI runs and the only one that can run
    there -- no database in reach. It re-derived every attempt and not one
    event."""

    def _export(self, root, conn):
        out = root / ".v4" / "ledger_export.jsonl"
        ledger_mod.export_jsonl(conn, out, root)
        return out

    def test_an_edited_event_row_is_reported(self):
        root, conn = self._repo()
        out = self._export(root, conn)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        for r in rows:
            if r.get("_table") == "event":
                r["payload"] = json.dumps({"path": "EDITED.py", "why": "w" * 60})
                break
        out.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows)
                       + "\n", encoding="utf-8")
        _rows, problems = ledger_mod.verify_exported(out)
        self.assertTrue(any("event" in p and "does not match" in p
                            for p in problems),
                        f"an edited event survived the exported walk: "
                        f"{problems}")

    def test_and_an_untouched_export_is_clean(self):
        root, conn = self._repo()
        out = self._export(root, conn)
        rows, problems = ledger_mod.verify_exported(out)
        self.assertEqual(problems, [])
        self.assertGreater(rows, 0, "the walk read no rows at all")


class TheLastFilterBeforeTheFileLeaves(_Anchored):
    """`_redact` is the last thing between a payload and a committed export.
    Its body was `except Exception: return text`, so the one case it exists for
    -- it could not run -- was the case it let through."""

    def test_a_filter_that_cannot_run_does_not_return_the_input(self):
        with mock.patch("kernel.analysis.redaction.redact",
                        side_effect=RuntimeError("pattern table unreadable")):
            got = ledger_mod._redact("token sk_live_0000000000000000", ROOT)
        self.assertNotIn("sk_live_", got)
        self.assertIn("could not be filtered", got)
        self.assertIn("RuntimeError", got)

    def test_and_a_filter_that_runs_still_passes_ordinary_text(self):
        """The control: refusing everything would pass the case above and make
        every export unreadable."""
        got = ledger_mod._redact("an ordinary note about a task", ROOT)
        self.assertEqual(got, "an ordinary note about a task")


class TheDomainReconciliationMovedAndBehavesTheSame(_Anchored):
    """`reconcile_deferrals` spelled `finding_deferred`,
    `finding_deferral_withdrawn`, `.v4/deferred/` and the `why`/`target`
    contract as literals, one layer below the module that owns all four."""

    def test_it_reports_a_deferral_whose_record_is_missing(self):
        from kernel import review

        root, conn = self._repo()
        ledger_mod.insert(conn, "claim", id="c1", task_id="t",
                          kind="review-finding", question="q",
                          subject_refs=[], checker="review_finding",
                          origin="review", note="n" * 40, created_at="2026")
        review.defer(conn, root, claim_id="c1", target="t-later",
                     why="Real, and not now: the owner of this surface is "
                         "being rewritten next week.")
        (root / ".v4" / "deferred" / "c1.json").unlink()
        problems = review.reconcile_deferrals(conn, root)
        self.assertTrue(any("c1" in p for p in problems), problems)

    def test_and_a_deferral_with_both_halves_is_clean(self):
        from kernel import review

        root, conn = self._repo()
        ledger_mod.insert(conn, "claim", id="c1", task_id="t",
                          kind="review-finding", question="q",
                          subject_refs=[], checker="review_finding",
                          origin="review", note="n" * 40, created_at="2026")
        review.defer(conn, root, claim_id="c1", target="t-later",
                     why="Real, and not now: the owner of this surface is "
                         "being rewritten next week.")
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                        "add", "-A"], cwd=root, capture_output=True)
        self.assertEqual(review.reconcile_deferrals(conn, root), [])


if __name__ == "__main__":
    unittest.main()
