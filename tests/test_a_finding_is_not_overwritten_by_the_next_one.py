"""Six findings from the 2026-08-27 lens sweep, all about `v4 review add`.

    python3 -m unittest tests.test_a_finding_is_not_overwritten_by_the_next_one -v

One fact with several faces: the command that files a reviewer's finding wrote
to one place and read from another, and its claim id had no room for a second
finding at one coordinate -- so a lens that found something new where it had
already filed rewrote what was there, printed `note amended on <id>`, and
exited 0.

It is not hypothetical and it is not old. That sweep amended ten notes across
nine claims; six of them were claims whose last attempt was exit 0 -- findings
opened on 8-18 and answered on 8-26, now serving somebody else's sentence. One
reviewer noticed and tried to put a note back by re-filing the original text,
which matched `claim.note`, returned `already open`, wrote nothing, and
reported success. That is the part worth keeping in view: the defect made even
the repair look done.

All of these fail against dbd0a60.
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
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, config, hashing, ledger, review, risk, state  # noqa: E402


#: Long enough to be real sentences, because the thing under test is whether
#: two of them can coexist and whether one can replace another.
FIRST = ("WRITERS maps cp/install/ln to LAST_ARG, but GNU cp takes the "
         "destination first under -t, so the slice picks the source.")
SECOND = ("An interpreter one-liner that writes a protected file is parseable, "
          "trips no UNSUPPORTED token, and no interpreter is in WRITERS.")


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                ["git", "config", "user.name", "c"]):
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "thresholds": {"min_chars": 40}}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"review-finding": {"checker": "review-finding", "detector": None,
                            "staleness": "subject", "engagement": True,
                            "question_template": "is {file}::{symbol} closed"},
         "test": {"checker": "test", "question_template": "q",
                  "staleness": "repo"}}))
    (tmp / ".v4" / "checkers.json").write_text("{}")
    (tmp / "mod.py").write_text(
        "WAIT = frozenset()\n\n\ndef helper():\n    return 1\n")
    (tmp / "doc.md").write_text("a document with two things wrong in it\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, capture_output=True)
    return tmp


def _run(fn, args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(args)
    return code, out.getvalue(), err.getvalue()


class _Filing(unittest.TestCase):
    """A repo, a ledger, and one way to file a finding into it."""

    def setUp(self):
        self.root = _repo(self)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)

    def file(self, note, *, path="mod.py", symbol="helper",
             lens="architecture-fit"):
        return review.raise_finding(self.conn, self.cfg, task_id=None,
                                    file=path, symbol=symbol, note=note,
                                    lens=lens)

    def amendments(self, claim_id):
        return [r["payload"] for r in self.conn.execute(
            "SELECT payload FROM event WHERE claim_id = ? AND kind = ? "
            "ORDER BY id", (claim_id, review.AMENDED_KIND))]

    def claims(self):
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM claim ORDER BY created_at, id")]


class OneReadingOfWhatAFindingSays(_Filing):
    """`kernel/review.py::current_note` was one of two answers to one question.

    It applies `finding_note_amended` and every reader of a finding went
    through it. The writer did not: `raise_finding` compared an incoming note
    against `claim.note`, the original row. So the two halves of one module
    disagreed about what a finding said, and the direction that cost most was
    the repair: pasting the original text back matched the row nobody was
    reading, and the command said `already open`.
    """

    def test_the_reader_applies_the_amendment(self):
        cid, created, _siblings = self.file(FIRST)
        self.assertTrue(created)
        review.amend_note(self.conn, claim_id=cid, note=SECOND, actor="agent")
        self.assertEqual(review.current_note(self.conn, cid, FIRST), SECOND)

    def test_and_the_writer_asks_the_reader(self):
        """Re-filing the text as it stands is the same finding, not a second
        no-op amendment reported as `note amended`."""
        cid, _created, _siblings = self.file(FIRST)
        review.amend_note(self.conn, claim_id=cid, note=SECOND, actor="agent")
        again, created, siblings = self.file(SECOND)
        self.assertEqual(again, cid)
        self.assertFalse(created)
        self.assertEqual(siblings, [])
        self.assertEqual(len(self.amendments(cid)), 1,
                         "re-filing the current text recorded an amendment "
                         "of the text to itself")

    def test_putting_a_note_back_is_not_silently_nothing(self):
        """The measured failure, in the shape it happened.

        A mistaken amendment, then the reviewer pasting the original sentence
        back into `review add`. Against `claim.note` that matched, so the
        command wrote nothing and exited 0 with `already open` -- and the
        superseded text went on serving. Nothing here needs the new API: this
        is red at the parent because the old writer read the wrong column.
        """
        cid, _created, _siblings = self.file(FIRST)
        with ledger.writing(self.conn):
            ledger.insert(self.conn, "event", task_id=None, claim_id=cid,
                          kind=review.AMENDED_KIND, actor="worker",
                          payload={"was": FIRST, "now": SECOND},
                          created_at="2026-08-27T09:02:49+00:00")
        back, created, siblings = self.file(FIRST)
        self.assertNotEqual(back, cid, "the original text came back as `already "
                                       "open` and wrote nothing")
        self.assertTrue(created)
        self.assertEqual(siblings, [cid])


class ASecondFindingIsASecondClaim(_Filing):
    """`kernel/review.py::raise_finding` resolved an ambiguity by guessing.

    Two intentions arrive at it looking identical -- "I am correcting what I
    wrote" and "I have found a second thing here" -- and it cannot tell them
    apart from the arguments. It used to guess, and it guessed the destructive
    one: the incoming note replaced the stored one and the older finding
    stopped existing as text.
    """

    def test_the_same_finding_again_is_still_one_finding(self):
        """The property that made a sweep bearable, and that had to survive
        this: finding it again four days later is not a new finding. Green
        before this change as well as after -- it pins the half that must not
        move."""
        first, made, _siblings = self.file(FIRST)
        again, made_again, siblings = self.file(FIRST)
        self.assertEqual(first, again)
        self.assertTrue(made)
        self.assertFalse(made_again)
        self.assertFalse(siblings)
        self.assertEqual(len(self.claims()), 1)

    def test_a_different_finding_gets_a_claim_of_its_own(self):
        first, _made, _siblings = self.file(FIRST)
        second, made, siblings = self.file(SECOND)
        self.assertTrue(made)
        self.assertNotEqual(second, first)
        self.assertEqual(siblings, [first])
        self.assertEqual(len(self.claims()), 2)

    def test_and_the_one_that_was_there_is_untouched(self):
        """What the six lost. An answered finding whose coordinates a later
        sweep files on keeps its text, and no amendment is written about it."""
        first, _made, _siblings = self.file(FIRST)
        self.file(SECOND)
        stored = self.conn.execute("SELECT note FROM claim WHERE id = ?",
                                   (first,)).fetchone()["note"]
        self.assertEqual(stored, FIRST)
        self.assertEqual(review.current_note(self.conn, first, stored), FIRST)
        self.assertEqual(self.amendments(first), [])

    def test_a_correction_is_asked_for_by_name(self):
        """And it is refused for a claim that is not there, rather than
        opening one -- `add` raises findings, `amend` corrects them."""
        with self.assertRaises(review.BadCoordinates):
            review.amend_note(self.conn, claim_id="nosuchclaim", note=SECOND)


class ADocumentIsNotOneFindingWide(_Filing):
    """`resolve_symbol` forces `symbol = ""` for every non-Python file.

    So the claim id of a finding on a document was `(task, kind, file, "",
    lens)` -- the same five values for every finding that lens ever raised
    about that document. Measured: a second `architecture-fit` finding on
    `docs/SPEC.md` came back as `note amended on f44e3ec056e267a2`, and the
    first one stopped being readable anywhere but an event payload.
    """

    def file_doc(self, note):
        return self.file(note, path="doc.md", symbol="")

    def test_two_findings_on_one_document_under_one_lens(self):
        first, made_first, _siblings = self.file_doc(FIRST)
        second, made_second, siblings = self.file_doc(SECOND)
        self.assertTrue(made_first)
        self.assertTrue(made_second, "the second finding was not a claim")
        self.assertNotEqual(first, second)
        self.assertEqual(siblings, [first])

    def test_the_lens_still_names_the_first(self):
        """The slot is a suffix on `variant`, which is where this kernel
        already puts "which finding at this file and symbol"."""
        first, _made, _siblings = self.file_doc(FIRST)
        second, _made2, _sib2 = self.file_doc(SECOND)
        variants = {r["id"]: r["variant"] for r in self.conn.execute(
            "SELECT id, variant FROM claim")}
        self.assertEqual(variants[first], "architecture-fit")
        self.assertEqual(variants[second], "architecture-fit#2")

    def test_the_id_is_still_derived_from_the_row_it_is_stored_in(self):
        """A slot that lived only in the writer's head would make a claim id
        unrecomputable from its own claim, which is the one thing
        `hashing.claim_id` is for."""
        _first, _made, _siblings = self.file_doc(FIRST)
        second, _made2, _sib2 = self.file_doc(SECOND)
        row = self.conn.execute(
            "SELECT task_id, kind, file, symbol, variant FROM claim "
            "WHERE id = ?", (second,)).fetchone()
        self.assertEqual(second, hashing.claim_id(
            row["task_id"], row["kind"], row["file"], row["symbol"] or "",
            row["variant"] or ""))


class ASignatureIsOfferedOnlyWhereItIsTheOnlyExit(unittest.TestCase):
    """`kernel/risk.py::route` read one refusal as though it decided all exits.

    `resolve_symbol` raising means red-green cannot close the finding, and
    `route` turned that into `SIGN_ONLY` -- "signature is the only exit". It is
    not: `review.bind_text_change` and `checkers/review_finding._text_closure`
    close a finding whose repair is a sentence, and they need no symbol at all.
    That is the population text closure was built for, so the constant that
    promises to offer a signature "only where it is the only one" was false
    exactly where it mattered.
    """

    def setUp(self):
        self.root = _repo(self)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)
        ledger.insert(self.conn, "task", id="t-live", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")

    def claim(self, cid, *, file, symbol, kind="review-finding"):
        ledger.insert(self.conn, "claim", id=cid, task_id="t-live", kind=kind,
                      question="q", subject_refs=[], checker=kind,
                      origin="review", file=file, symbol=symbol,
                      created_at="2026")
        return self.conn.execute("SELECT * FROM claim WHERE id = ?",
                                 (cid,)).fetchone()

    def test_a_finding_on_a_document_is_not_down_to_a_signature(self):
        """The `assertNotEqual` first, and on purpose: it is the whole claim,
        and it is answerable without any name this change introduced."""
        row = self.claim("c-doc", file="doc.md", symbol="writing")
        what, why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertNotEqual(what, risk.SIGN_ONLY)
        self.assertIn("--gone", why)
        self.assertEqual(what, risk.CLOSE_TEXT)

    def test_a_value_no_frame_is_named_after_is_not_either(self):
        """Same refusal, other reason. A module constant runs in no frame, and
        the sentence about it still moves in a diff."""
        row = self.claim("c-const", file="mod.py", symbol="WAIT")
        what, _why = risk.route(self.conn, self.cfg, row, state.OPEN)
        self.assertNotEqual(what, risk.SIGN_ONLY)
        self.assertEqual(what, risk.CLOSE_TEXT)

    def test_a_file_this_repo_does_not_carry_is_a_signature(self):
        """The floor. `_text_closure` reads the file at the parent commit and
        at HEAD, so a finding naming a path that is not here has no text to
        move."""
        row = self.claim("c-gone", file="gone/away.md", symbol="thing")
        self.assertEqual(risk.route(self.conn, self.cfg, row, state.OPEN)[0],
                         risk.SIGN_ONLY)

    def test_a_symbol_a_test_can_enter_is_still_a_red_green(self):
        row = self.claim("c-fn", file="mod.py", symbol="helper")
        self.assertEqual(risk.route(self.conn, self.cfg, row, state.OPEN)[0],
                         risk.CLOSE_REDGREEN)


class TheReviewCommandWritesThroughTheLayerThatOwnsTheWrites(unittest.TestCase):
    """`kernel/cli.py::cmd_review` inserted two events into the ledger itself.

    Every other write in this domain is a function in `kernel/review.py` --
    `raise_finding`, `amend_note`, `bind_closing_test`, `bind_text_change`,
    `group`, `defer`, `withdraw_deferral`. `lens_run` and `lens_reviewed`, the
    two newest, were built at the entry surface with their refusals in the
    parser branch, so nothing but argv could produce them and nothing but argv
    was held to the rules around them.
    """

    def setUp(self):
        self.root = _repo(self)
        d = self.root / ".v4" / "lenses"
        d.mkdir(parents=True, exist_ok=True)
        (d / "probe.json").write_text(json.dumps(
            {"name": "probe", "source": "test", "checks": ["one thing"],
             "anti_patterns": ["not another"]}, ensure_ascii=False))

    def args(self, **over):
        a = dict(repo=str(self.root), action="lens", lens=None, task=None,
                 findings=None, file=None, symbol=None, note=None, claim=None,
                 test=None, command=None, parent=None, gone=None, now=None,
                 why=None, target=None, name=None, withdraw=False)
        a.update(over)
        return SimpleNamespace(**a)

    def events(self, kind):
        conn = ledger.connect(self.root)
        try:
            return [json.loads(r["payload"]) for r in conn.execute(
                "SELECT payload FROM event WHERE kind = ? ORDER BY id", (kind,))]
        finally:
            conn.close()

    def test_the_brief_records_its_run_through_that_layer(self):
        real = review.record_lens_run
        seen = []

        def spy(conn, **kw):
            seen.append(kw)
            return real(conn, **kw)

        with mock.patch.object(review, "record_lens_run", spy):
            code, _out, _err = _run(cli.cmd_review,
                                    self.args(action="lens", lens="probe"))
        self.assertEqual(code, 0)
        self.assertEqual([k["slug"] for k in seen], ["probe"])
        self.assertEqual(self.events(review.LENS_RUN_KIND),
                         [{"lens": "probe", "checks": 1}])

    def test_and_so_does_the_reviewer_saying_it_finished(self):
        real = review.record_lens_reviewed
        seen = []

        def spy(conn, root, **kw):
            seen.append(kw)
            return real(conn, root, **kw)

        with mock.patch.object(review, "record_lens_reviewed", spy):
            code, _out, _err = _run(
                cli.cmd_review, self.args(action="done", lens="probe",
                                          findings=0))
        self.assertEqual(code, 0)
        self.assertEqual([k["findings"] for k in seen], [0])
        self.assertEqual(self.events(review.LENS_REVIEWED_KIND),
                         [{"lens": "probe", "findings": 0}])

    def test_the_refusals_belong_to_the_write_and_not_to_argv(self):
        """A caller that is not the command line gets the same two answers."""
        conn = ledger.connect(self.root)
        self.addCleanup(conn.close)
        with self.assertRaises(review.BadCoordinates):
            review.record_lens_reviewed(conn, self.root, slug="probe",
                                        findings=None)
        with self.assertRaises(review.BadCoordinates):
            review.record_lens_reviewed(conn, self.root, slug="nope",
                                        findings=1)
        self.assertEqual(self.events(review.LENS_REVIEWED_KIND), [])

    def test_an_amendment_has_a_command(self):
        """The route the six needed and did not have. It names the claim,
        because a correction is a decision about one finding."""
        conn = ledger.connect(self.root)
        cfg = config.RepoConfig(self.root)
        cid, _made, _sib = review.raise_finding(
            conn, cfg, task_id=None, file="mod.py", symbol="helper",
            note=FIRST, lens="probe")
        conn.close()
        code, out, _err = _run(cli.cmd_review,
                               self.args(action="amend", claim=[cid],
                                         note=SECOND))
        self.assertEqual(code, 0)
        self.assertIn("note amended on", out)
        conn = ledger.connect(self.root)
        self.addCleanup(conn.close)
        self.assertEqual(review.current_note(conn, cid, FIRST), SECOND)

    def test_and_it_refuses_to_amend_nothing(self):
        code, _out, err = _run(cli.cmd_review,
                               self.args(action="amend", note=SECOND))
        self.assertEqual(code, 2)
        self.assertIn("--claim", err)


class ACancelledDeferralIsReadFromOnePlace(unittest.TestCase):
    """`kernel/review.py::withdrawn_deferrals` had no caller anywhere.

    One occurrence in the whole tree, its own `def`, while `deferred()` seven
    lines below carried the identical `WITHDRAWN_KIND` query inline. Two
    implementations of one sentence, one of which nothing exercised -- which is
    not a problem on the day it is written. It is a problem on the day somebody
    changes the copy that runs.
    """

    def setUp(self):
        self.root = _repo(self)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        with ledger.writing(self.conn):
            for cid, kind in (("c-kept", review.DEFER_KIND),
                              ("c-gone", review.DEFER_KIND),
                              ("c-gone", review.WITHDRAWN_KIND)):
                ledger.insert(self.conn, "event", task_id=None, claim_id=cid,
                              kind=kind, actor="agent",
                              payload={"why": "w", "target": "t-42"},
                              created_at="2026")

    def test_the_named_reader_answers_the_question_it_is_named_for(self):
        self.assertEqual(review.withdrawn_deferrals(self.conn), {"c-gone"})

    def test_and_it_is_the_one_the_accounting_reads(self):
        """Not a second copy that happens to agree. Patched to something the
        inline query could never return: if `deferred` still had its own, this
        passes with the wrong set."""
        with mock.patch.object(review, "withdrawn_deferrals",
                               return_value={"c-kept"}) as patched:
            out = review.deferred(self.conn)
        self.assertTrue(patched.called,
                        "`deferred` answered without asking the function that "
                        "owns the question")
        self.assertEqual([cid for cid, _target in out], ["c-gone"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
