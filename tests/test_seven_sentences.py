"""Sentences that described a repo other than this one.

    python3 -m unittest tests.test_seven_sentences -v

Three of the seven have something to run against; the rest are prose, repaired
in the file that carries them and closed as text.

  63e4704b  the only check that asks whether a session-facing document names a
            mechanism that exists read none of the four documents that *are* a
            role's system prompt
  0223cc43  the `test` kind's first engagement rule tells every worker to reason
            from a premise that is false here -- that `test_command` carries a
            marker filter
  0bddcfc1  where the runtime probe writes and where the checker reads it back
            were two spellings of one expression, and they have already drifted
            once in this repo's history
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.test_a_brief_names_a_mechanism_that_exists as briefs  # noqa: E402


class EveryRolePromptIsRead(unittest.TestCase):
    """63e4704b -- the four documents a role is instantiated with.

    `spec_coverage.user_facing_docs` does read them, for `launcher_is_reachable`
    and `flags_resolve` -- and `flags_resolve` says in its own docstring that it
    is "shape only: the flag has to be declared for that subcommand". So a role
    prompt could name a command that parses and still cannot run, and one did.
    """

    def test_the_role_definitions_are_in_the_set(self):
        for rel in sorted((ROOT / ".claude" / "agents").glob("*.md")):
            with self.subTest(role=rel.name):
                self.assertIn(f".claude/agents/{rel.name}", briefs.BRIEFS)

    def test_and_the_two_commands_that_instantiate_them(self):
        for rel in ("run.md", "wave.md", "sweep.md"):
            self.assertIn(f".claude/commands/{rel}", briefs.BRIEFS)

    def test_every_brief_is_a_file_that_is_here(self):
        """A path in the tuple that is not on disk would make the checks it
        drives vacuous rather than red."""
        for rel in briefs.BRIEFS:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_a_lens_is_a_name_this_system_has(self):
        """Found by widening the tuple: `reviewer.md` names `near-miss`, which
        is a lens here, and the known-names set knew kinds, agents and commands
        and not lenses."""
        slugs = briefs._lens_slugs()
        self.assertIn("near-miss", slugs)
        self.assertEqual(
            slugs, {p.stem for p in (ROOT / ".v4" / "lenses").glob("*.json")})


class TheRuleAWorkerReasonsFrom(unittest.TestCase):
    """0223cc43 -- and what this repo's oracle actually does."""

    def setUp(self):
        self.kinds = json.loads(
            (ROOT / ".v4" / "claim_kinds.json").read_text(encoding="utf-8"))
        self.cfg = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8"))

    def _rules(self):
        r = self.kinds["test"].get("rule") or []
        return [r] if isinstance(r, dict) else r

    def test_this_repos_oracle_carries_no_filter(self):
        """The measurement the rule was asserting the opposite of.

        The command carries no flag, and the runner behind it collects whatever
        is there: a throwaway directory with one unmarked case is discovered.
        The first version of this read the runner's source instead and
        `test-shape` refused it -- rightly, since running the loader is exactly
        what settles the question and reading it is not.
        """
        import shutil
        import tempfile
        import unittest as _ut

        cmd = self.cfg["test_command"]
        self.assertNotIn(" -m ", f" {cmd} ")
        self.assertNotIn(" -k ", f" {cmd} ")

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "test_unmarked.py").write_text(
            "import unittest\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertTrue(True)\n")
        found = _ut.defaultTestLoader.discover(str(tmp))
        names = [t.id() for s in found for c in s for t in c]
        self.assertEqual(len(names), 1, names)
        self.assertTrue(names[0].endswith("T.test_it"), names)

    def test_and_the_rule_no_longer_says_it_does(self):
        """The shipped rule must also be true in adopters with a different oracle."""
        from types import SimpleNamespace
        from kernel import doctrine
        import tempfile

        texts = [(r.get("text") or "") for r in self._rules()]
        self.assertTrue(texts)
        self.assertNotIn("python3 tests/run_without_silent_skips.py", "\n".join(texts))
        self.assertNotIn("冇任何 filter", "\n".join(texts))
        with tempfile.TemporaryDirectory() as td:
            for command in ("pytest -m integration -q", "cargo test"):
                cfg = SimpleNamespace(root=Path(td), kinds={"test": self.kinds["test"]},
                                      protected=[], config={"test_command": command})
                rendered = doctrine.render(cfg)
                self.assertIn(command, rendered)
                self.assertNotIn("python3 tests/run_without_silent_skips.py", rendered)

    def test_the_rule_still_asks_the_question_the_risk_is_about(self):
        """The premise was false; the risk it named is real and unguarded. The
        replacement has to keep asking, not drop the subject."""
        texts = " ".join((r.get("text") or "") for r in self._rules())
        self.assertIn("CI", texts)


class OneOwnerForWhereTheProbeLives(unittest.TestCase):
    """0bddcfc1 -- the write and the read, from one expression."""

    def _path(self, env=None):
        return subprocess.run(
            ["bash", str(ROOT / "tools" / "runtime_probe.sh"), "--path"],
            cwd=ROOT, env=env, capture_output=True, text=True)

    def test_the_script_prints_where_it_would_write(self):
        r = self._path()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().endswith("v4-runtime-probe"), r.stdout)

    def test_the_override_moves_it(self):
        import os

        env = dict(os.environ, V4_RUNTIME_PROBE="/tmp/somewhere-else")
        self.assertEqual(self._path(env).stdout.strip(), "/tmp/somewhere-else")

    def test_the_config_asks_the_script_rather_than_respelling_it(self):
        cfg = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8"))
        truth = cfg["truth_command"]
        self.assertIn("runtime_probe.sh --path", truth)
        self.assertNotIn("V4_RUNTIME_PROBE", truth,
                         "re-spelling the expression is what drifted")

    def test_the_read_lands_where_the_write_does(self):
        """The property both halves exist for, asked end to end."""
        import os
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, V4_RUNTIME_PROBE=str(tmp / "probe"))
        self.assertEqual(self._path(env).stdout.strip(), str(tmp / "probe"))

        cfg = json.loads(
            (ROOT / ".v4" / "config.json").read_text(encoding="utf-8"))
        where = subprocess.run(
            ["bash", "-c",
             cfg["truth_command"].replace("sqlite3 ", "printf '%s\\n' ")],
            cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(where, str(tmp / "probe") + "/.git/v4/ledger.db")


if __name__ == "__main__":
    unittest.main()
