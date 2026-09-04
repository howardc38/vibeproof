"""Three TypeScript readers that judged text they had said they would not read.

    python3 -m unittest tests.test_what_a_typescript_reader_counts_as_code -v

None of the three has a parser. All three work off a mask, and all three said
in their own docstring or comment that comments and strings are gone before
anything is matched. Each of them was reading a different half of that
sentence:

* ``symbols.ts_imports`` -- the string pass was
  ``_TS_STRING.sub(lambda m: m.group(0), ...)``, an identity substitution, so
  an ``import`` written inside a template literal was yielded as a real import
  and travelled on to ``dangling_ref.ts_scan`` and ``layers.scan``.
* ``external_write._ts_scopes`` -- it took the first ``{`` after the opening
  paren of the parameter list, which is the destructuring brace, so a function
  with a destructured parameter got a one-line body and the write inside it
  fell back to the file-wide span the function exists to prevent.
* ``test_shape.ts_findings`` -- the fan-out bound was a substring search over
  the whole file, over text that still had its string literals, so one
  ``const label = "batch upload"`` silenced ``unbounded_fanout`` everywhere in
  the file.

The fixture sets pin two of these from the outside
(``external_write/bypass/ts_the_parameters_are_destructured.ts``,
``test_shape/bypass/ts_a_bound_named_only_in_a_string``). These are the same
facts asked of the functions directly, which is where the next reader will
look.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import external_write, symbols, test_shape  # noqa: E402
from kernel.analysis.test_expectation import _ts_mask  # noqa: E402


class AnImportInsideATemplateLiteralIsNotAnImport(unittest.TestCase):
    SOURCE = (
        "const example = `\n"
        "import { Secret } from '../../forbidden/place';\n"
        "`;\n"
        "import { Real } from './real';\n"
    )

    def test_the_quoted_one_is_not_yielded(self):
        specs = [spec for spec, _names, _line in symbols.ts_imports(self.SOURCE)]
        self.assertNotIn(
            "../../forbidden/place", specs,
            "an import inside a template literal reaches layers.scan as a "
            "crossing this file does not make")

    def test_the_real_one_still_is(self):
        # The specifier of a real import is itself a string, so this is the
        # half a blanket strip would have destroyed.
        self.assertEqual([("./real", ["Real"], 4)],
                         symbols.ts_imports(self.SOURCE))

    def test_a_string_holding_a_module_path_is_not_a_specifier(self):
        src = "const path = \"import x from './x'\";\nimport y from './y';\n"
        # `y` is a default import, so the bound name is spelled `default`.
        self.assertEqual([("./y", ["default"], 2)], symbols.ts_imports(src))


class AFunctionBodyStartsAfterTheParameterList(unittest.TestCase):
    SOURCE = (
        "async function send({ id, body }) {\n"
        "  await fetch('/api/' + id, { method: 'POST', body });\n"
        "  return id;\n"
        "}\n"
        "\n"
        "export async function status(id) {\n"
        "  return await fetch('/api/' + id);\n"
        "}\n"
    )

    def _scopes(self):
        return dict((name, (a, b))
                    for name, a, b in external_write._ts_scopes(
                        self.SOURCE, _ts_mask(self.SOURCE)))

    def test_a_destructured_parameter_does_not_end_the_body(self):
        self.assertEqual((1, 4), self._scopes()["send"])

    def test_the_two_functions_do_not_share_a_scope(self):
        # This is what the span is for: without it the readback in `status`
        # excuses the unobserved write in `send`.
        send, status = self._scopes()["send"], self._scopes()["status"]
        self.assertLess(send[1], status[0])

    def test_a_concise_arrow_claims_no_body_at_all(self):
        # It has none, and taking the next brace in the file would hand it
        # somebody else's.
        src = "const twice = (a) => a * 2;\nfunction real(b) {\n  return b;\n}\n"
        names = [n for n, _a, _b in external_write._ts_scopes(src, _ts_mask(src))]
        self.assertEqual(["real"], names)


class AFanOutBoundIsSomethingTheCodeNames(unittest.TestCase):
    UNBOUNDED = (
        "const STEP = 'batch upload';\n"
        "export async function send(items) {\n"
        "  return Promise.all(items.map((x) => fetch(x)));\n"
        "}\n"
    )
    BOUNDED = (
        "import pLimit from 'p-limit';\n"
        "const limit = pLimit(5);\n"
        "export async function send(items) {\n"
        "  return Promise.all(items.map((x) => limit(() => fetch(x))));\n"
        "}\n"
    )
    ELSEWHERE = (
        "export async function bounded(items) {\n"
        "  const limiter = pLimit(5);\n"
        "  return Promise.all(items.map((x) => limiter(() => fetch(x))));\n"
        "}\n"
        "export async function blast(items) {\n"
        "  return Promise.all(items.map((x) => fetch(x)));\n"
        "}\n"
    )

    def _variants(self, src):
        return [v for _f, _s, _l, _w, v in
                test_shape.ts_findings("src/send.ts", src, False)]

    def test_a_bound_named_only_in_a_string_bounds_nothing(self):
        self.assertEqual(["unbounded_fanout"], self._variants(self.UNBOUNDED))

    def test_a_limiter_beside_the_imports_does_bound_it(self):
        self.assertEqual([], self._variants(self.BOUNDED))

    def test_a_limiter_in_another_function_does_not(self):
        findings = test_shape.ts_findings("src/send.ts", self.ELSEWHERE, False)
        self.assertEqual([6], [line for _f, _s, line, _w, _v in findings])

    def test_a_source_path_is_still_read_out_of_the_source(self):
        # The mask blanks the string the filename lives in, so the argument has
        # to be sliced out of the original -- which is only possible because
        # `_ts_mask` keeps every offset.
        src = ("import { readFileSync } from 'node:fs';\n"
               "it('checks', () => {\n"
               "  const text = readFileSync('src/handler.ts', 'utf8');\n"
               "  expect(text).toContain('validateToken');\n"
               "});\n")
        self.assertEqual(
            ["source_assertion"],
            [v for _f, _s, _l, _w, v in
             test_shape.ts_findings("tests/h.test.ts", src, True)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
