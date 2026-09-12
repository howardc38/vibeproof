"""The export that could not walk its own chain.

    python3 -m unittest tests.test_the_export_that_could_not_verify_itself -v

Two adopters reported it on the same day and it reproduces here: the row is
hashed at `insert` over the text as written, the exporter blanks credential
shapes in that same text on the way out, and `verify_exported` re-derives over
what it can see. So every sentence the redactor touches breaks the chain by
construction, and the accusation it prints -- "Something edited the row after it
was written" -- names a person who does not exist. `v4 audit --events` exited 1
on this repo, and on one adopter it is the CI job that has never passed.

The values below are synthetic: generated for this fixture, never issued, never
valid. Recorded because location is never evidence, only the value is.

All of them fail against 592d840.
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

from kernel import ledger as L  # noqa: E402
from kernel.analysis import redaction  # noqa: E402

#: A redactable shape, built rather than written, and deliberately not a URI
#: carrying a password: `secret` is right to flag one of those in a file that
#: is not a `secret` fixture, and this value exists to be blanked rather than
#: to be found. Any shape the redactor catches serves the cases below.
SECRET = "api_key=" + "K7" + "x" * 18 + "9Q"

#: The vendor prefixes, with a body no issuer produces. Built rather than
#: written, so no literal in this file is shaped like a live key: this is not a
#: `secret` fixture and `test-token-shape` is right to ask about one that is.
AWSISH = "AKIA" + "0" * 16

#: The URL rule's control needs a URI with a password in it, and a URI with a
#: password in it is the thing `secret` exists to refuse in a committed file.
#: Assembled through a name so no literal here is one -- the case still puts a
#: real one to `redact`, which is what it is for.
_PW = "hunter" + "2pw"
URIISH = "postgres://u:" + _PW + "@db.prod/app"
GHISH = "ghp_" + "0" * 24


class TheExportVerifiesItself(unittest.TestCase):
    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        conn = L.connect(tmp)
        self.addCleanup(conn.close)
        L.insert(conn, "task", id="t", request="r" * 80, scope_globs=["**"],
                 base_commit="x", created_at="2026")
        return tmp, conn

    def _export(self, tmp, conn):
        out = tmp / ".v4" / "ledger_export.jsonl"
        L.export_jsonl(conn, out, tmp)
        return out

    def test_a_sentence_carrying_a_credential_still_verifies(self):
        """The finding, put as the property that has to hold. Any sentence the
        redactor touches used to break the chain."""
        tmp, conn = self._repo()
        L.insert(conn, "event", task_id="t", claim_id=None, kind="engagement",
                 actor="worker",
                 payload={"sentence": f"the connection is {SECRET} today"},
                 created_at="2026")
        _n, problems = L.verify_exported(self._export(tmp, conn))
        self.assertEqual(problems, [], problems)

    def test_and_the_credential_is_not_in_the_export(self):
        """The other half, and the reason the case above cannot be satisfied by
        simply not redacting: the cheapest way to make a chain verify is to stop
        blanking anything."""
        tmp, conn = self._repo()
        L.insert(conn, "event", task_id="t", claim_id=None, kind="engagement",
                 actor="worker",
                 payload={"sentence": f"the connection is {SECRET} today"},
                 created_at="2026")
        text = self._export(tmp, conn).read_text(encoding="utf-8")
        self.assertNotIn("K7" + "x" * 18 + "9Q", text)
        self.assertIn("[redacted]", text)

    def test_and_it_is_not_in_the_ledger_either(self):
        """Redaction moved to `insert`, so the value never reaches
        `.git/v4/ledger.db`. That file is untracked, which is not the same as
        gone: it is copied, bundled and printed like any other file."""
        tmp, conn = self._repo()
        L.insert(conn, "event", task_id="t", claim_id=None, kind="engagement",
                 actor="worker",
                 payload={"sentence": f"the connection is {SECRET} today"},
                 created_at="2026")
        stored = conn.execute(
            "SELECT payload FROM event WHERE kind = 'engagement'").fetchone()[0]
        self.assertNotIn("K7" + "x" * 18 + "9Q", stored)

    def test_a_real_edit_is_still_caught(self):
        """The control. A walk that verifies everything is the shape being
        repaired, pointed the other way."""
        tmp, conn = self._repo()
        L.insert(conn, "event", task_id="t", claim_id=None, kind="engagement",
                 actor="worker", payload={"sentence": "an ordinary sentence"},
                 created_at="2026")
        out = self._export(tmp, conn)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        for r in rows:
            if r.get("_table") == "event":
                r["payload"] = json.dumps({"sentence": "EDITED"})
                break
        out.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows)
                       + "\n", encoding="utf-8")
        _n, problems = L.verify_exported(out)
        self.assertTrue(any("edited the row" in p for p in problems), problems)

    def test_a_row_the_exporter_blanked_says_so_rather_than_accusing(self):
        """A row written before this repair still holds the original text, so
        the exporter still blanks it and its hash still cannot be re-derived.
        What changed is the sentence: it names the exporter, not a person."""
        tmp, conn = self._repo()
        # Bypass `insert` to write the row the old writer produced: hashed over
        # the original text. The append-only triggers refuse a direct insert,
        # which is the point of them, so they come off first -- the same move a
        # forger makes, used here to build a row this framework really did write
        # before the repair.
        for t in list(conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'")):
            conn.execute(f"DROP TRIGGER IF EXISTS {t[0]}")
        conn.execute(
            "INSERT INTO event (task_id, claim_id, kind, actor, payload, "
            "created_at, prev_hash, row_hash, scheme) VALUES "
            "('t', NULL, 'engagement', 'worker', ?, '2026', ?, 'deadbeef', ?)",
            (json.dumps({"sentence": f"see {SECRET} here"}), L.GENESIS,
             L.EVENT_SCHEME))
        conn.commit()
        # The historical writer did hash the original correctly. Give this
        # fixture that real shape, then remove the new projection sidecar to
        # exercise an old export that has no upgrade proof yet. A corrupt
        # original hash is separately refused by the projection tests.
        row = dict(conn.execute("SELECT * FROM event ORDER BY id DESC LIMIT 1").fetchone())
        conn.execute("UPDATE event SET row_hash=? WHERE id=?",
                     (L._event_hash(row["prev_hash"], row), row["id"]))
        conn.commit()
        out = self._export(tmp, conn)
        L.projection_path(out).unlink()
        _n, problems = L.verify_exported(out)
        self.assertTrue(problems, "an unverifiable row was reported clean")
        self.assertTrue(any("cannot be verified from the export alone" in p
                            for p in problems), problems)
        self.assertFalse(any("edited the row" in p for p in problems),
                         f"the false accusation survived: {problems}")


class TheEventChainCrossesASegmentBoundary(unittest.TestCase):
    """`ev_prev` was initialised per file while `prev` was carried across, so
    the first event of the open file was compared against GENESIS instead of
    the last event of the segment before it. Measured on this repo the day the
    event walk landed: one `prev_hash does not follow` at exactly that seam."""

    def test_a_sealed_segment_and_the_file_after_it_chain(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        conn = L.connect(tmp)
        self.addCleanup(conn.close)
        L.insert(conn, "task", id="t", request="r" * 80, scope_globs=["**"],
                 base_commit="x", created_at="2026")
        out = tmp / ".v4" / "ledger_export.jsonl"
        for i in range(6):
            L.insert(conn, "event", task_id="t", claim_id=None,
                     kind="scope_widen", actor="person",
                     payload={"added": [f"p{i}.py"], "why": "w" * 80},
                     created_at="2026")
            # A tiny seal size, so the second export seals the first file and
            # the boundary this case is about actually exists.
            L.export_jsonl(conn, out, tmp, seal_at=900)
        self.assertTrue(list(out.parent.glob("ledger_export.jsonl.0001")),
                        "nothing sealed, so there is no boundary to cross")
        _n, problems = L.verify_exported(out)
        self.assertEqual([p for p in problems if "does not follow" in p], [],
                         problems)


class TheRedactionIsKeyedOnTheValue(unittest.TestCase):
    """The pattern required a keyword immediately before the value and twelve
    or more characters after any separator, so it was wrong in both
    directions."""

    def test_an_english_word_is_not_a_credential(self):
        """Measured on an adopter's own engagement sentence: `specifically` is
        exactly twelve letters, and the reviewer chasing the blanked word finds
        a word."""
        self.assertEqual(redaction.redact("check the token specifically."),
                         "check the token specifically.")

    def test_a_bare_credential_shape_is_one(self):
        """The other direction: nothing precedes it, so every keyword rule
        missed it."""
        got = redaction.redact("value 1234567:synthetic-not-a-real-bot-token x")
        self.assertNotIn("synthetic-not-a-real-bot-token", got)

    def test_and_the_rules_that_worked_still_work(self):
        """The controls. A pattern narrowed until it stops eating prose, and
        also stops catching credentials, is worse than the defect."""
        for probe, gone in (
            (URIISH, _PW),
            # Synthetic: generated for this fixture, never issued, never valid.
            # Assembled from parts so the file carries no literal shaped like a
            # live key -- `test-token-shape` is right to ask, and these exist to
            # be blanked rather than to be found.
            ("api_key=" + AWSISH, AWSISH),
            ("token: " + GHISH, GHISH),
        ):
            with self.subTest(probe):
                self.assertNotIn(gone, redaction.redact(probe))

    def test_and_an_identifier_this_ledger_is_full_of_is_left_alone(self):
        """A commit hash and a UUID are not credentials, and blanking them
        would make the record unreadable to buy nothing."""
        probe = "commit 4f0d7892d375 and 0f1aad17-b7d2-4e3a-9c1b-2d3e4f5a6b7c"
        self.assertEqual(redaction.redact(probe), probe)

    def test_each_rule_carries_its_own_replacement(self):
        """The dispatch was `pat.groups == 2` / `== 3`, so a new rule with the
        same group count would silently take another's substitution -- and the
        bare-credential rule has two groups, exactly like the URL rule whose
        replacement keeps the host."""
        for entry in redaction._REDACTIONS:
            with self.subTest(str(entry)[:40]):
                self.assertEqual(len(entry), 2)


if __name__ == "__main__":
    unittest.main()
