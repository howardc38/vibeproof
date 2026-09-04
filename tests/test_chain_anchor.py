"""A ledger rebuilt with the same attempt count used to verify clean.

    python3 -m unittest tests.test_chain_anchor -v

`audit_chain`'s walk cannot tell a real chain from a rebuilt one, and says so:
every row's `row_hash` is derived from that row's own contents, so a ledger that
was deleted and re-appended is internally perfect at every link.  The anchor in
`.v4/chain_head.json` is the only record outside the database, and it exists for
exactly this -- `chain_head_path`'s own docstring says a rebuild "disagrees more
loudly" with it.

It did not disagree at all.  `head_hash` was compared in the `count >` branch
and nowhere else, so a forgery that kept the count never reached a comparison.

Reproduced before the fix, and that reproduction is the first test below: three
attempts, anchor written, triggers dropped, `DELETE FROM attempt`, three rows
re-appended with `stdout = "FORGED"`.  The anchor recorded head `c68f5d31…`,
the database held `0009d1c6…`, and `audit_chain` returned `(True, [])`.

This is what `v4 audit` runs and what the chain argument in SPEC.md rests on, so
the three cases are each asserted rather than assumed: the forgery is caught,
growth since the anchor is not called one, and truncation is still caught by the
branch that already worked.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as LG  # noqa: E402


def _repo(case, n_attempts: int, stdout_prefix: str):
    """A git repo with a real ledger holding `n_attempts` honest attempts.

    Both the directory and the connection go to `case.addCleanup`.  The first
    version called `tempfile.mkdtemp()` with no cleanup at all -- one leaked
    directory per test -- which a monitor session reading `test-sufficiency`
    reported against this file twenty minutes after it was written.

    An `ExitStack` was tried in between and was worse: the stack closed before
    the test body ran, so the tree was deleted while SQLite still held the
    handle and the tests passed against a directory that no longer existed.  A
    cleanup that runs too early is not a smaller leak, it is a green test with
    no subject.
    """
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
    (root / ".v4").mkdir(parents=True, exist_ok=True)
    conn = LG.connect(root)
    case.addCleanup(conn.close)
    with LG.writing(conn):
        LG.insert(conn, "task", id="t", request="r", scope_globs='["**"]',
                  base_commit="x", created_at="2026")
        for i in range(n_attempts):
            LG.insert(conn, "claim", id=f"c{i}", task_id="t", kind="k",
                      question="q", subject_refs="[]", checker="ch",
                      origin="derive", created_at="2026")
    for i in range(n_attempts):
        _append(conn, f"c{i}", f"{stdout_prefix} {i}")
    return root, conn


def _append(conn, claim_id: str, stdout: str):
    # `subject_digest` is JSON: `state.claim_state` does `json.loads` on it
    # to compare against a fresh digest, so a bare string raises there
    # rather than in the chain walk this file is about.
    LG.append_attempt(conn, claim_id=claim_id, subject_digest="{}",
                      checker_sha="s", config_sha="cf", head_commit="hc",
                      worktree="w", argv="[]", exit_code=0, stdout=stdout,
                      stderr="", started_at="2026", ended_at="2026",
                      duration_ms=1,
                      claim_digest=LG.claim_digest(conn, claim_id),
                      facts_sha="")


def _drop_triggers(conn):
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


def _rebuild(conn, n: int, stdout_prefix: str):
    """Drop the triggers, delete every attempt, re-append the same number.

    What somebody with write access to `.git/v4/ledger.db` can do, written out
    so the test asserts against the actual move rather than a stand-in.
    """
    _drop_triggers(conn)
    conn.execute("DELETE FROM attempt")
    conn.commit()
    for i in range(n):
        _append(conn, f"c{i}", f"{stdout_prefix} {i}")


class ARebuiltLedgerIsCaught(unittest.TestCase):
    def test_same_count_different_chain_is_refused(self):
        root, conn = _repo(self, 3, "real output")
        LG.write_chain_head(conn, root)
        anchor = json.loads(LG.chain_head_path(root).read_text())
        self.assertTrue(LG.audit_chain(conn, root)[0], "honest chain")

        _rebuild(conn, 3, "FORGED")

        head = conn.execute(
            "SELECT row_hash FROM attempt ORDER BY id DESC LIMIT 1").fetchone()[0]
        count = conn.execute("SELECT count(*) FROM attempt").fetchone()[0]
        self.assertEqual(count, anchor["attempts"], "the forgery keeps the count")
        self.assertNotEqual(head, anchor["head_hash"], "and changes the chain")
        self.assertEqual(
            [r[0] for r in conn.execute("SELECT stdout FROM attempt ORDER BY id")],
            ["FORGED 0", "FORGED 1", "FORGED 2"],
            "and every recorded output is now the forger's")

        ok, problems = LG.audit_chain(conn, root)
        self.assertFalse(ok, "a rebuilt ledger must not verify clean")
        self.assertTrue(any("rebuilt" in p for p in problems), problems)

    def test_the_walk_alone_still_cannot_tell(self):
        """Without the anchor there is nothing to compare against, and the
        message says so rather than passing quietly."""
        root, conn = _repo(self, 3, "real output")
        LG.write_chain_head(conn, root)
        _rebuild(conn, 3, "FORGED")
        LG.chain_head_path(root).unlink()
        ok, problems = LG.audit_chain(conn, root)
        self.assertFalse(ok)
        self.assertTrue(any("chain_head.json" in p for p in problems), problems)


class HonestLedgersStillPass(unittest.TestCase):
    def test_growth_since_the_anchor_is_not_a_forgery(self):
        """The anchor is refreshed after each run, so every ship walks a chain
        longer than the one recorded.  Calling that tampering would make the
        check fire on every honest repo, which is the same as not having it."""
        root, conn = _repo(self, 3, "real")
        LG.write_chain_head(conn, root)
        with LG.writing(conn):
            LG.insert(conn, "claim", id="c9", task_id="t", kind="k",
                      question="q", subject_refs="[]", checker="ch",
                      origin="derive", created_at="2026")
        _append(conn, "c9", "a new real run")
        ok, problems = LG.audit_chain(conn, root)
        self.assertTrue(ok, problems)

    def test_an_untouched_ledger_passes(self):
        root, conn = _repo(self, 3, "real")
        LG.write_chain_head(conn, root)
        ok, problems = LG.audit_chain(conn, root)
        self.assertTrue(ok, problems)


class TruncationIsStillCaught(unittest.TestCase):
    def test_rows_removed_from_the_end(self):
        """The branch that already worked, asserted so the reordering above
        cannot have silently taken it out."""
        root, conn = _repo(self, 3, "real")
        LG.write_chain_head(conn, root)
        _drop_triggers(conn)
        conn.execute("DELETE FROM attempt WHERE id = 3")
        conn.commit()
        ok, problems = LG.audit_chain(conn, root)
        self.assertFalse(ok)
        self.assertTrue(any("removed from the end" in p for p in problems),
                        problems)



class ABrokenChainHoldsTheShip(unittest.TestCase):
    """`ship`'s predicate is `(not blocked) and chain_ok and not unconfirmed`,
    and dropping `chain_ok` from it left all 777 tests green.

    Reported by a monitor session reading `test-sufficiency`, which mutated the
    line and ran the suite rather than reading it.

    Isolating `chain_ok` is the whole difficulty, and the first attempt at this
    test did not: it broke a row belonging to the task being shipped, so the
    claim went STALE, `blocked` was non-empty, and `assertFalse(ok)` passed
    whether or not the predicate consulted the chain at all. A test that passes
    for the wrong reason is the shape this file exists to catch, one level in.

    So the task under test carries no claims -- `blocked` and `unconfirmed` are
    both empty by construction, and `chain_ok` is the only term left. The
    damaged attempt belongs to a *different* task, which `state.task_report`
    scopes out and `ledger.audit_chain` does not.
    """

    def _two_tasks(self):
        from kernel import config
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text("{}")
        (root / "detectors").mkdir()
        conn = LG.connect(root)
        self.addCleanup(conn.close)
        with LG.writing(conn):
            # The one being shipped. No claims, so `blocked` is empty.
            LG.insert(conn, "task", id="t", request="r", scope_globs='["**"]',
                      base_commit="", created_at="2026")
            # Somebody else's, whose attempts are what the chain walk reads.
            LG.insert(conn, "task", id="other", request="r",
                      scope_globs='["**"]', base_commit="", created_at="2026")
            for i in range(2):
                LG.insert(conn, "claim", id=f"o{i}", task_id="other", kind="k",
                          question="q", subject_refs="[]", checker="ch",
                          origin="derive", created_at="2026")
        for i in range(2):
            _append(conn, f"o{i}", f"real {i}")
        LG.write_chain_head(conn, root)
        return conn, config.RepoConfig(root), root

    def test_an_intact_chain_ships(self):
        """The control. Without it, a predicate that always refuses would pass
        both tests below."""
        from kernel import lifecycle
        conn, cfg, root = self._two_tasks()
        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertEqual(rep["blocked"], [], "chain_ok must be the only term")
        self.assertEqual(rep["facts_unconfirmed"], [])
        self.assertTrue(rep["chain_ok"], rep.get("chain_problems"))
        self.assertTrue(ok, rep)

    def test_a_row_edited_in_place_holds_it(self):
        """The failure the chain exists to catch: a recorded verdict rewritten
        afterwards, in a task this ship is not about."""
        from kernel import lifecycle
        conn, cfg, root = self._two_tasks()
        _drop_triggers(conn)
        conn.execute("UPDATE attempt SET stdout = 'edited afterwards' WHERE id = 1")
        conn.commit()

        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertEqual(rep["blocked"], [], "nothing else may be holding it")
        self.assertFalse(rep["chain_ok"], "the walk must see the edit")
        self.assertFalse(ok, "and ship must refuse on chain_ok alone")

    def test_a_rebuilt_ledger_holds_it_too(self):
        """Same count, different chain -- caught by the anchor rather than by
        the walk, and it has to reach the same verdict."""
        from kernel import lifecycle
        conn, cfg, root = self._two_tasks()
        _drop_triggers(conn)
        conn.execute("DELETE FROM attempt")
        conn.commit()
        for i in range(2):
            _append(conn, f"o{i}", f"FORGED {i}")

        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertEqual(rep["blocked"], [])
        self.assertFalse(rep["chain_ok"], rep.get("chain_problems"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
