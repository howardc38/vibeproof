"""The `source_assertion` shape, reached through reflection instead of text.

`red/getsource_assert` is the same test written with `inspect.getsource`, and
that one is reported. This one never opens a file and never reads a line of
source: it asks the live object what its parameters are. It also never calls
`run`, so deleting the body of `run` leaves it green, which is the property the
rule is named for.

It exits 0, and that is the gap. `source_assertions` matches `getsource`,
`getsourcelines` and `read_text` of a source file -- three ways of getting at
the *text* -- and reflection is not one of them.
"""

import inspect
import unittest

from app.worker import run


class TheDefaultTimeoutHasOneOwner(unittest.TestCase):
    def test_the_default_is_not_a_literal(self):
        sig = inspect.signature(run)
        self.assertIsNone(sig.parameters["timeout"].default)

    def test_the_flag_that_could_not_work_is_gone(self):
        self.assertNotIn("retries", inspect.signature(run).parameters)
