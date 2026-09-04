"""`kernel/` is shared, so a table in it is not a table an adopter can edit.

    python3 -m unittest tests.test_a_repo_can_widen_the_rule_it_is_judged_by -v

`fail_closed`'s RISK REGISTRY held 156 literals and its header said "add a row
rather than a branch" -- an instruction only this repo could follow, because
`kernel/` is pointed at, never copied (SPEC §8.8). `secret_patterns.py` made
the same move earlier and wrote down why: "adding them meant editing the
kernel. A regex table is data."

Union, never replacement, for the reason `protected_paths` gives: a repo that
declares three transports of its own must not thereby lose the ten it did not
have to think about.

All of it fails against 0ad6b61.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import fail_closed  # noqa: E402

#: A swallow whose call the shipped table does not name.
UNKNOWN_TRANSPORT = ("def go(client, body):\n"
                     "    try:\n"
                     "        client.zap(body)\n"
                     "    except Exception:\n"
                     "        pass\n")


def _repo(case, rows=None):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    (tmp / ".v4").mkdir()
    if rows is not None:
        (tmp / fail_closed.VOCABULARY_FILE).write_text(json.dumps(rows))
    return tmp


class ARepoNamesItsOwnTransports(unittest.TestCase):
    def test_the_shipped_table_does_not_know_it(self):
        self.assertEqual(fail_closed.analyse_source(
            UNKNOWN_TRANSPORT, path="x.py"), [])

    def test_and_a_repo_that_declares_it_is_judged_on_it(self):
        root = _repo(self, {"vocabulary": {"outbound_tails_weak": ["zap"]}})
        got = fail_closed.analyse_source(
            UNKNOWN_TRANSPORT, path="x.py",
            vocab=fail_closed.vocabulary_for(root))
        self.assertEqual([(f.symbol, f.variant) for f in got], [("go", "swallow")])

    def test_declaring_one_keeps_the_ten_it_did_not_write(self):
        root = _repo(self, {"vocabulary": {"outbound_roots": ["ourtransport"]}})
        vocab = fail_closed.vocabulary_for(root)
        self.assertIn("requests", vocab.outbound_roots)
        self.assertIn("ourtransport", vocab.outbound_roots)
        self.assertEqual(vocab.outbound_tails_strong,
                         fail_closed.SHIPPED.outbound_tails_strong)

    def test_a_repo_that_declares_nothing_gets_the_shipped_table(self):
        self.assertIs(fail_closed.vocabulary_for(_repo(self)),
                      fail_closed.SHIPPED)

    def test_a_table_that_does_not_read_is_refused_not_ignored(self):
        """Falling back would judge a repo by a narrower rule than it asked
        for, and nothing would say so -- `secret_patterns.table_for` refuses
        the same way."""
        root = _repo(self)
        (root / fail_closed.VOCABULARY_FILE).write_text("{ not json")
        with self.assertRaises(Exception):
            fail_closed.vocabulary_for(root)

    def test_an_auth_word_a_repo_adds_is_an_auth_decision(self):
        root = _repo(self, {"vocabulary": {"auth_words": ["entitlement"]}})
        vocab = fail_closed.vocabulary_for(root)
        self.assertIsNone(fail_closed.classify_call("check_entitlement"))
        self.assertTrue(fail_closed.classify_call("check_entitlement", vocab))


class WhatStaysInTheKernel(unittest.TestCase):
    """Facts about Python, not about a repo's vocabulary.

    An adopter redefining what `sys.exit` does is not extending a rule; it is
    turning one off, and the file says so where the line is drawn.
    """

    def test_the_control_flow_tables_are_not_extendable(self):
        for name in ("outbound_roots", "auth_words", "receiver_hints"):
            self.assertIn(name, fail_closed.Vocabulary.__dataclass_fields__)
        for name in ("terminating_bare", "falsey_constants",
                     "predicate_prefixes"):
            self.assertNotIn(name, fail_closed.Vocabulary.__dataclass_fields__)

    def test_and_a_repo_cannot_reach_them_through_the_file(self):
        root = _repo(self, {"vocabulary": {"terminating_bare": ["log"]}})
        vocab = fail_closed.vocabulary_for(root)
        self.assertFalse(hasattr(vocab, "terminating_bare"))
        self.assertIn("exit", fail_closed.TERMINATING_BARE)


class MovingTheRuleOutOfCodeDidNotMoveItOutOfTheKey(unittest.TestCase):
    """A program is its code *and* the table beside it.

    `program_sha` follows imports, and imports resolve to `.py` and
    `__init__.py` -- so moving 156 literals into a JSON file took the rule out
    of the hash that decides whether a previous answer still stands. Measured:
    editing `fail_closed.py` moved the sha and editing `fail_closed.json` did
    not, while `fail-closed` is subject-scoped, so the worktree digest does not
    cover it either. Every claim answered under the old vocabulary would have
    stayed answered under a new one.
    """

    def test_the_table_beside_a_module_is_part_of_its_program(self):
        import shutil
        from kernel import hashing
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "kernel" / "analysis").mkdir(parents=True)
        (tmp / "kernel" / "__init__.py").write_text("")
        (tmp / "kernel" / "analysis" / "__init__.py").write_text("")
        (tmp / "kernel" / "analysis" / "rule.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "TABLE = json.loads(Path(__file__).with_suffix('.json').read_text())\n")
        table = tmp / "kernel" / "analysis" / "rule.json"
        table.write_text('{"names": ["one"]}')
        (tmp / "checkers").mkdir()
        entry = tmp / "checkers" / "thing.py"
        entry.write_text("from kernel.analysis import rule\n")

        before = hashing.program_sha(tmp, entry)
        (tmp / "kernel" / "analysis" / "rule.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "TABLE = json.loads(Path(__file__).with_suffix('.json').read_text())\n"
            "# and a comment\n")
        self.assertNotEqual(hashing.program_sha(tmp, entry), before)

        (tmp / "kernel" / "analysis" / "rule.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "TABLE = json.loads(Path(__file__).with_suffix('.json').read_text())\n")
        self.assertEqual(hashing.program_sha(tmp, entry), before)
        table.write_text('{"names": ["one", "two"]}')
        self.assertNotEqual(hashing.program_sha(tmp, entry), before,
                            "the rule moved into data and out of the key")

    def test_this_repos_own_checker_answers_to_its_table(self):
        from kernel import hashing
        js = ROOT / "kernel" / "analysis" / "fail_closed.json"
        entry = ROOT / "checkers" / "fail_closed.py"
        body = js.read_text()
        before = hashing.program_sha(ROOT, entry)
        try:
            js.write_text(body.replace('"requests"', '"requests_probe"'))
            self.assertNotEqual(hashing.program_sha(ROOT, entry), before)
        finally:
            js.write_text(body)
        self.assertEqual(hashing.program_sha(ROOT, entry), before)


class TheNamesTheDocumentationUsesStillResolve(unittest.TestCase):
    """`checkers/facts_coverage.py` cites two of these by name and this
    module's own docstring cites four; the names stayed, the values moved."""

    def test_every_cited_name_is_the_shipped_value(self):
        for name in ("OUTBOUND_ROOTS", "RECEIVER_HINTS", "HTTP_VERB_TAILS",
                     "AUTH_WORDS", "OUTBOUND_PREFIXES", "PATH_HINTS"):
            self.assertTrue(getattr(fail_closed, name), name)
        self.assertEqual(fail_closed.OUTBOUND_ROOTS,
                         fail_closed.SHIPPED.outbound_roots)

    def test_the_shipped_table_is_the_json_beside_the_module(self):
        rows = json.loads(
            (ROOT / "kernel" / "analysis" / "fail_closed.json").read_text())
        self.assertEqual(sorted(rows["vocabulary"]["outbound_roots"]),
                         sorted(fail_closed.SHIPPED.outbound_roots))


if __name__ == "__main__":
    unittest.main(verbosity=2)
