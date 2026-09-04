"""The two halves of the engagement gate that refused true sentences.

    python3 -m unittest tests.test_what_the_sentence_gate_can_and_cannot_tell -v

Both were the same mistake in different places: a measure aimed at one failure
was pointed at a case it cannot decide, and refused real work.

* the duplicate corpus was every sentence ever written, so meeting the same
  claim again -- reopened after an abandon -- collided with the worker's own
  earlier answer about the same code;
* the recitation floor was borrowed from `dup_threshold`, and a rule short
  enough to be a question can only be answered in its own nouns.

Both fail against 0ad6b61.
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

from kernel import config as config_mod, engagement, ledger  # noqa: E402

#: 14 tokens, and a question: the shape that cannot be answered without using
#: its own nouns.
SHORT_RULE = "環境變數注入係平台要求,定係方便?"
TRUE_ANSWER = ("呢個 secret 由環境變數注入,係平台要求嚟,唔係方便 —— "
               "Cloud Run 唔畀寫檔案。")


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"secret": {"question_template": "does {file} hold a credential?",
                    "checker": "secret", "staleness": "subject",
                    "engagement": True,
                    "rule": [{"text": SHORT_RULE}]}}))
    return tmp


class AReproductionAndAnAnswer(unittest.TestCase):
    """Padding cannot lower containment -- which is why containment was chosen,
    and why the line that holds is total reproduction."""

    def test_a_copy_is_refused(self):
        self.assertGreaterEqual(engagement.recited(SHORT_RULE, [SHORT_RULE]),
                                engagement.RECITED_FLOOR)

    def test_a_copy_with_padding_is_refused(self):
        for tail in (" 呢個好重要要小心處理",
                     " 呢個好重要要小心處理再三檢查唔可以求其算數要認真對待"):
            with self.subTest(tail=tail):
                self.assertGreaterEqual(
                    engagement.recited(SHORT_RULE + tail, [SHORT_RULE]),
                    engagement.RECITED_FLOOR)

    def test_and_a_true_answer_to_a_question_shaped_rule_is_not(self):
        got = engagement.recited(TRUE_ANSWER, [SHORT_RULE])
        self.assertLess(got, engagement.RECITED_FLOOR)
        self.assertGreater(got, 0.8, "and it is over the old borrowed floor, "
                                     "which is why it used to be refused")

    def test_the_floor_is_its_own_number(self):
        """Borrowing `dup_threshold` is what pointed one question's answer at
        another question."""
        cfg = config_mod.RepoConfig(_repo(self))
        self.assertNotEqual(engagement.RECITED_FLOOR,
                            cfg.thresholds["dup_threshold"])


class TheSameClaimMetTwice(unittest.TestCase):
    """V3's failure was one sentence across claims that had nothing to do with
    each other. A worker meeting the same claim again is the opposite case."""

    def _seed(self, root, *, kind, file, symbol, sentence):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t-old", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-01T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c-old", task_id="t-old", kind=kind,
                      question="q", subject_refs=[], checker=kind,
                      origin="derive", file=file, symbol=symbol, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at="2026-08-01T00:00:00+00:00")
        ledger.insert(conn, "event", task_id="t-old", claim_id="c-old",
                      kind="engagement", actor="worker",
                      payload={"verdict": "accepted", "sentence": sentence},
                      created_at="2026-08-01T00:00:01+00:00")
        conn.commit()
        return conn

    def _row(self, conn, *, kind, file, symbol):
        ledger.insert(conn, "task", id="t-new", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-20T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c-new", task_id="t-new", kind=kind,
                      question="q", subject_refs=[], checker=kind,
                      origin="derive", file=file, symbol=symbol, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at="2026-08-20T00:00:00+00:00")
        conn.commit()
        return conn.execute("SELECT * FROM claim WHERE id = 'c-new'").fetchone()

    def test_the_same_sentence_for_the_same_code_is_allowed_again(self):
        root = _repo(self)
        said = ("kernel/x.py 個 handler 收唔到 upstream 嘅答案就靜靜雞 return "
                "None,而 caller 分唔開「冇嘢」同「問唔到」")
        conn = self._seed(root, kind="secret", file="kernel/x.py",
                          symbol="handler", sentence=said)
        row = self._row(conn, kind="secret", file="kernel/x.py", symbol="handler")
        ok, why = engagement.judge(conn, config_mod.RepoConfig(root),
                                   claim_row=row, sentence=said)
        self.assertTrue(ok, why)

    def test_but_not_for_a_different_claim(self):
        root = _repo(self)
        said = ("kernel/x.py 個 handler 收唔到 upstream 嘅答案就靜靜雞 return "
                "None,而 caller 分唔開「冇嘢」同「問唔到」")
        conn = self._seed(root, kind="secret", file="kernel/x.py",
                          symbol="handler", sentence=said)
        row = self._row(conn, kind="secret", file="kernel/other.py",
                        symbol="different")
        ok, why = engagement.judge(conn, config_mod.RepoConfig(root),
                                   claim_row=row, sentence=said)
        self.assertFalse(ok)
        self.assertIn("repeat", why)

    def test_the_corpus_leaves_out_only_this_claims_coordinates(self):
        root = _repo(self)
        conn = self._seed(root, kind="secret", file="kernel/x.py",
                          symbol="handler", sentence="a sentence about x")
        row = self._row(conn, kind="secret", file="kernel/x.py", symbol="handler")
        self.assertEqual(engagement.history(conn), ["a sentence about x"])
        self.assertEqual(engagement.history(conn, unlike=row), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
