"""Repairs in `kernel/hashing.py`, `kernel/state.py` and `kernel/derive.py`.

    python3 -m unittest tests.test_the_kernel_keys_things_the_same_way_twice -v

Every one of these is about a key: what a claim is identified by, what makes a
PASS go stale, and what a signature covers. A key that answers differently in
two places is two answers to "is this still true".

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

from kernel import derive, hashing, state  # noqa: E402


class AClaimIdIsSixtyFourBits(unittest.TestCase):
    """The truncation was there and the odds were not.

    Examined rather than widened: widening renames every claim in every
    existing ledger, and the number is fine -- `n**2 / 2**65` is about 3 in
    10**9 at ten thousand claims.
    """

    def test_the_id_is_sixteen_hex_characters(self):
        got = hashing.claim_id("t", "lint", "a.py", "f", "v")
        self.assertEqual(len(got), 16)
        int(got, 16)

    def test_the_task_is_part_of_it(self):
        """The ledger is shared across worktrees: without the task, the same
        call site touched by two tasks collapses to one claim and one worker's
        PASS silently answers the other's."""
        self.assertNotEqual(hashing.claim_id("t1", "lint", "a.py", "f", "v"),
                            hashing.claim_id("t2", "lint", "a.py", "f", "v"))

    def test_and_the_subject_files_are_not(self):
        """`v4 scope widen` changes them, and the same defect has to keep one
        identity across a widen -- so the id takes five fields and no more."""
        same = hashing.claim_id("t", "lint", "a.py", "f", "v")
        self.assertEqual(same, hashing.claim_id("t", "lint", "a.py", "f", "v"))


class AWorktreeGitCannotAnswerForIsNotEmpty(unittest.TestCase):
    """`tree_state` returns `{}` where git cannot answer, and the digest used
    to hash nothing -- one fixed constant for every such repo, so every
    repo-scoped claim keyed on the same value and none of them ever went
    stale."""

    def _dir(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "a.py").write_text("x = 1\n")
        return tmp

    def test_content_still_moves_the_digest(self):
        tmp = self._dir()
        before = hashing.worktree_digest(tmp)
        (tmp / "a.py").write_text("x = 2\n")
        self.assertNotEqual(before, hashing.worktree_digest(tmp))

    def test_it_is_not_the_hash_of_nothing(self):
        import hashlib
        self.assertNotEqual(hashing.worktree_digest(self._dir()),
                            hashlib.sha256(b"").hexdigest())


class AFailureSaysWhichFailureItWas(unittest.TestCase):
    """`claim_state` collapsed four exit codes into `CHECKER_ERROR`.

    A checker that was tampered with, one whose subject moved, one that timed
    out and one that crashed are four different things to do next, and the
    report said the same word for all of them.
    """

    def _state(self, exit_code):
        root, conn, cfg, row = _repo_with_claim(self, exit_code)
        return state.claim_state(
            conn, root, row, kinds_cfg=cfg.kinds, config_sha="c",
            checker_sha_of=lambda cid: "s", facts_sha_of=lambda cid: "")

    def test_each_exit_code_has_its_own_state(self):
        seen = {self._state(c) for c in (5, 6, 7, 8)}
        self.assertEqual(len(seen), 4, seen)

    def test_and_an_unknown_code_is_still_an_error(self):
        """Still an error, and it says which one it is not.

        This asserted `CHECKER_ERROR`, which is the silent fall-through the
        comment over `_EXIT_STATE` says the table exists to prevent -- "adding a
        code to `runner` and forgetting it here is one missing row rather than a
        silent fall-through to CHECKER_ERROR", of a read that was
        `.get(code, CHECKER_ERROR)`. The intent of this case is unchanged: an
        unknown code is an error, is not terminal, and holds the task. What
        changed is that it no longer borrows a sentence that may be false --
        `CHECKER_ERROR` means "the checker crashed", and a code nobody has
        mapped is not that. The same repair as the one four lines up, where four
        distinct failures used to print as one string.
        """
        got = self._state(99)
        self.assertEqual(got, state.UNKNOWN_EXIT)
        self.assertNotIn(got, state.TERMINAL)
        self.assertNotEqual(got, state.CHECKER_ERROR,
                            "an unmapped code is reported as a crash")


def _repo_with_claim(case, exit_code):
    """A repo, a claim, and one recorded attempt -- enough for `claim_state`."""
    from kernel import config, ledger
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"lint": {"checker": "lint", "question_template": "q",
                  "staleness": "repo"}}))
    conn = ledger.connect(tmp)
    case.addCleanup(conn.close)
    ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                  base_commit="", created_at="2026")
    ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint", question="q",
                  subject_refs="[]", checker="lint", origin="derive",
                  created_at="2026")
    ledger.append_attempt(conn, claim_id="c1", subject_digest="{}",
                          checker_sha="s", config_sha="c", head_commit="h",
                          worktree="w", argv="[]", exit_code=exit_code,
                          stdout="", stderr="", started_at="2026",
                          ended_at="2026", duration_ms=1)
    cfg = config.RepoConfig(tmp)
    row = conn.execute("SELECT * FROM claim WHERE id = 'c1'").fetchone()
    return tmp, conn, cfg, row


class ASignatureAndAPassAreKeyedTheSameWay(unittest.TestCase):
    """SPEC §6 says they use one key so signing does not become cheaper.

    `claim_state` expired a PASS when the facts table moved and
    `_staleness_key` -- what `risk.accept` stores as the cover -- had no facts
    entry at all, so editing `.v4/facts.<repo>.json` turned a PASS stale and
    left the signature standing.
    """

    def test_the_key_carries_the_facts_table(self):
        root, conn, cfg, row = _repo_with_claim(self, 0)
        key = state._staleness_key(
            conn, root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=lambda cid: "s",
            facts_sha_of=lambda cid: "FACTS-A")
        self.assertIn("facts", key)
        self.assertEqual(key["facts"], "FACTS-A")

    def test_and_moving_the_table_moves_the_key(self):
        """The control: a constant in that slot would pass the test above and
        keep a signature standing over a table it no longer covers."""
        root, conn, cfg, row = _repo_with_claim(self, 0)
        a = state._staleness_key(
            conn, root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=lambda cid: "s", facts_sha_of=lambda cid: "A")
        b = state._staleness_key(
            conn, root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
            checker_sha_of=lambda cid: "s", facts_sha_of=lambda cid: "B")
        self.assertNotEqual(a, b)


class AGitFailureIsNotACleanTable(unittest.TestCase):
    """`_filters_matching_nothing` returned `[]` when `git ls-files` failed.

    The caller reads `[]` as "no dead globs" and lets every facts-reading
    detector through -- the same answer as a clean table, from the guard whose
    whole subject is that a filter matching nothing turns a detector off while
    the ship report says it ran.
    """

    def test_a_directory_git_cannot_answer_for_says_so(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        got = derive._filters_matching_nothing(
            tmp, {"entrypoint_globs": ["api/**"]})
        self.assertTrue(got)
        self.assertTrue(got[0].startswith(derive._UNREADABLE_PREFIX), got)

    def test_a_real_repo_with_a_live_glob_reports_nothing(self):
        """The control: reporting a problem always would pass the test above
        and refuse every detector in every repo."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / "api").mkdir()
        (tmp / "api" / "routes.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        self.assertEqual(
            derive._filters_matching_nothing(tmp, {"entrypoint_globs": ["api/**"]}),
            [])

    def test_a_glob_that_matches_nothing_is_still_reported(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        got = derive._filters_matching_nothing(tmp, {"ui_globs": ["web/**"]})
        self.assertTrue(got)
        self.assertIn("web/**", got[0])


class ASymbolReferenceThatNamesNothingIsReported(unittest.TestCase):
    """`SYMBOL_REF_KEYS` sat beside the filter keys and had no reader.

    `public_routes` values are `file::symbol` -- "this handler is deliberately
    unauthenticated" -- so an entry naming a symbol nobody defines exempts
    nothing, and nothing said so.
    """

    def test_a_stale_exemption_is_reported(self):
        from kernel import doctor
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / ".v4" / f"facts.{tmp.name}.json").write_text(json.dumps(
            {"repo": tmp.name, "generated_from_commit": "0" * 40,
             "outbound_write": [{"pattern": ".post", "seen_at": "a.py:1",
                                 "kind": "http"}],
             "outbound_read": [{"pattern": ".get", "seen_at": "a.py:1",
                                "kind": "http"}],
             "auth_decision": [{"pattern": "check", "seen_at": "a.py:1",
                                "kind": "authz"}],
             "entrypoint_globs": ["*.py"], "ui_globs": ["web/**"],
             "config_files": [".v4/config.json"],
             "protected_paths": [".v4/**"],
             "public_routes": ["gone.py::handler"]}))
        (tmp / "a.py").write_text("x = 1\n")
        rows = {r["what"]: r for r in doctor.run(tmp)}
        self.assertIn("symbol refs", rows)
        self.assertIn("gone.py", rows["symbol refs"]["detail"])

    def test_and_it_is_not_treated_as_a_glob(self):
        """It was in `FILTER_KEYS` once, and `fnmatch(path, "a/b.py::f")` is
        false for every file that exists -- so two live exemptions refused
        seven detectors on every task."""
        self.assertNotIn("public_routes", derive.FILTER_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
