"""The R3 repairs, one symbol each, called directly.

    python3 -m unittest tests.test_a_word_list_means_what_its_sentence_says -v

Every one of these was a table whose documented rule and whose contents
disagreed, and every one produced a *false positive* -- a finding naming
something innocent. The fixtures prove the checker; these prove the symbol,
which is what a review finding names. All of them fail against 0ad6b61.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.analysis import (                                    # noqa: E402
    fail_closed, secret_patterns, shell_command, test_expectation,
    test_token_shape, test_weakened, webhook_replay,
)


def _fn(src: str):
    return [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)][0]


class ABookkeepingCallIsNotAProcessExit(unittest.TestCase):
    """`fail_closed._is_terminating_call` matched the bare tail of any callee."""

    def _kind(self, body: str) -> str:
        src = f"def f():\n    try:\n        go()\n    except Exception:\n        {body}\n"
        handler = [n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.ExceptHandler)][0]
        return fail_closed.exit_kind(handler.body)

    def test_a_logger_alias_does_not_seal_a_handler(self):
        """`logging.Logger.fatal` writes a line and returns."""
        self.assertEqual(self._kind("log.fatal('boom')"), fail_closed.EXIT_FALL)

    def test_nor_does_any_other_method_named_like_one(self):
        self.assertEqual(self._kind("job.fail('boom')"), fail_closed.EXIT_FALL)

    def test_sys_exit_still_does(self):
        self.assertEqual(self._kind("sys.exit(1)"), fail_closed.EXIT_RAISE)

    def test_and_so_does_a_bare_helper(self):
        self.assertEqual(self._kind("die('boom')"), fail_closed.EXIT_RAISE)


class AReceiverNamesAThingItDoesNotMerelyContain(unittest.TestCase):
    """`fail_closed._matches_outbound` -- `PATH_HINTS` carries `os`."""

    def test_a_string_replace_is_not_a_filesystem_write(self):
        for name in ("hostname.replace", "post_body.replace", "cost.replace"):
            self.assertIsNone(fail_closed._matches_outbound(name), name)

    def test_a_real_one_still_is(self):
        self.assertIn("filesystem write", fail_closed._matches_outbound("os.replace"))
        self.assertIn("filesystem write", fail_closed._matches_outbound("tmp_path.unlink"))

    def test_an_in_process_queue_is_not_an_outbound_call(self):
        self.assertIsNone(fail_closed._matches_outbound("q.put"))

    def test_a_transport_shaped_receiver_is(self):
        self.assertIn("outbound call", fail_closed._matches_outbound("http_client.put"))


#: `AnAttributeThatTheTableDeclares` stood here: two tests on
#: `route_auth._touches`, which read `ast.Call` nodes only and so could never
#: match a pattern written as an attribute (`.protected`) that this repo's own
#: facts table declares. `a9ae5fb` cut the `route-auth` kind and both programs
#: that reached `_touches`; this task removed the function, and the two tests
#: went with it rather than being repointed at something they were not about.
#: Nothing else read the leading-dot notation, so no rule lost a judge here --
#: the rule left with the only code that applied it.


class AVocabularyMatchedBySubstringAnswersAboutSpelling(unittest.TestCase):
    """`webhook_replay.scan` -- `REPLAY` carries the three-letter `iat`."""

    HANDLER = ('from fastapi import APIRouter\nimport hmac\nrouter = APIRouter()\n\n\n'
               '@router.post("/webhook/stripe")\n'
               'def stripe_webhook(body, sig, expected):\n'
               '    if not hmac.compare_digest(sig, expected):\n'
               '        raise ValueError("bad signature")\n'
               '    log.info("initiated")\n'
               '    return process(body)\n')

    def _scan(self, source, facts=None):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "api").mkdir()
        (tmp / "api" / "routes.py").write_text(source)
        return webhook_replay.scan(tmp, facts or {"entrypoint_globs": ["api/**"]},
                                   files=["api/routes.py"])

    def test_a_log_line_spelling_iat_does_not_bound_replay(self):
        got = self._scan(self.HANDLER)
        self.assertTrue(got)
        self.assertIn("nothing bounds replay", got[0][3])

    def test_a_real_timestamp_check_does(self):
        """The control: refusing everything would pass the test above."""
        real = self.HANDLER.replace('log.info("initiated")',
                                    'check_timestamp(body, max_age=300)')
        self.assertEqual(self._scan(real), [])

    def test_the_repos_own_receiver_names_are_honoured(self):
        """`scan` called `is_route` without `known=`, so a repo whose app
        object is `admin_api` had zero routes found, silently."""
        src = ('import hmac\nadmin_api = object()\n\n\n'
               '@admin_api.post("/webhook/stripe")\n'
               'def stripe_webhook(body, sig, expected):\n'
               '    if not hmac.compare_digest(sig, expected):\n'
               '        raise ValueError("bad")\n'
               '    return process(body)\n')
        got = self._scan(src, {"entrypoint_globs": ["api/**"],
                               "route_receivers": ["admin_api"]})
        self.assertTrue(got)


class ATemplateIsAShapeNotACharacter(unittest.TestCase):
    """`secret_patterns.has_template_char` -- five RFC 3986 sub-delims."""

    def test_a_password_carrying_sub_delims_is_not_a_template(self):
        self.assertEqual(secret_patterns.has_template_char("aX9$qw(Lm)2Zp*7Rt"), "")

    def test_a_substitution_still_is(self):
        for value in ("${DB_PASSWORD}", "$(pass show db)", "%(password)s"):
            self.assertTrue(secret_patterns.has_template_char(value), value)


class APortMappingIsNotACredential(unittest.TestCase):
    """`test_token_shape.CREDENTIAL_SHAPE` had no non-digit requirement."""

    def test_docker_compose_ports_do_not_match(self):
        for value in ("5432:5432", "8080:8080", "27017:27017"):
            self.assertIsNone(test_token_shape.CREDENTIAL_SHAPE.match(value), value)

    def test_the_literal_this_checker_exists_for_still_does(self):
        self.assertIsNotNone(test_token_shape.CREDENTIAL_SHAPE.match("123:SECRET"))

    #: Through `scan`, not the pattern alone. The claim names `scan`, and a
    #: test that never enters the symbol it closes is the shape `redgreen`
    #: refuses -- it refused this file until these two arrived.
    PORTS = ('def test_compose():\n'
             '    ports = ["5432:5432", "8080:8080"]\n'
             '    assert all(":" in p for p in ports)\n')
    #: A literal that does *not* say it is fake, because that is the case this
    #: asserts on: a fixture carrying the word `TEST` is one `test_token_shape`
    #: is right to pass over, and passing over it here would leave the rule
    #: with no case that fires. It is a string inside a string -- nothing sends
    #: it anywhere -- and it is in `.v4/test-token-shape_baseline.json` with
    #: that reason rather than hidden by rewording.
    TOKEN = 'def test_send():\n    send(token="123:SECRET")\n'

    def test_scan_reports_no_finding_for_ports(self):
        self.assertEqual(test_token_shape.scan(self.PORTS, "tests/t.py"), [])

    def test_scan_still_reports_the_token_shape(self):
        got = test_token_shape.scan(self.TOKEN, "tests/t.py")
        self.assertTrue(got)
        self.assertEqual(got[0].literal, "123:SECRET")


class ADecoratorThatStopsATestJudging(unittest.TestCase):
    """`test_weakened.count` tested `"skip" in decorators`."""

    LIVE = "def test_x():\n    assert 1 == 1\n"

    def test_expected_failure_takes_a_test_out_of_the_run(self):
        src = "import unittest\n@unittest.expectedFailure\n" + self.LIVE
        self.assertEqual(test_weakened.count(src), 0)

    def test_and_so_does_xfail(self):
        src = "import pytest\n@pytest.mark.xfail\n" + self.LIVE
        self.assertEqual(test_weakened.count(src), 0)

    def test_a_live_test_is_still_counted(self):
        self.assertEqual(test_weakened.count(self.LIVE), 1)


class AnExpectationEditedToMatchTheResult(unittest.TestCase):
    """`test_expectation.ASSERT_CALLS` named ten of eighteen."""

    def test_a_comparison_assertion_carries_an_expectation(self):
        for name in ("assertGreaterEqual", "assertLess", "assertRegex"):
            self.assertIn(name, test_expectation.ASSERT_CALLS, name)

    def test_loosening_one_is_visible(self):
        def literals(bound):
            src = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_x(self):\n"
                   f"        self.assertGreaterEqual(compute(), {bound})\n")
            return test_expectation.expectations(_fn(src))
        self.assertNotEqual(literals(10), literals(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ANewlineSeparatesTwoCommands(unittest.TestCase):
    """`shell_command.tokenize` read a newline as ordinary whitespace.

    So `writes_to_protected` classified the first line of a multi-line payload
    and nothing after it, and a Bash payload is routinely multi-line --
    prefixing any harmless first line walked past `hooks/bash_guard.py`
    entirely.
    """

    PROTECTED = (".v4/**",)
    WRITE = "sed -i '' 's/a/b/' .v4/config.json"

    def test_a_newline_produces_an_operator(self):
        kinds = [k for k, _ in shell_command.tokenize("echo hi\n" + self.WRITE)]
        self.assertIn("op", kinds)

    def test_so_the_second_line_is_classified_too(self):
        got = shell_command.writes_to_protected("echo hi\n" + self.WRITE, self.PROTECTED)
        self.assertTrue(got, "a harmless first line hid the write behind it")
        self.assertEqual(got[0][0], ".v4/config.json")

    def test_the_single_line_form_still_answers(self):
        self.assertTrue(shell_command.writes_to_protected(self.WRITE, self.PROTECTED))
