"""Four things the kernel knew and did not hand over.

    python3 -m unittest tests.test_what_the_kernel_knew_and_dropped -v

An acceptance that threw away the only diagnosis a failing fixture has; a skip
decision assembled and dropped; a staleness reason that named an oracle which
had not moved; and a table whose comment promised the opposite of what its one
reader did.

All of them fail against a807a7a.
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

from kernel import accept, config as config_mod, lifecycle, state  # noqa: E402
from kernel import ledger as ledger_mod  # noqa: E402


class AFailingFixtureSaysWhy(unittest.TestCase):
    """Both fixture steps read `if r.returncode: bad.append(cid)` and dropped
    stdout and stderr, and `cmd_accept` deletes the tree they ran in before the
    summary prints. `_test_detail` in the same module was written for exactly
    that cost."""

    def test_the_detail_names_a_file_that_holds_the_output(self):
        got = accept._fixture_detail(
            "checkers", [("design-pins", "Traceback: boom\n")])
        self.assertIn("design-pins", got)
        self.assertIn("whole output:", got)
        path = Path(got.split("whole output:")[1].strip())
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertIn("boom", path.read_text(encoding="utf-8"))

    def test_and_a_passing_step_leaves_no_file(self):
        """`_test_detail` gives the reason: a file per green `v4 accept` is
        litter that teaches people to ignore the line."""
        self.assertEqual(accept._fixture_detail("checkers", []), "")

    def test_run_itself_carries_it_into_the_row(self):
        """`run` is the symbol, and a helper nothing reaches is not a repair.
        A registry naming a checker that cannot pass its fixtures, put to the
        function `v4 accept` calls."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"probe": {"path": "checkers/probe.py", "sha256": "x",
                       "kinds": ["probe"],
                       "fixtures": "tests/fixtures/nothing_here"}}))
        (tmp / ".v4" / "detectors.json").write_text("{}")
        (tmp / "checkers").mkdir()
        (tmp / "checkers" / "probe.py").write_text("import sys\nsys.exit(5)\n")
        rows = accept.run(tmp, tests=False, fixtures=True, docs=False)
        detail = next(d for step, _ok, d in rows if step == "checkers")
        self.assertIn("probe", detail)
        self.assertIn("whole output:", detail,
                      f"the failing step named nobody and nothing: {detail}")
        path = Path(detail.split("whole output:")[1].strip())
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertTrue(path.read_text(encoding="utf-8").strip(),
                        "the file it points at is empty")


class _Task(unittest.TestCase):
    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        conn = ledger_mod.connect(tmp)
        self.addCleanup(conn.close)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        return tmp, conn


class AnUnmappedExitCodeSaysSo(_Task):
    """`_EXIT_STATE`'s comment says the table exists so a code added to
    `runner` and forgotten here "is one missing row rather than a silent
    fall-through to CHECKER_ERROR". Its one reader was
    `.get(code, CHECKER_ERROR)`."""

    def _state(self, exit_code):
        tmp, conn = self._repo()
        cfg = config_mod.RepoConfig(tmp)
        ledger_mod.insert(conn, "claim", id="c1", task_id="t", kind="probe",
                          question="q", subject_refs="[]", checker="probe",
                          origin="derive", created_at="2026")
        ledger_mod.append_attempt(
            conn, claim_id="c1", subject_digest="{}", checker_sha="s",
            config_sha="c", head_commit="h", worktree="w", argv="[]",
            exit_code=exit_code, stdout="", stderr="", started_at="2026",
            ended_at="2026", duration_ms=1)
        row = conn.execute("SELECT * FROM claim WHERE id = 'c1'").fetchone()
        return state.claim_state(conn, tmp, row, kinds_cfg=cfg.kinds,
                                 config_sha="c",
                                 checker_sha_of=lambda cid: "s",
                                 facts_sha_of=lambda cid: "")

    def test_a_code_the_table_has_no_row_for_is_named(self):
        self.assertEqual(self._state(99), state.UNKNOWN_EXIT)

    def test_and_it_still_holds_the_task(self):
        """The half that must not change: unknown is not terminal, so a claim
        that reached it is still owed."""
        self.assertNotIn(state.UNKNOWN_EXIT, state.TERMINAL)

    def test_and_a_code_the_table_does_have_is_unmoved(self):
        """The control. A reader that answered `UNKNOWN_EXIT` for everything
        would pass both cases above and lose four distinct failures that were
        separated on purpose."""
        self.assertEqual(self._state(6), state.CHECKER_TAMPERED)
        self.assertEqual(self._state(8), state.TIMEOUT)


class TheStalenessReasonSaysWhatMoved(_Task):
    """It compared the whole-file sha of `.v4/config.json` and printed one
    fixed sentence blaming the test command. Three commits in this repo's own
    history edited that file without touching `test_command`."""

    def test_it_does_not_blame_the_test_command_alone(self):
        tmp, conn = self._repo()
        ledger_mod.insert(conn, "claim", id="c1", task_id="t", kind="probe",
                          question="q", subject_refs="[]", checker="probe",
                          origin="derive", created_at="2026")
        ledger_mod.append_attempt(
            conn, claim_id="c1", subject_digest="{}", checker_sha="s",
            config_sha="before", head_commit="h", worktree="w", argv="[]",
            exit_code=0, stdout="ok", stderr="", started_at="2026",
            ended_at="2026", duration_ms=1)
        row = conn.execute("SELECT * FROM claim WHERE id = 'c1'").fetchone()
        att = conn.execute(
            "SELECT * FROM attempt WHERE claim_id = 'c1'").fetchone()
        why = state.stale_reason(conn, tmp, row, att, kinds_cfg={},
                                 config_sha="after",
                                 checker_sha_of=lambda cid: "s",
                                 facts_sha_of=lambda cid: "")
        self.assertIn(".v4/config.json", why)
        self.assertIn("whole file", why,
                      f"the reason still names one key as the cause: {why}")

    def test_and_an_unchanged_config_is_not_stale_for_this_reason(self):
        """The control: a reason produced on every run says nothing."""
        tmp, conn = self._repo()
        ledger_mod.insert(conn, "claim", id="c1", task_id="t", kind="probe",
                          question="q", subject_refs="[]", checker="probe",
                          origin="derive", created_at="2026")
        ledger_mod.append_attempt(
            conn, claim_id="c1", subject_digest="{}", checker_sha="s",
            config_sha="same", head_commit="h", worktree="w", argv="[]",
            exit_code=0, stdout="ok", stderr="", started_at="2026",
            ended_at="2026", duration_ms=1)
        row = conn.execute("SELECT * FROM claim WHERE id = 'c1'").fetchone()
        att = conn.execute(
            "SELECT * FROM attempt WHERE claim_id = 'c1'").fetchone()
        why = state.stale_reason(conn, tmp, row, att, kinds_cfg={},
                                 config_sha="same",
                                 checker_sha_of=lambda cid: "s",
                                 facts_sha_of=lambda cid: "") or ""
        self.assertNotIn(".v4/config.json", why)


if __name__ == "__main__":
    unittest.main()
