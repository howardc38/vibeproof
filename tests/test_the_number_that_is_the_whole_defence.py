"""Repairs in `kernel/scope.py` and `kernel/ledger.py`, called directly.

    python3 -m unittest tests.test_the_number_that_is_the_whole_defence -v

`scope` has no gate on widening on purpose -- SPEC says the defence is that the
number is visible -- so a number that is wrong is the whole defence being
wrong. `ledger` is the other half: the chain is what makes a row nobody can
edit afterwards, and it had two ways to be walked past.

All of these fail against 0ad6b61.
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

from kernel import ledger, scope  # noqa: E402


class WideningEverythingIsNotFourPercentOfAPercent(unittest.TestCase):
    """`scope.usage` divided a count of globs by a count of files."""

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                    ["git", "config", "user.name", "c"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        for name in ("a.py", "b.py", "c.py", "d.py"):
            (tmp / name).write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["a.py"],
                      base_commit="", created_at="2026")
        return tmp, conn

    def _cfg(self, root):
        from kernel import config
        return config.RepoConfig(root)

    def test_widening_to_everything_reads_as_everything(self):
        root, conn = self._repo()
        scope.widen(conn, self._cfg(root), task_id="t", add=["**"],
                    why="w" * 60)
        got = scope.usage(conn, self._cfg(root), "t")
        self.assertGreaterEqual(got["pct"], 99.0,
                                "a glob count over a file count is a category error")

    def test_widening_to_one_file_does_not(self):
        """The control: reporting 100% for any widen at all would pass the
        test above."""
        root, conn = self._repo()
        scope.widen(conn, self._cfg(root), task_id="t", add=["b.py"],
                    why="w" * 60)
        self.assertLess(scope.usage(conn, self._cfg(root), "t")["pct"], 50.0)

    def test_a_virtualenv_is_not_part_of_the_repo(self):
        """The denominator was an `rglob` of everything on disk.

        Measured before the repair: 94,147 paths in 4.5 seconds where the
        tracked set was a few thousand, the difference being `.venv/` and
        `node_modules/` -- so on any adopter with a virtualenv the percentage
        was divided by a number two orders of magnitude too large and
        `over_warn` could never fire.
        """
        root, conn = self._repo()
        (root / ".gitignore").write_text(".venv/\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "ignore"], cwd=root, capture_output=True)
        before = scope.usage(conn, self._cfg(root), "t")["repo_files"]
        (root / ".venv").mkdir()
        for i in range(20):
            (root / ".venv" / f"m{i}.py").write_text("x = 1\n")
        after = scope.usage(conn, self._cfg(root), "t")["repo_files"]
        self.assertEqual(before, after)


class AWidenSaysWhoAskedForIt(unittest.TestCase):
    """`scope.widen` wrote `actor="worker"` as a literal."""

    def test_the_event_records_the_actor_it_was_given(self):
        root, conn = WideningEverythingIsNotFourPercentOfAPercent._repo(self)
        from kernel import config
        scope.widen(conn, config.RepoConfig(root), task_id="t", add=["b.py"],
                    why="w" * 60)
        row = conn.execute("SELECT payload FROM event "
                           "WHERE kind = 'scope_widen' ORDER BY id DESC").fetchone()
        payload = json.loads(row["payload"])
        self.assertIn("who", payload, "the event that grants write permission "
                                      "recorded no identity at all")
        self.assertIn("asked_by", payload)

    _repo = WideningEverythingIsNotFourPercentOfAPercent._repo


class AProtectedPathIsReachedNotSpelled(unittest.TestCase):
    """`scope._matches` compared the widen glob against the protected glob."""

    def test_a_broad_glob_reaches_a_protected_path(self):
        root, conn = WideningEverythingIsNotFourPercentOfAPercent._repo(self)
        (root / ".v4" / "extra.json").write_text("{}")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "p"], cwd=root, capture_output=True)
        from kernel import config
        hit = scope._reaches_protected(root, ["**"], (".v4/**",))
        self.assertTrue(hit, "`--add **` reaches `.v4/**` and read as no hit")

    def test_a_narrow_one_does_not(self):
        """The control: a rule that reports every glob would pass the test
        above."""
        root, _conn = self._repo()
        self.assertEqual(scope._reaches_protected(root, ["docs/**"], (".v4/**",)), [])

    _repo = WideningEverythingIsNotFourPercentOfAPercent._repo


class AnExportCarriesTheAnchorForItsOwnRows(unittest.TestCase):
    """`ledger.verify_exported` walked forward and could not see a truncation."""

    def _exported(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for i in range(3):
            ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
            ledger.append_attempt(conn, claim_id=f"c{i}", subject_digest="d",
                                  checker_sha="s", config_sha="c", head_commit="h",
                                  worktree="w", argv="[]", exit_code=0, stdout="",
                                  stderr="", started_at="2026", ended_at="2026",
                                  duration_ms=1)
        out = tmp / "export.jsonl"
        ledger.export_jsonl(conn, out, tmp)
        return out

    def test_a_whole_export_walks(self):
        n, problems = ledger.verify_exported(self._exported())
        self.assertEqual(problems, [])
        self.assertEqual(n, 3)

    def test_rows_cut_off_the_end_are_seen(self):
        """A shorter chain is internally perfect, which is why this needs the
        anchor travelling in the same file."""
        path = self._exported()
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        anchor = [l for l in lines if "_chain_head" in l]
        body = [l for l in lines if "_chain_head" not in l]
        path.write_text("\n".join(body[:-1] + anchor) + "\n")
        _n, problems = ledger.verify_exported(path)
        self.assertTrue(problems, "a truncated export walked clean")

    def test_an_export_with_no_anchor_says_so(self):
        path = self._exported()
        lines = [l for l in path.read_text().splitlines()
                 if l.strip() and "_chain_head" not in l]
        path.write_text("\n".join(lines) + "\n")
        _n, problems = ledger.verify_exported(path)
        self.assertTrue(any("no chain anchor" in p for p in problems), problems)


class TheAnchorIsConsultedWhenTheCountMatches(unittest.TestCase):
    """`ledger.audit_chain` compared `head_hash` only when the counts differed.

    So swapping a row and recomputing its hash -- count unchanged -- walked
    past the anchor entirely. The anchor exists for the case the forward walk
    cannot see, and this was the other half of it.
    """

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for i in range(2):
            ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
            ledger.append_attempt(conn, claim_id=f"c{i}", subject_digest="d",
                                  checker_sha="s", config_sha="c", head_commit="h",
                                  worktree="w", argv="[]", exit_code=0, stdout="",
                                  stderr="", started_at="2026", ended_at="2026",
                                  duration_ms=1)
        return tmp, conn

    def _anchor(self, root, conn):
        (root / ".v4").mkdir(exist_ok=True)
        row = conn.execute("SELECT id, row_hash FROM attempt "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        n = conn.execute("SELECT count(*) n FROM attempt").fetchone()["n"]
        return root / ".v4" / "chain_head.json", {
            "attempts": n, "last_id": row["id"], "head_hash": row["row_hash"]}

    def test_an_untouched_chain_walks(self):
        root, conn = self._repo()
        path, body = self._anchor(root, conn)
        path.write_text(json.dumps(body))
        ok, problems = ledger.audit_chain(conn, root)
        self.assertTrue(ok, problems)

    def test_a_head_that_does_not_match_the_anchor_is_reported(self):
        root, conn = self._repo()
        path, body = self._anchor(root, conn)
        body["head_hash"] = "0" * 64               # same count, different chain
        path.write_text(json.dumps(body))
        ok, problems = ledger.audit_chain(conn, root)
        self.assertFalse(ok, "same count, different head, and it walked clean")
        self.assertTrue(any("same count" in p for p in problems), problems)


class TheExportRedactsWhatTheLedgerCarries(unittest.TestCase):
    """`export_jsonl` filtered two columns of free text and shipped the rest."""

    def test_free_text_columns_are_named(self):
        for table, fields in (("event", ("payload",)),
                              ("accepted_risk", ("why",)),
                              ("claim", ("note", "question")),
                              ("task", ("request",))):
            self.assertEqual(ledger._EXPORT_REDACTED.get(table), fields, table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
