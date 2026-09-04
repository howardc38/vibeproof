"""Writing the doctrine twice has to produce the same file.

    python3 -m unittest tests.test_doctrine_idempotent -v

`block()` ends with `END\\n`, and `split()` starts `after` at the byte following
`END` -- so `after` carries the same newline, and `before + block + after` added
one on every write.  Measured on the reference adopter: 45 trailing blank lines,
and `+1` on each of five consecutive `v4 doctrine --write` runs.  `v4 install`
runs it, so it grew once per install.

`drift()` could not see it.  It compares `have.rstrip("\\n")` against
`block(cfg).rstrip("\\n")`, so the only thing that was growing is the only thing
it strips off both sides.  `v4 doctor` reported ok throughout.

CLAUDE.md is the file every worker reads before touching anything, which is why
this is worth a test rather than a tidy-up: the mechanism that exists to say
"this generated file is still what the generator produces" was answering about
everything except the part that changed.

Three things pinned:

* **Repeated writes do not change the file.**  The property the whole generated-
  file design rests on.
* **Whatever the adopter wrote after the block survives byte for byte.**  That
  is the case `split()` exists for, and the fix must not buy idempotency by
  truncating.
* **An already-leaked file heals.**  Every repo that installed before this
  carries the blank lines; the next write has to remove them rather than freeze
  them in place.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import config, doctrine  # noqa: E402

CONFIG = {"test_command": "true", "policy": "allow_accepted_risk", "doctrine": True}


def _repo(tmp: Path) -> config.RepoConfig:
    (tmp / ".v4").mkdir(parents=True, exist_ok=True)
    (tmp / ".v4" / "config.json").write_text(json.dumps(CONFIG))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / ".v4" / "checkers.json").write_text("{}")
    return config.RepoConfig(tmp)


class WritingTwiceChangesNothing(unittest.TestCase):
    def test_five_writes_produce_one_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _repo(Path(td))
            path, _ = doctrine.write(cfg)
            first = path.read_text()
            for _ in range(4):
                doctrine.write(cfg)
            self.assertEqual(path.read_text(), first)

    def test_the_second_write_reports_nothing_changed(self):
        """`write` returns `(path, changed)` and `v4 install` prints from it.
        A file that grows silently also reports `changed=True` forever."""
        with tempfile.TemporaryDirectory() as td:
            cfg = _repo(Path(td))
            doctrine.write(cfg)
            _, changed = doctrine.write(cfg)
            self.assertFalse(changed)


class WhatTheAdopterWroteSurvives(unittest.TestCase):
    def test_content_after_the_block_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _repo(root)
            path = doctrine.path_for(root)
            path.write_text("# mine\n\n" + doctrine.block(cfg).rstrip("\n")
                            + "\n\n## my own section\n\ntext the adopter wrote\n")
            before = path.read_text()
            for _ in range(3):
                doctrine.write(cfg)
            after = path.read_text()
            self.assertEqual(after, before)
            self.assertIn("## my own section", after)
            self.assertIn("text the adopter wrote", after)
            self.assertTrue(after.startswith("# mine"))

    def test_content_before_the_block_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _repo(root)
            path = doctrine.path_for(root)
            path.write_text("# a repo that had one already\n\nprose\n")
            doctrine.write(cfg)
            doctrine.write(cfg)
            text = path.read_text()
            self.assertIn("a repo that had one already", text)
            self.assertIn("prose", text)
            self.assertIn(doctrine.BEGIN, text)


class AnAlreadyLeakedFileHeals(unittest.TestCase):
    def test_accumulated_blank_lines_are_removed(self):
        """Every repo that installed before this carries them: 45 on the
        reference adopter, 10 on this framework's own."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _repo(root)
            path = doctrine.path_for(root)
            path.write_text(doctrine.block(cfg).rstrip("\n") + "\n" + "\n" * 45)
            leaked = len(path.read_text().splitlines())
            doctrine.write(cfg)
            healed = len(path.read_text().splitlines())
            self.assertLess(healed, leaked)
            doctrine.write(cfg)
            self.assertEqual(len(path.read_text().splitlines()), healed)


class TheMarkersStayUnique(unittest.TestCase):
    def test_repeated_writes_do_not_duplicate_the_block(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _repo(Path(td))
            path = doctrine.path_for(Path(td))
            for _ in range(3):
                doctrine.write(cfg)
            text = path.read_text()
            self.assertEqual(text.count(doctrine.BEGIN), 1)
            self.assertEqual(text.count(doctrine.END), 1)


if __name__ == "__main__":
    unittest.main()
