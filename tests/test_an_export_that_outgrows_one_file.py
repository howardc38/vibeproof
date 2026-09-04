"""An export that outgrows one file is sealed and continued, not rewritten forever.

    python3 -m unittest tests.test_an_export_that_outgrows_one_file -v

Measured on an adopter on 2026-09-02: `.v4/ledger_export.jsonl` at 58.7 MB and
growing 2.34 MB a day, 23 days into using this. GitHub warns at 50 MiB per file
and refuses a push at 100 MiB, so the committed record the whole CI chain walk
rests on was seventeen days from becoming unpushable. Compressing it was the
obvious move and the wrong one: an append-only text file packs into git as the
lines added since the last version, and a gzip of the same bytes is a whole
new blob on every ship.

So `export_jsonl` seals. Once the open file has outgrown `SEAL_AT` it is
renamed to `ledger_export.jsonl.0001` and never written again, and the new
open file opens with a `_segment` row saying how many rows of each table the
sealed files already hold and which attempt hash they ended on.
`verify_exported` walks the sealed files first and carries the chain across;
`sweep.last_exported` reads whichever file holds the newest sweep.

Every test here seals at one byte, because the number that decides sealing is
a parameter and the property under test is what happens on either side of it,
not where the line sits.
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

from kernel import hashing, ledger, sweep  # noqa: E402

EXPORT = ".v4/ledger_export.jsonl"


def _repo(case) -> Path:
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    return tmp


def _attempts(conn, first: int, n: int):
    """`n` claims and one attempt each, ids `c<first>` onward."""
    for i in range(first, first + n):
        ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        ledger.append_attempt(conn, claim_id=f"c{i}", subject_digest="{}",
                              checker_sha="s", config_sha="c", head_commit="h",
                              worktree="w", argv="[]", exit_code=0, stdout=f"out {i}",
                              stderr="", started_at="2026", ended_at="2026",
                              duration_ms=1)


def _rows(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class BelowTheThresholdNothingChanges(unittest.TestCase):
    """A repo that never outgrows one file writes the bytes it always did."""

    def test_no_segment_row_and_no_sibling(self):
        tmp = _repo(self)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="", created_at="2026")
        _attempts(conn, 0, 3)
        out = tmp / EXPORT
        ledger.export_jsonl(conn, out, tmp)
        ledger.export_jsonl(conn, out, tmp)
        self.assertEqual(ledger.segment_files(out), [])
        self.assertNotIn(ledger.SEGMENT_TABLE, {r["_table"] for r in _rows(out)})
        self.assertEqual(ledger.verify_exported(out), (3, []))


class AnOversizedExportIsSealedAndContinued(unittest.TestCase):
    def setUp(self):
        self.tmp = _repo(self)
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="", created_at="2026")
        _attempts(self.conn, 0, 3)
        self.out = self.tmp / EXPORT
        # The first export is written at the default threshold, so it looks
        # exactly like every export that exists today: no `_segment` row.
        ledger.export_jsonl(self.conn, self.out, self.tmp)
        self.first = self.out.read_bytes()
        _attempts(self.conn, 3, 2)
        ledger.export_jsonl(self.conn, self.out, self.tmp, seal_at=1)

    def test_the_old_file_is_sealed_byte_for_byte(self):
        sealed = ledger.segment_files(self.out)
        self.assertEqual([p.name for p in sealed], ["ledger_export.jsonl.0001"])
        self.assertEqual(sealed[0].read_bytes(), self.first)

    def test_the_open_file_carries_only_what_the_sealed_one_does_not(self):
        rows = _rows(self.out)
        header = rows[0]
        self.assertEqual(header["_table"], ledger.SEGMENT_TABLE)
        self.assertEqual(header["seq"], 2)
        self.assertEqual(header["skip"]["attempt"], 3)
        self.assertEqual(header["skip"]["claim"], 3)
        self.assertEqual(header["skip"]["task"], 1)
        attempts = [r for r in rows if r["_table"] == "attempt"]
        self.assertEqual([r["id"] for r in attempts], [4, 5])
        # The chain head the sealed file ended on, so the walk can cross over.
        sealed_attempts = [r for r in _rows(ledger.segment_files(self.out)[0])
                           if r["_table"] == "attempt"]
        self.assertEqual(header["starts_after"], sealed_attempts[-1]["row_hash"])
        self.assertEqual(attempts[0]["prev_hash"], header["starts_after"])

    def test_the_walk_crosses_the_boundary(self):
        self.assertEqual(ledger.verify_exported(self.out), (5, []))

    def test_sealing_again_numbers_the_next_segment(self):
        _attempts(self.conn, 5, 1)
        ledger.export_jsonl(self.conn, self.out, self.tmp, seal_at=1)
        self.assertEqual([p.name for p in ledger.segment_files(self.out)],
                         ["ledger_export.jsonl.0001", "ledger_export.jsonl.0002"])
        header = _rows(self.out)[0]
        self.assertEqual(header["seq"], 3)
        self.assertEqual(header["skip"]["attempt"], 5)
        self.assertEqual(ledger.verify_exported(self.out), (6, []))

    def test_a_row_exported_once_is_never_exported_again(self):
        _attempts(self.conn, 5, 1)
        ledger.export_jsonl(self.conn, self.out, self.tmp, seal_at=1)
        seen = []
        for f in ledger.segment_files(self.out) + [self.out]:
            seen += [r["id"] for r in _rows(f) if r["_table"] == "attempt"]
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6])


class WhatTheWalkStillCatchesAcrossFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = _repo(self)
        conn = ledger.connect(self.tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="", created_at="2026")
        _attempts(conn, 0, 3)
        self.out = self.tmp / EXPORT
        ledger.export_jsonl(conn, self.out, self.tmp)
        _attempts(conn, 3, 2)
        ledger.export_jsonl(conn, self.out, self.tmp, seal_at=1)
        _attempts(conn, 5, 2)
        ledger.export_jsonl(conn, self.out, self.tmp, seal_at=1)
        self.sealed = ledger.segment_files(self.out)
        self.assertEqual(len(self.sealed), 2)
        self.assertEqual(ledger.verify_exported(self.out), (7, []))

    def test_a_sealed_segment_cut_short(self):
        """The sealed file carries its own anchor, so the same truncation that
        is seen on a single file is seen on a segment."""
        lines = [l for l in self.sealed[0].read_text().splitlines() if l.strip()]
        anchor = [l for l in lines if ledger.ANCHOR_TABLE in l]
        body = [l for l in lines if ledger.ANCHOR_TABLE not in l]
        self.sealed[0].write_text("\n".join(body[:-1] + anchor) + "\n")
        _n, problems = ledger.verify_exported(self.out)
        self.assertTrue(any("ledger_export.jsonl.0001" in p and "anchor" in p
                            for p in problems), problems)

    def test_a_sealed_segment_removed(self):
        self.sealed[0].unlink()
        _n, problems = ledger.verify_exported(self.out)
        self.assertTrue(any("missing or misnamed" in p for p in problems), problems)
        self.assertTrue(any("continues from" in p for p in problems), problems)

    def test_an_open_file_rebuilt_from_elsewhere(self):
        """A `_segment` row that claims a different chain head is a file that
        was not written after the segment it says it follows."""
        rows = _rows(self.out)
        rows[0]["starts_after"] = "f" * 64
        self.out.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        _n, problems = ledger.verify_exported(self.out)
        self.assertTrue(any("continues from" in p for p in problems), problems)


class TheReadersThatWereNotVerifyingStillFindTheirRows(unittest.TestCase):
    def test_the_sweep_the_open_file_no_longer_holds(self):
        tmp = _repo(self)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        sweep.record(conn, lenses=["devx"], findings=0)
        when = sweep.last(conn)
        out = tmp / EXPORT
        ledger.export_jsonl(conn, out, tmp)
        # Seal it away: the sweep row now lives only in the sealed segment.
        ledger.insert(conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="", created_at="2026")
        ledger.export_jsonl(conn, out, tmp, seal_at=1)
        self.assertNotIn(sweep.KIND, out.read_text())
        self.assertEqual(sweep.last_exported(tmp), when)

    def test_a_sealed_segment_is_still_kernel_output_to_the_scope_checker(self):
        """`hashing.KERNEL_WRITTEN` matches the export by prefix, and the
        sealed name keeps that prefix on purpose."""
        self.assertTrue(hashing.kernel_written("ledger_export.jsonl.0001".join(
            [".v4/", ""])))
        self.assertTrue(hashing.kernel_written(".v4/ledger_export.jsonl"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
