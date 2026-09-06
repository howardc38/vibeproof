"""The first document a newcomer opens, and the checks that never read it.

    python3 -m unittest tests.test_the_front_door -v

`spec_coverage.user_facing_docs` is the only thing in this repo that checks a
documented `v4` invocation against the CLI, and its own docstring says what
that means: "what it does not read is unchecked by anything". It read
`docs/`, `CLAUDE.md`, the two monitor files and the seven role files -- and not
`README.md`, which is 58 KB across three languages and shows `v4 …` as
something to type.

Half of f41271c8fbffdd01. The other half -- whether these three belong in
`docs/README.md`'s authority table, and with what scope, given that they restate
contracts SPEC owns -- is a decision about what this repo's front door is, and
is left open with that written down.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage                                # noqa: E402


class TheFrontDoorIsRead(unittest.TestCase):
    def setUp(self):
        self.docs = [d.relative_to(ROOT).as_posix()
                     for d in spec_coverage.user_facing_docs(ROOT)]

    def test_all_three_readmes_are_in_the_set(self):
        for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
            with self.subTest(doc=name):
                self.assertIn(name, self.docs)

    def test_and_nothing_that_was_there_left(self):
        """Adding to this set must not be a rewrite of it."""
        for name in ("docs/SPEC.md", "CLAUDE.md",
                     ".github/monitor/PROMPT.md",
                     ".claude/agents/reviewer.md",
                     ".claude/commands/wave.md"):
            with self.subTest(doc=name):
                self.assertIn(name, self.docs)

    def test_every_document_in_the_set_is_here(self):
        """A path in the set that is not on disk makes the checks it drives
        vacuous rather than red."""
        for d in spec_coverage.user_facing_docs(ROOT):
            self.assertTrue(d.is_file(), d)

    def test_the_two_checks_hold_with_them_included(self):
        """Measured before the change and asserted after: this is coverage at
        no cost. If it ever stops being free, that is a finding about a README,
        not a reason to take them out."""
        self.assertEqual(list(spec_coverage.launcher_is_reachable(ROOT) or []),
                         [])
        self.assertEqual(list(spec_coverage.flags_resolve(ROOT) or []), [])

    def test_a_readme_that_types_v4_without_saying_where_is_caught(self):
        """The property the inclusion buys, shown on a document that fails it.

        `launcher_is_reachable` reads whatever `user_facing_docs` hands it, so
        this hands it one README-shaped file that shows a command and never says
        where `v4` comes from.
        """
        import shutil
        import tempfile
        from unittest import mock

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        bad = tmp / "README.md"
        bad.write_text("# A repo\n\n```\nv4 --repo . status --task t\n```\n")

        # The root it is asked about is the tempdir, because the check reports
        # paths relative to it.
        with mock.patch.object(spec_coverage, "user_facing_docs",
                               lambda root: [bad]):
            problems = list(spec_coverage.launcher_is_reachable(tmp) or [])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("README.md", str(problems[0]))


if __name__ == "__main__":
    unittest.main()
