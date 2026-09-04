"""A repo-scoped answer keys on what its checker reads, not on the whole tree.

    python3 -m unittest tests.test_an_answer_expires_on_what_it_read -v

`test` runs a suite that takes 300-470 seconds here, and every repo-scoped
claim keyed on a digest of the entire working tree -- so editing `docs/SPEC.md`
or a baseline expired an answer whose oracle reads neither. Measured on one
day's work: roughly fifteen full runs for edits the suite could not see.

`reads` is the field that already answers this. `v4 register` requires it,
`.v4/checkers.json` stores it, and `v4 doctor` already checks it against the
paths a checker names. Nothing new is declared here; the key stops asking a
question nobody meant.

Fails against 0ad6b61.
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

from kernel import config as config_mod, hashing, ledger, state  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"suite": {"question_template": "does the suite pass?", "checker": "suite",
                   "staleness": "repo"}}))
    (tmp / ".v4" / "checkers.json").write_text(json.dumps(
        {"suite": {"path": "checkers/suite.py", "kinds": ["suite"],
                   "sha256": "x", "reads": ["tests/**", "src/**"]}}))
    for rel, body in (("src/app.py", "x = 1\n"), ("tests/test_app.py", "def test_a():\n    pass\n"),
                      ("docs/SPEC.md", "# a document\n")):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "in"], cwd=tmp, capture_output=True)
    return tmp


class TheDigestIsOverWhatTheCheckerReads(unittest.TestCase):
    def test_an_edit_inside_reads_moves_it(self):
        root = _repo(self)
        before = hashing.worktree_digest(root, reads=["tests/**", "src/**"])
        (root / "src" / "app.py").write_text("x = 2\n")
        self.assertNotEqual(hashing.worktree_digest(
            root, reads=["tests/**", "src/**"]), before)

    def test_an_edit_outside_it_does_not(self):
        root = _repo(self)
        before = hashing.worktree_digest(root, reads=["tests/**", "src/**"])
        (root / "docs" / "SPEC.md").write_text("# a different document\n")
        self.assertEqual(hashing.worktree_digest(
            root, reads=["tests/**", "src/**"]), before)

    def test_and_the_whole_tree_still_sees_it(self):
        """The old key, for a checker that declared nothing."""
        root = _repo(self)
        before = hashing.worktree_digest(root)
        (root / "docs" / "SPEC.md").write_text("# a different document\n")
        self.assertNotEqual(hashing.worktree_digest(root), before)


class WhatTheRegistrySays(unittest.TestCase):
    def test_reads_for_returns_the_declaration(self):
        cfg = config_mod.RepoConfig(_repo(self))
        self.assertEqual(cfg.reads_for("suite"), ["tests/**", "src/**"])

    def test_and_none_for_a_checker_the_registry_does_not_hold(self):
        """A checker that declared nothing might read anything, so the safe
        direction there is the whole tree."""
        cfg = config_mod.RepoConfig(_repo(self))
        self.assertIsNone(cfg.reads_for("no-such-checker"))


class AnAnswerSurvivesAnEditItCannotSee(unittest.TestCase):
    """The end-to-end shape: PASS, edit a document, still ANSWERED."""

    def _answered(self, root):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        cfg = config_mod.RepoConfig(root)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-20T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c1", task_id="t1", kind="suite",
                      question="q", subject_refs=[], checker="suite",
                      origin="derive", file=None, symbol=None, variant=None,
                      line=None, note=None, detector=None, detector_sha=None,
                      created_at="2026-08-20T00:00:00+00:00")
        stamp = hashing.worktree_digest(root, reads=cfg.reads_for("suite"))
        ledger.append_attempt(
            conn, claim_id="c1", subject_digest={}, checker_sha="",
            config_sha=cfg.sha, head_commit=stamp, worktree=str(root),
            facts_sha="", argv=["x"], exit_code=0, stdout="", stderr="",
            duration_ms=1, started_at="2026-08-20T00:00:01+00:00",
            ended_at="2026-08-20T00:00:01+00:00")
        conn.commit()
        return conn, cfg

    def _state(self, conn, cfg, root):
        row = conn.execute("SELECT * FROM claim WHERE id = 'c1'").fetchone()
        return state.claim_state(conn, root, row, kinds_cfg=cfg.kinds,
                                 config_sha=cfg.sha,
                                 checker_sha_of=lambda _c: "",
                                 facts_sha_of=lambda _c: "",
                                 reads_of=cfg.reads_for)

    def test_a_document_edit_leaves_the_suite_answered(self):
        root = _repo(self)
        conn, cfg = self._answered(root)
        self.assertEqual(self._state(conn, cfg, root), state.ANSWERED)
        (root / "docs" / "SPEC.md").write_text("# rewritten\n")
        self.assertEqual(self._state(conn, cfg, root), state.ANSWERED,
                         "the suite's oracle does not read docs/")

    def test_and_a_source_edit_expires_it(self):
        root = _repo(self)
        conn, cfg = self._answered(root)
        (root / "src" / "app.py").write_text("x = 999\n")
        self.assertEqual(self._state(conn, cfg, root), state.STALE)

    def test_a_task_report_asks_once_per_declared_read_set(self):
        root = _repo(self)
        conn, cfg = self._answered(root)
        seen = []
        real = hashing.worktree_digest

        def counting(r, exclude=None, reads=None):
            seen.append(tuple(reads or ()))
            return real(r, exclude, reads)

        hashing.worktree_digest = counting
        try:
            state.task_report(conn, root, "t1", kinds_cfg=cfg.kinds,
                              config_sha=cfg.sha,
                              checker_sha_of=lambda _c: "",
                              facts_sha_of=lambda _c: "",
                              reads_of=cfg.reads_for)
        finally:
            hashing.worktree_digest = real
        self.assertEqual(seen, [("tests/**", "src/**")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
