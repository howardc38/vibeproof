"""Repairs in `kernel/doctor.py` and `kernel/engagement.py`.

    python3 -m unittest tests.test_the_report_and_the_gate_before_work -v

`doctor` is the command whose whole subject is silent untruth and `engagement`
is the one layer that stops work before it starts. Each of these was a place
where one of them was quiet about itself.

A third subject lived here: the trigger the lens layer never had. That kind
was cut -- 295 runs, 294 skipped, 0 bytes printed, 87 signatures -- and its
two classes went with it. Layer 3 has no trigger again, which is the state
this file used to record being fixed.

All of them fail against 0ad6b61.
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

from kernel import baseline, config, doctor, engagement, install, ledger, sweep  # noqa: E402

sys.path.insert(0, str(ROOT / "checkers"))


def _lens(root):
    """One usable lens -- `review.unusable` names the keys it must carry."""
    d = root / ".v4" / "lenses"
    d.mkdir(exist_ok=True)
    (d / "one.json").write_text(json.dumps(
        {"name": "one", "source": "fixture", "anti_patterns": ["a shape"],
         "checks": [{"id": "a", "ask": "does it?"}]}))


def _repo(case, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


class ACheckThatRaisedUsedToVanish(unittest.TestCase):
    """Five checks in `doctor` were wrapped in `except Exception: pass`.

    A reader scanning fifteen rows has no way to notice a missing one, so the
    sweep freshness read, the path lists, the open-tasks line, the unclosed
    findings line and the deferral reconciliation each disappeared silently
    when they raised -- in the command whose whole subject is silent untruth.
    """

    def test_every_group_reports_a_row(self):
        rows = doctor.run(_repo(self))
        what = {r["what"] for r in rows}
        for name in ("config", "kinds", "open tasks", "deferrals",
                     "ledger", "CI"):
            self.assertIn(name, what, name)

    def test_a_deferral_file_that_does_not_parse_is_a_row_not_a_gap(self):
        root = _repo(self)
        ledger.connect(root).close()
        (root / ".v4" / "deferred").mkdir()
        (root / ".v4" / "deferred" / "beef.json").write_text("{ not json")
        doctor._close_reader()
        rows = {r["what"]: r for r in doctor.run(root)}
        self.assertIn("deferrals", rows)
        self.assertNotEqual(rows["deferrals"]["status"], "ok",
                            "the block was `except Exception: pass`, so the "
                            "row vanished and the report read as clean")

    def test_a_check_that_cannot_run_says_so_rather_than_disappearing(self):
        """The ledger is unreadable here -- there is no git dir for it to live
        in -- and the rows that read it still have to appear."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        rows = doctor.run(tmp)
        self.assertTrue(rows)
        # Every row carries a status a reader can act on, including the ones
        # whose read failed -- the point is that they are still rows.
        for r in rows:
            self.assertTrue(r["status"], r)
            self.assertTrue(r["what"], r)


class EachGroupOfRowsCanBeRunOnItsOwn(unittest.TestCase):
    """`run` was 550 lines emitting 36 rows, navigated by section comments.

    A reader who wanted the CI rows read past nineteen other checks to find
    them, and no group could be exercised without running the whole report.
    """

    def test_a_group_appends_its_own_rows_and_nothing_elses(self):
        root = _repo(self)
        out = []
        doctor._check_config(root, out)
        self.assertEqual({r["what"] for r in out}, {"config"})

    def test_the_report_is_the_groups_and_nothing_else(self):
        root = _repo(self)
        doctor._close_reader()
        whole = {r["what"] for r in doctor.run(root)}
        piece = []
        for fn in (doctor._check_config, doctor._check_ci_can_actually_walk_the_chain,
                   doctor._check_hooks_are_called_and_not_merely_present):
            fn(root, piece)
        self.assertTrue({r["what"] for r in piece} <= whole,
                        sorted({r["what"] for r in piece} - whole))

    def test_and_another_reads_its_own_inputs(self):
        root = _repo(self)
        out = []
        doctor._check_ci_can_actually_walk_the_chain(root, out)
        self.assertTrue(out)
        self.assertNotIn("config", {r["what"] for r in out})


class TheHooksRowAsksTheLedgerNotTheFile(unittest.TestCase):
    """Whether a hook is installed is configuration; whether it fired is a fact.

    The row was headed "hooks" and answered from the files on disk, so a repo
    that had copied the hooks and never run one reported the same as a repo
    where every write went through them.
    """

    def _wired(self):
        root = _repo(self)
        (root / "hooks").mkdir()
        (root / "hooks" / "write_block.py").write_text("x = 1\n")
        (root / ".claude").mkdir()
        (root / ".claude" / "settings.json").write_text(json.dumps(
            {"hooks": {"PreToolUse": [{"command": "hooks/write_block.py"}]}}))
        return root

    def test_a_repo_whose_hooks_never_fired_says_so(self):
        root = self._wired()
        rows = {r["what"]: r for r in doctor.run(root)}
        self.assertIn("hooks", rows)
        self.assertIn("none has", rows["hooks"]["detail"])
        self.assertNotIn("2026-", rows["hooks"]["detail"])

    def test_and_one_where_they_did_names_when(self):
        root = self._wired()
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="hook_seen", actor="worker",
                          payload={"allowed": 1, "basis": "in-scope"},
                          created_at="2026-08-01T00:00:00+00:00")
        rows = {r["what"]: r for r in doctor.run(root)}
        self.assertIn("2026-08-01", rows["hooks"]["detail"],
                      "whether a hook fired is a fact and the row answered "
                      "from the files on disk")


class TheSentenceGateHasSevenCriteria(unittest.TestCase):
    """`judge_text` refuses on seven grounds and SPEC §9 listed six.

    The seventh is the one that stops a sentence the secret scanner reads as a
    live credential: `v4 ship` writes engagement prose verbatim into a
    committed, scanned file, so answering a `secret-chain` claim well could
    make the ledger uncommittable hours later.
    """

    def _judge(self, sentence, subject=("kernel/x.py", "helper")):
        cfg = config.RepoConfig(_repo(self))
        return engagement.judge_text(
            cfg, sentence=sentence, subject_words=set(subject),
            off_subject="names nothing this claim is about")

    def test_a_sentence_carrying_a_credential_shape_is_refused(self):
        ok, why = self._judge(
            "kernel/x.py 個 helper 收一條 URL,例如 "
            # The password half has to look live: each assertion below is that redaction
            # blanks it, or that the engagement gate refuses a sentence carrying it.
            # pragma: allow-secret
            "postgresql://svc:aX9qwLm2Zp7Rt@db.prod/app 呢種形,"
            "而佢冇包住個 token,所以 log 入面見到成條")
        self.assertFalse(ok)
        self.assertIn("credential", why)

    def test_the_same_point_without_a_literal_is_accepted(self):
        """The control: refusing every long sentence would pass the test
        above, and the refusal exists so the point can still be made."""
        ok, why = self._judge(
            "kernel/x.py 個 helper 把一條帶住 userinfo 嘅 URL 原文寫入 log,"
            "而嗰個 URL 嘅密碼部分冇遮過,所以任何讀 log 嘅人都攞到佢")
        self.assertTrue(ok, why)

    def test_an_empty_sentence_is_refused(self):
        self.assertFalse(self._judge("")[0])

    def test_a_short_one_is_refused(self):
        self.assertFalse(self._judge("kernel/x.py 太短")[0])

    def test_one_that_names_nothing_is_refused(self):
        ok, why = self._judge(
            "呢段 code 有問題,應該修好佢,唔係一句夠長就得,要講返佢做緊乜")
        self.assertFalse(ok)


class TheClassifierIsTheOneTheCheckerUses(unittest.TestCase):
    """`_credential_in` returned None on both failure paths.

    So when `secret_patterns` could not be imported, or `analyse_source`
    raised, the gate said "no credential here" -- the answer that lets the
    sentence through, from the half that was supposed to stop it.
    """

    def test_a_plain_sentence_carries_no_credential(self):
        cfg = config.RepoConfig(_repo(self))
        self.assertFalse(engagement._credential_in("just some prose", cfg))

    def test_a_guard_that_cannot_run_says_so_out_loud(self):
        """Returning None is the answer that lets the sentence through, and
        both failure paths returned it silently."""
        import contextlib, io
        root = _repo(self)
        # A table this repo declares and cannot read: `table_for` raises rather
        # than reading it as empty, which is the failure path.
        (root / ".v4" / "secret_patterns.json").write_text("{not json")
        cfg = config.RepoConfig(root)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = engagement._credential_in(
                # The password half has to look live: each assertion below is that redaction
                # blanks it, or that the engagement gate refuses a sentence carrying it.
                # pragma: allow-secret
                "postgres://u:hunter2isnotreal@db.prod/app", cfg)
        self.assertIsNone(got)
        self.assertIn("did not run", err.getvalue())

    def test_the_repos_own_families_are_the_ones_it_judges_by(self):
        """`secret_scan` unions `.v4/secret_patterns.json` in and this did not,
        so an adopter's own families passed the gate and were caught later by
        `secret` -- after the sentence was in an append-only table."""
        root = _repo(self)
        (root / ".v4" / "secret_patterns.json").write_text(json.dumps(
            {"patterns": [{"id": "fixture-token", "label": "a fixture family",
                           "kind": "provider", "order": 900,
                           "regex": r"zzq_[A-Za-z0-9]{20}"}]}))
        cfg = config.RepoConfig(root)
        self.assertTrue(engagement._credential_in(
            "zzq_a7Kq2mZp9Rt4Xw1Bs6Vd", cfg))

    def test_and_a_shape_it_knows_is_found(self):
        cfg = config.RepoConfig(_repo(self))
        self.assertTrue(engagement._credential_in(
            # The password half has to look live: each assertion below is that redaction
            # blanks it, or that the engagement gate refuses a sentence carrying it.
            # pragma: allow-secret
            "postgres://u:hunter2isnotreal@db.prod/app", cfg))


class TheReportOpensTheLedgerOnceThroughTheReadDoor(unittest.TestCase):
    """`run` opened it nine times, each through `ledger.connect`.

    `connect` creates the file, runs SCHEMA, installs the append-only triggers,
    commits and migrates columns -- all of that, nine times, from a command
    whose entire job is to read; and the sweep one kept no reference, so it was
    never closed.
    """

    def test_a_reader_is_shared_across_the_whole_report(self):
        root = _repo(self)
        ledger.connect(root).close()
        opened = []
        real = ledger.connect_readonly

        def counting(r):
            opened.append(str(r))
            return real(r)

        ledger.connect_readonly = counting
        doctor._close_reader()
        try:
            doctor.run(root)
        finally:
            ledger.connect_readonly = real
        self.assertEqual(len(opened), 1, opened)


class WhatAScopedRunCanSayAboutABaseline(unittest.TestCase):
    """"Matches nothing in this scan" is not "matches nothing any more".

    A checker whose subject names one file scans one file, and `partition` then
    reported every accepted id belonging to any other file as stale -- printed
    as `baseline entry <id> matches nothing any more -- delete it`. Measured:
    the first execution of `test-shape` in this ledger's life said that about
    all 22 of its entries while scanning a single test file, and taking the
    advice would have failed the next whole-repo run on all 22.
    """

    def test_a_whole_repo_run_still_names_what_is_gone(self):
        new, carried, stale = baseline.partition(
            ["a", "b"], {"a", "vanished"}, key=lambda f: f)
        self.assertEqual(new, ["b"])
        self.assertEqual(carried, ["a"])
        self.assertEqual(stale, ["vanished"])

    def test_and_a_scoped_one_says_nothing_about_what_it_did_not_look_at(self):
        new, carried, stale = baseline.partition(
            ["a", "b"], {"a", "elsewhere"}, key=lambda f: f, complete=False)
        self.assertEqual(new, ["b"])
        self.assertEqual(carried, ["a"])
        self.assertEqual(stale, [], "the run never looked where `elsewhere` is")


if __name__ == "__main__":
    unittest.main(verbosity=2)
