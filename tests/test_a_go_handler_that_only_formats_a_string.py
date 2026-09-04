"""`GO_REPORTS` matched a substring of the callee, and sealed handlers with it.

    python3 -m unittest tests.test_a_go_handler_that_only_formats_a_string -v

The table's own comment says what it is for -- "calls inside an `if err != nil`
body that mean somebody finds out" -- and the test was
``any(r in c for r in GO_REPORTS)``, the word appearing anywhere in the name.
``fmt.Sprintf`` contains ``print``; ``catalog.Get`` contains ``log``. So an
``if err != nil`` body that formats a string into a variable and does nothing
else read as a handler that had reported the failure, in the rule whose whole
subject is failures that go unreported.

Asked of `_go_reports` rather than through the Go parser: `go_findings` takes a
shape dict, `shape.go` produces the dotted callee names, and the judgement
about those names is Python's -- which is the split
`kernel/analysis/gosource.py` states ("every judgement is made in Python beside
the same judgement for Python code"). `go_findings` is exercised here on the
same shape the extractor emits, so the caller is covered too and the test needs
no Go toolchain to run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import fail_closed  # noqa: E402


def _err_shape(*calls, stmts=1, fn="Write"):
    """The shape `kernel/analysis/_go/shape.go` emits for one `if err != nil`."""
    return {"errs": [{"name": "err", "line": 9, "fn": fn, "stmts": stmts,
                      "calls": list(calls)}], "blanks": []}


class FormattingAStringIsNotReporting(unittest.TestCase):
    def test_sprintf_is_not_a_print(self):
        self.assertFalse(fail_closed._go_reports("fmt.Sprintf"))

    def test_a_catalog_is_not_a_log(self):
        self.assertFalse(fail_closed._go_reports("catalog.Get"))

    def test_the_handler_is_still_a_finding(self):
        findings = fail_closed.go_findings("svc/write.go",
                                           _err_shape("fmt.Sprintf"))
        self.assertEqual(1, len(findings))
        self.assertEqual(fail_closed.VARIANT_GO_EMPTY, findings[0].variant)


class WhatDoesMeanSomebodyFindsOut(unittest.TestCase):
    def test_the_package_a_report_goes_through(self):
        for callee in ("log.Printf", "logger.Info", "metrics.Inc",
                       "tracer.Start", "t.Fatalf"):
            with self.subTest(callee=callee):
                self.assertTrue(fail_closed._go_reports(callee))

    def test_the_method_that_wraps_the_error(self):
        # Go's convention is a suffix on the name, not a word inside it, which
        # is why the match is on the start of a segment.
        for callee in ("fmt.Errorf", "errors.Wrap", "zap.Warnf"):
            with self.subTest(callee=callee):
                self.assertTrue(fail_closed._go_reports(callee))

    def test_a_body_that_logs_is_sealed(self):
        self.assertEqual([], fail_closed.go_findings("svc/write.go",
                                                     _err_shape("log.Printf")))

    def test_work_that_is_not_reporting_is_not(self):
        for callee in ("os.WriteFile", "strings.Replace", "db.Get", "cache.Set"):
            with self.subTest(callee=callee):
                self.assertFalse(fail_closed._go_reports(callee))


if __name__ == "__main__":
    unittest.main(verbosity=2)
