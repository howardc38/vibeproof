"""Repairs in `kernel/ledger.py`: what the export carries out, and what a
`kind` in it means.

    python3 -m unittest tests.test_what_leaves_the_ledger_and_what_a_kind_means -v

Two facts of the same shape -- something written that no reader can reach.

The export is the artefact that leaves this repo: committed, scanned, and the
only thing an auditor gets, because the database lives in `.git/v4/`. Its last
filter judged by the twelve shipped credential families and never by the ones
the repo it came from declares, so a family an adopter wrote down was detected
by `secret_scan` and written out in the clear.

The other direction: `event.kind` is what a reader queries this table by, and
the only enumeration of that vocabulary was a SQL comment naming six values
while the code wrote thirty-two.

All of them fail against 0785a00.
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

from kernel import ledger  # noqa: E402

#: A webhook signing secret, in the shape a scanner reads and with `notareal`
#: in the value rather than in the variable name -- the literal travels, the
#: name does not. Not one of the twelve shipped families on purpose: the whole
#: question here is what happens to a family only the repo declares.
FAKE_SIGNING_SECRET = "whsec_notarealsigningsecret00000000"

#: One of the twelve, for the other half of the same question.
FAKE_AWS_KEY = "AKIATESTNOTAREALKEY0"

#: What a repo says when it has a family of its own.
DECLARED = {"patterns": [{"id": "webhook-signing",
                          "label": "a webhook signing secret",
                          "kind": "provider", "order": 900,
                          "regex": r"whsec_[A-Za-z0-9]{20,}"}]}


def _repo(case, *, patterns=None):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    if patterns is not None:
        (tmp / ".v4" / "secret_patterns.json").write_text(json.dumps(patterns))
    return tmp


def _conn(case, root):
    conn = ledger.connect(root)
    case.addCleanup(conn.close)
    return conn


def _task(conn, request):
    ledger.insert(conn, "task", id="t-export", request=request,
                  scope_globs=["**"], base_commit="",
                  created_at="2026-08-27T00:00:00+00:00")


class TheExportIsFilteredByTheTableThisRepoDeclares(unittest.TestCase):
    """`_redact` called `redact(text)` and dropped the repo on the floor.

    Measured before the repair, on a repo declaring one `whsec_` family: the
    token survives `redact(text)`, survives `ledger._redact(text)`, is blanked
    by `redact(text, root)`, and lands verbatim in `.v4/ledger_export.jsonl`.
    `runner.py` and `derive.py` were already passing the root; this was the one
    call that was not, and it is the last one before a committed file.
    """

    def test_a_family_this_repo_declares_does_not_reach_the_export(self):
        root = _repo(self, patterns=DECLARED)
        conn = _conn(self, root)
        _task(conn, f"rotate the signing secret {FAKE_SIGNING_SECRET} today")
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        body = out.read_text()
        self.assertNotIn(FAKE_SIGNING_SECRET, body)
        self.assertIn("[redacted]", body,
                      "the row has to still be there with the value blanked, "
                      "not dropped -- an auditor walks every row")

    def test_the_shipped_families_are_blanked_beside_the_declared_one(self):
        """Union, not replacement. A repo that declares its own families must
        not lose the twelve that ship, or declaring one costs eleven."""
        root = _repo(self, patterns=DECLARED)
        conn = _conn(self, root)
        _task(conn, f"the key {FAKE_AWS_KEY} and the secret "
                    f"{FAKE_SIGNING_SECRET} are both in this sentence")
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        body = out.read_text()
        self.assertNotIn(FAKE_AWS_KEY, body)
        self.assertNotIn(FAKE_SIGNING_SECRET, body)

    def test_a_repo_that_declares_none_still_gets_the_shipped_twelve(self):
        root = _repo(self)
        conn = _conn(self, root)
        _task(conn, f"the key {FAKE_AWS_KEY} is in this sentence")
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        self.assertNotIn(FAKE_AWS_KEY, out.read_text())


class EveryKindAReaderMeetsHasASentence(unittest.TestCase):
    """The vocabulary was a SQL comment naming six of thirty-two kinds.

    `scope_widen|engagement|detector_run|blocked|remerge|register` was the only
    enumeration anywhere, so `hook_seen`, `checker_out`, `lens_run`,
    `lens_reviewed`, `shipped` and twenty-one others were names a reader met in
    a query with nowhere to look them up. `runner.record` had already repaired
    the identical omission for `cost_observation.source`.
    """

    def test_the_vocabulary_is_a_value_a_reader_can_read(self):
        for kind, meaning in ledger.EVENT_KINDS.items():
            self.assertTrue(meaning.strip(), f"{kind} carries no sentence")
            self.assertNotEqual(meaning.strip(), kind,
                                f"{kind} is glossed with its own name")
            self.assertGreater(len(meaning.strip()), 20,
                               f"{kind}: a gloss shorter than the name it "
                               f"explains is the comment again")

    def test_the_names_the_comment_never_carried_are_in_it(self):
        for kind in ("hook_seen", "checker_out", "lens_run", "lens_reviewed",
                     "shipped", "retracted", "finding_deferred"):
            self.assertIn(kind, ledger.EVENT_KINDS)

    def test_a_row_written_through_the_kernel_names_a_declared_kind(self):
        root = _repo(self)
        conn = _conn(self, root)
        _task(conn, "r")
        ledger.insert(conn, "event", task_id="t-export", claim_id=None,
                      kind="shipped", actor="kernel", payload={"held": False},
                      created_at="2026-08-27T00:00:00+00:00")
        row = conn.execute("SELECT kind FROM event").fetchone()
        self.assertIn(row["kind"], ledger.EVENT_KINDS)


class AKindNothingDeclaresIsRefusedAtTheWrite(unittest.TestCase):
    """A comment cannot hold a vocabulary: this one drifted by twenty-six.

    So the set is a value with teeth. The rows are permanent -- the ledger takes
    no updates -- which is why this is refused at the write rather than reported
    afterwards, and why the vocabulary is what the code *writes*: `remerge` has
    never once been written in this repo's own ledger and `kernel/remerge.py`
    writes it, so it belongs to the set while `blocked`, the row that ledger
    actually holds from that module, is not the whole story.
    """

    def test_an_undeclared_kind_does_not_become_a_row(self):
        root = _repo(self)
        conn = _conn(self, root)
        _task(conn, "r")
        with self.assertRaises(ledger.UndeclaredEventKind):
            ledger.insert(conn, "event", task_id="t-export", claim_id=None,
                          kind="lens_swept", actor="worker", payload={},
                          created_at="2026-08-27T00:00:00+00:00")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM event").fetchone()[0], 0,
            "the refusal has to happen before the write gate opens")

    def test_the_refusal_says_where_the_set_lives(self):
        root = _repo(self)
        conn = _conn(self, root)
        _task(conn, "r")
        try:
            ledger.insert(conn, "event", task_id="t-export", claim_id=None,
                          kind="not_a_kind", actor="worker", payload={},
                          created_at="2026-08-27T00:00:00+00:00")
        except ledger.UndeclaredEventKind as exc:
            said = str(exc)
        else:                                          # pragma: no cover
            self.fail("an undeclared kind was written")
        self.assertIn("EVENT_KINDS", said)
        self.assertIn("kernel/ledger.py", said)

    def test_a_kind_this_ledger_has_never_held_is_still_writable(self):
        """`remerge` is in the vocabulary because the code writes it, not
        because a row exists. A set derived from what the table happens to hold
        would refuse the first `v4 remerge` this repo ever runs."""
        root = _repo(self)
        conn = _conn(self, root)
        _task(conn, "r")
        ledger.insert(conn, "event", task_id="t-export", claim_id=None,
                      kind="remerge", actor="orchestrator",
                      payload={"round": 1},
                      created_at="2026-08-27T00:00:00+00:00")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM event WHERE kind = 'remerge'"
                         ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
