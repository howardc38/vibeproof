"""A gate that refuses the sentence its own refusal asks for.

    python3 -m unittest tests.test_two_tokenisers_that_disagreed -v

`judge` intersects what a sentence names with what the claim is about. The
sentence goes through `WORD`, which stops at a hyphen; the subject split on `/`
and `.` only, so a hyphenated filename stayed one word. The intersection is then
empty for every sentence anybody can write, and the refusal says "name the file
or the symbol" to somebody who just did. A `review-finding` on such a file could
only ever be signed.

Reported by an adopter and reproduced here. It is also already in the file,
attributed to something else: the comment about `dep-provenance` refusing three
sentences in a row "each of which named the actual file" blamed the `<module>`
placeholder. `dep-provenance` is hyphenated.

All of them fail against 15a6940.
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

from kernel import config, engagement, ledger  # noqa: E402

NAMED = ("the launcher at social-ops could not run, and the entry that starts "
         "it reads a path this repo does not have")
UNNAMED = ("this looks fine to me and I have read the rule carefully before "
           "writing down what I think about it")


class _Judged(unittest.TestCase):
    def _judge(self, text, *, file, symbol=""):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"probe": {"checker": "probe", "staleness": "subject",
                       "engagement": True}}))
        (tmp / ".v4" / "checkers.json").write_text("{}")
        cfg = config.RepoConfig(tmp)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r" * 80,
                      scope_globs=["**"], base_commit="x", created_at="2026")
        ledger.insert(conn, "claim", id="c", task_id="t", kind="probe",
                      question="q", subject_refs=[], checker="probe",
                      origin="derive", file=file, symbol=symbol,
                      created_at="2026")
        row = conn.execute("SELECT * FROM claim WHERE id='c'").fetchone()
        return engagement.judge(conn, cfg, claim_row=row, sentence=text)


class AHyphenatedFilenameCanBeNamed(_Judged):
    def test_a_sentence_that_names_it_is_accepted(self):
        ok, why = self._judge(NAMED, file="social-ops")
        self.assertTrue(ok, why)

    def test_and_one_that_does_not_is_still_refused(self):
        """The control, and the reason this cannot be satisfied by dropping the
        subject test: the cheapest way to stop refusing everything is to stop
        asking."""
        ok, why = self._judge(UNNAMED, file="social-ops")
        self.assertFalse(ok)
        self.assertIn("mentions", why)

    def test_the_second_instance_the_file_already_recorded(self):
        """`dep-provenance` is what the comment in that file blames the
        `<module>` placeholder for. The placeholder repair landed; this is why
        it kept happening."""
        ok, why = self._judge(
            "the dep provenance rule reads a manifest this repo does not "
            "carry, so it answers about nothing at all",
            file="dep-provenance")
        self.assertTrue(ok, why)


class AnOrdinaryFilenameIsUnmoved(_Judged):
    """The repair must not loosen the gate for the paths that already worked."""

    def test_a_sentence_that_names_the_file_passes(self):
        ok, why = self._judge(
            "the ledger in this kernel hashes a row before the exporter blanks "
            "it, so the export cannot re-derive what it holds",
            file="kernel/ledger.py")
        self.assertTrue(ok, why)

    def test_and_one_that_names_nothing_is_refused(self):
        ok, why = self._judge(UNNAMED, file="kernel/ledger.py")
        self.assertFalse(ok)

    def test_a_symbol_can_still_be_the_thing_named(self):
        ok, why = self._judge(
            "the function verify_exported walks a file with no database in "
            "reach, and it skipped every event row it was handed",
            file="kernel/ledger.py", symbol="verify_exported")
        self.assertTrue(ok, why)


class ThePlaceholderIsStillNotASubject(_Judged):
    """A repo-scoped claim carries no file and `<module>` for a symbol. If the
    placeholder became nameable, this repair would reopen the hole the comment
    beside it records closing."""

    def test_a_sentence_naming_the_placeholder_is_not_enough(self):
        ok, _why = self._judge(
            "the module here is what this claim is about and I have read the "
            "rule about it before writing this down",
            file="", symbol=engagement.MODULE_SYMBOL)
        self.assertTrue(ok, "a repo-scoped claim has nothing to name, so the "
                             "subject test does not hold it")


if __name__ == "__main__":
    unittest.main()
