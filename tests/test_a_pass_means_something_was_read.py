"""An analyser that returned "nothing to report" having read nothing.

    python3 -m unittest tests.test_a_pass_means_something_was_read -v

`scan` returning `[]` is printed by its checker as a PASS. Returning `None` is
printed as exit 4, UNSUPPORTED, which is not terminal and does not close a
task. The difference is the whole reason exit 4 exists.

This module was written about two analysers. `route_auth.scan` was the one that
collapsed the two -- with rows in `auth_decision` and no `entrypoint_globs`, its
candidate list came out empty and it returned `[]` -- and `webhook_replay.scan`
was the sibling that had answered `None` all along, which is what made the
first a divergence rather than a design choice. `route_auth.scan` no longer
exists: `a9ae5fb` cut the `route-auth` kind and both programs that called it,
and the dead half of that module went with it. Four of the five tests here were
about that function and are gone with it.

What is left is the sibling, and it is left because the rule is about every
`scan` in `kernel/analysis/`, not about the one that broke it: a repo that has
not said where its entrypoints are gets exit 4, not a PASS.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import webhook_replay  # noqa: E402


class NothingToReadIsNotNothingToReport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_webhook_replay_with_no_entrypoints_cannot_answer(self):
        self.assertIsNone(webhook_replay.scan(self.tmp, {"entrypoint_globs": []}),
                          "[] is printed as a PASS; this repo said nothing "
                          "about where its entrypoints are")


if __name__ == "__main__":
    unittest.main(verbosity=2)
