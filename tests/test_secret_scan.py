"""Tests for the ``secret`` checker and its analysis module.

Run with the interpreter, no third-party dependency:

    python3 -m unittest tests.test_secret_scan -v

**The red fixtures are credential-shaped on purpose.**  Every value under
``tests/fixtures/secret/red/`` was generated for this suite and was never issued
by any provider, but a scanner cannot know that -- which is the whole point: a
fixture that a scanner can tell apart from a real key proves nothing.  A
repo-level scan of this repo will therefore flag that directory, correctly.
Exclude it at the subject level; do not teach the checker to exempt it, because
"exempt this path" is the defect this checker was written to remove.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from checkers import secret_scan as checker  # noqa: E402
from kernel.analysis import secret_patterns as analysis  # noqa: E402

RED = ROOT / "tests" / "fixtures" / "secret" / "red"
GREEN = ROOT / "tests" / "fixtures" / "secret" / "green"
#: The same credential rewritten to look like it evades. No test in this file
#: read the directory: the colour that carries the most information -- the
#: gate's first run found 13 real evasions across 20 checkers -- ran only
#: under `v4 verify`.
BYPASS = ROOT / "tests" / "fixtures" / "secret" / "bypass"

#: The pattern each red fixture exists to prove is still caught.
RED_EXPECTED = {
    "anthropic_key.py": "anthropic",
    "aws_key.yml": "aws_access_key",
    "base64_wrapped.txt": "encoded_aws_access_key",
    "db_url.md": "uri_credentials",
    # The same shape with the RFC 3986 sub-delims a generated password carries.
    # `TEMPLATE_CHARS` held `$ ( ) * '` while the sentence beside it said
    # sub-delims are left out because a real password may contain them, so this
    # line was suppressed as a template and never reported.
    "db_url_with_sub_delims.md": "uri_credentials",
    "google_api.tsx": "google_api",
    "openai_key.env": "openai",
    "service_account.json": "private_key",
    "slack_token.ts": "slack",
    "stripe_live.py": "stripe_live",
    "test_live_integration.py": "github_pat_classic",
}


def run(repo_root: Path, rel_paths, *, explain: bool = False, diff_base=None):
    """(exit_code, stdout) for one checker invocation, in-process.

    `diff_base` is what tells a repo-scoped claim how to work out which files a
    task changed. Without it a subject naming no files is malformed rather than
    empty, and the two answers differ.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        payload = {
            "claim_id": "claim-t",
            "claim_kind": "secret",
            "repo_root": str(repo_root),
            "subject_refs": [{"kind": "file", "path": p} for p in rel_paths],
        }
        if diff_base is not None:
            payload["diff_base"] = diff_base
        json.dump(payload, handle)
        subject = handle.name
    argv = ["--subject", subject] + (["--explain"] if explain else [])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = checker.main(argv)
    return code, buffer.getvalue()


class BypassFixtures(unittest.TestCase):
    """A rewritten credential fails exactly as the red case it rewrites."""

    def test_each_bypass_fixture_still_fails(self):
        cases = sorted(d for d in BYPASS.iterdir() if d.is_dir())
        self.assertGreaterEqual(len(cases), 3, cases)
        for case in cases:
            rels = sorted(str(f.relative_to(case)) for f in case.rglob("*")
                          if f.is_file() and not f.name.startswith("."))
            with self.subTest(fixture=case.name):
                code, out = run(case, rels)
                self.assertEqual(code, checker.EXIT_FAIL, out)


class RedFixtures(unittest.TestCase):
    """Every red fixture must exit 1 and name the credential it hides."""

    def test_each_red_fixture_fails_alone(self):
        for name, pattern_id in sorted(RED_EXPECTED.items()):
            with self.subTest(fixture=name):
                code, out = run(RED, [name])
                self.assertEqual(code, checker.EXIT_FAIL, out)
                self.assertIn(f"{name}:", out)

    def test_each_red_fixture_matches_the_expected_pattern(self):
        for name, pattern_id in sorted(RED_EXPECTED.items()):
            with self.subTest(fixture=name):
                report = analysis.analyse_source((RED / name).read_text(), path=name)
                self.assertEqual(
                    [v.candidate.pattern_id for v in report.findings], [pattern_id]
                )

    def test_every_red_fixture_is_covered_by_this_table(self):
        on_disk = sorted(p.name for p in RED.iterdir() if p.is_file())
        self.assertEqual(on_disk, sorted(RED_EXPECTED))
        self.assertGreaterEqual(len(on_disk), 8)

    def test_a_test_path_is_not_an_exemption(self):
        """The whole point.  ``test_live_integration.py`` is still a leak."""
        code, out = run(RED, ["test_live_integration.py"])
        self.assertEqual(code, checker.EXIT_FAIL, out)
        self.assertIn("GitHub PAT", out)

    def test_a_test_path_under_a_tests_directory_is_not_an_exemption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "tests" / "integrations" / "test_bridge.py"
            target.parent.mkdir(parents=True)
            target.write_text((RED / "test_live_integration.py").read_text())
            code, out = run(root, ["tests/integrations/test_bridge.py"])
        self.assertEqual(code, checker.EXIT_FAIL, out)


class GreenFixtures(unittest.TestCase):
    """Every green fixture must exit 0, and say which rule silenced what."""

    def green_names(self):
        return sorted(p.name for p in GREEN.iterdir() if p.is_file())

    def test_there_are_at_least_eight(self):
        self.assertGreaterEqual(len(self.green_names()), 8)

    def test_each_green_fixture_passes_alone(self):
        for name in self.green_names():
            with self.subTest(fixture=name):
                code, out = run(GREEN, [name])
                self.assertEqual(code, checker.EXIT_PASS, out)

    def test_all_green_fixtures_together_pass(self):
        code, out = run(GREEN, self.green_names())
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertIn("no committed credential", out)

    def test_suppressions_are_explained_on_demand(self):
        code, out = run(GREEN, self.green_names(), explain=True)
        self.assertEqual(code, checker.EXIT_PASS, out)
        for rule in (
            analysis.RULE_TEMPLATE,
            analysis.RULE_PLACEHOLDER_WORD,
            analysis.RULE_USER_EQ_PASSWORD,
            analysis.RULE_FAKE_MARKER,
            analysis.RULE_DEGENERATE,
            analysis.RULE_PEM_EMPTY,
            analysis.RULE_PRAGMA,
        ):
            with self.subTest(rule=rule):
                self.assertIn(f"[{rule}]", out)

    def test_suppressions_are_counted_but_not_dumped_by_default(self):
        code, out = run(GREEN, self.green_names())
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertIn("Suppressed", out)
        self.assertNotIn(f"[{analysis.RULE_PLACEHOLDER_WORD}]", out)


class MixedScope(unittest.TestCase):
    def test_one_red_among_all_the_green_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for src in list(GREEN.iterdir()) + [RED / "anthropic_key.py"]:
                (root / src.name).write_text(src.read_text())
            names = sorted(p.name for p in root.iterdir())
            code, out = run(root, names)
        self.assertEqual(code, checker.EXIT_FAIL, out)
        self.assertIn("1 committed credential", out)

    def test_a_finding_outranks_an_unreadable_sibling(self):
        code, out = run(RED, ["anthropic_key.py", "does_not_exist.py"])
        self.assertEqual(code, checker.EXIT_FAIL, out)
        self.assertIn("Unverifiable", out)


class CannotVerify(unittest.TestCase):
    def test_missing_file(self):
        code, out = run(GREEN, ["nope.py"])
        self.assertEqual(code, checker.EXIT_CANNOT_VERIFY, out)
        self.assertIn("file is missing", out)

    def test_a_subject_with_no_files_and_no_way_to_find_them(self):
        """Malformed: no repo_root or no diff_base, so nothing can be worked out."""
        code, out = run(GREEN, [], diff_base=None)
        self.assertEqual(code, checker.EXIT_CANNOT_VERIFY, out)
        self.assertIn("named no files", out)

    def test_a_task_that_changed_nothing_passes(self):
        """Not the same thing. A task with no changes has nothing that could
        carry a credential, and calling that unverifiable blocks it forever."""
        code, out = run(GREEN, [], diff_base="HEAD")
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertIn("nothing could carry", out)

    def test_binary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blob.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
            code, out = run(root, ["blob.bin"])
        self.assertEqual(code, checker.EXIT_CANNOT_VERIFY, out)
        self.assertIn("not UTF-8 text", out)

    def test_an_oversize_text_file_is_read_in_pieces_rather_than_refused(self):
        """This used to answer CANNOT_VERIFY, and the limit is about memory.

        Measured on one adopter: the file that tripped it was the ledger export
        -- tool-written, append-only, carrying engagement prose verbatim -- so
        the one file most likely to hold a pasted credential was the one file
        nothing could read, and it grew past the limit again after every ship.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filler = "harmless line %d\n"
            body = "".join(filler % i for i in range(400_000))
            self.assertGreater(len(body.encode()), checker.MAX_FILE_BYTES)
            (root / "huge.log").write_text(body)
            code, out = run(root, ["huge.log"])
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertNotIn("over the", out)

    def test_an_oversize_text_file_still_reports_what_it_holds(self):
        """Scanned, not waved through."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = ["harmless line %d" % i for i in range(400_000)]
            # This checker's own test data: the assertion below is that this
            # exact shape IS judged a secret, so it has to look like one.
            # pragma: allow-secret
            rows[399_000] = "postgresql://realuser:S3cretPassw0rd@db:5432/app"
            (root / "huge.log").write_text("\n".join(rows))
            code, out = run(root, ["huge.log"])
        self.assertEqual(code, checker.EXIT_FAIL, out)
        self.assertIn("huge.log", out)

    def test_an_oversize_blob_is_still_unverifiable(self):
        """"Too big to hold" and "a text scanner cannot read this at all" are
        different answers, and collapsing them is what this checker exists to
        stop."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.onnx").write_bytes(
                b"\x00\x01\x02\x03" * (checker.MAX_FILE_BYTES // 2))
            code, out = run(root, ["model.onnx"])
        self.assertEqual(code, checker.EXIT_CANNOT_VERIFY, out)
        self.assertIn("binary", out)

    def test_a_wholly_vendored_subject_answers_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".venv" / "pkg" / "keys.py"
            target.parent.mkdir(parents=True)
            target.write_text((RED / "anthropic_key.py").read_text())
            code, out = run(root, [".venv/pkg/keys.py"])
        self.assertEqual(code, checker.EXIT_CANNOT_VERIFY, out)
        self.assertIn("every file in the subject is vendored", out)


class Scope(unittest.TestCase):
    def test_vendored_files_do_not_poison_a_clean_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendored = root / "node_modules" / "pkg" / "keys.js"
            vendored.parent.mkdir(parents=True)
            vendored.write_text((RED / "anthropic_key.py").read_text())
            (root / "clean.py").write_text((GREEN / "url_placeholders.md").read_text())
            code, out = run(root, ["clean.py", "node_modules/pkg/keys.js"])
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertIn("Out of scope", out)

    def test_vendored_segments(self):
        self.assertEqual(analysis.is_vendored(".venv/lib/x.py"), ".venv")
        self.assertEqual(analysis.is_vendored("a/node_modules/b.js"), "node_modules")
        self.assertEqual(analysis.is_vendored("lib/site-packages/x.py"), "site-packages")
        self.assertEqual(analysis.is_vendored("tests/integrations/test_x.py"), "")
        self.assertEqual(analysis.is_vendored("core/config/settings.py"), "")


class Rules(unittest.TestCase):
    """Each suppression rule, on the value from the 78 that motivated it."""

    def judge(self, text: str, pattern_id: str):
        pattern = analysis.PATTERN_BY_ID[pattern_id]
        kind = pattern.kind
        return analysis.judge(
            analysis.Candidate(
                path="x", line=1, col=0, pattern_id=pattern_id,
                label=pattern.label, kind=kind, text=text, line_text=text,
            )
        )

    def test_template_placeholder(self):
        j = self.judge("s3://{access_key}:{secret_key}@", "uri_credentials")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_TEMPLATE)

    def test_placeholder_word(self):
        j = self.judge("postgresql://operator:secret-password@", "uri_credentials")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_PLACEHOLDER_WORD)

    def test_user_equals_password(self):
        j = self.judge("postgresql://postgres:postgres@", "uri_credentials")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_USER_EQ_PASSWORD)

    def test_declared_fake_marker(self):
        j = self.judge("AKIAIOSFODNN7EXAMPLE", "aws_access_key")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_FAKE_MARKER)

    def test_degenerate_identical_run(self):
        j = self.judge("ghp_" + "x" * 36, "github_pat_classic")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_DEGENERATE)

    def test_degenerate_monotone_run(self):
        j = self.judge("AKIA1234567890ABCDEF", "aws_access_key")
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_DEGENERATE)

    def test_pem_without_key_material(self):
        j = self.judge(
            '-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----',
            "private_key",
        )
        self.assertFalse(j.is_secret)
        self.assertEqual(j.rule, analysis.RULE_PEM_EMPTY)

    def test_a_real_uri_password_is_a_secret(self):
        # This checker's own test data: the assertion below is that this
        # exact shape IS judged a secret, so it has to look like one.
        # pragma: allow-secret
        j = self.judge("postgresql://svc_publisher:anmVa1fJrqi8XKWbOn8SrCyU@", "uri_credentials")
        self.assertTrue(j.is_secret)

    def test_a_real_token_body_is_a_secret(self):
        # This checker's own test data: the assertion below is that this
        # exact shape IS judged a secret, so it has to look like one.
        # pragma: allow-secret
        j = self.judge("AKIALU3NDADVWPP7FXBG", "aws_access_key")
        self.assertTrue(j.is_secret)

    def test_degenerate_thresholds_do_not_reach_a_random_body(self):
        body = "LU3NDADVWPP7FXBG"
        self.assertEqual(analysis.degenerate_reason(body), "")
        self.assertEqual(analysis.fake_marker(body), "")


class V3Parity(unittest.TestCase):
    """Nothing V3 silenced starts firing, and nothing V3 caught stops firing."""

    #: Every ``fake_prefix`` in auto-dev-framework/scripts/data/secret-prefixes.json,
    #: extended to a value long enough to reach its own real_regex.
    V3_FAKE_VALUES = (
        ("openai", "sk-test-0123456789abcdef0123456789abcdef"),
        ("anthropic", "sk-ant-test-0123456789abcdef0123456789abcdef"),
        ("aws_access_key", "AKIATESTABCDEFGHIJKL"),
        ("github_pat_fine", "github_pat_test_0123456789abcdef0123456789"),
        ("google_api", "AIzaTest0123456789abcdef0123456789ABC"),
        ("slack", "xoxb-test-0123456789abcdef"),
        ("uri_credentials", "postgresql://user:pass@"),
        ("uri_credentials", "postgresql://unused:unused@"),
    )

    def test_every_v3_fake_prefix_is_still_silent(self):
        for pattern_id, value in self.V3_FAKE_VALUES:
            with self.subTest(value=value):
                report = analysis.analyse_source(value, path="x.py")
                self.assertEqual(
                    [], list(report.findings),
                    f"{value} should be suppressed, not reported",
                )

    def test_the_pragma_waiver_still_works(self):
        report = analysis.analyse_source(
            # This checker's own test data: the assertion below is that this
            # exact shape IS judged a secret, so it has to look like one.
            # pragma: allow-secret
            "# pragma: allow-secret\nKEY = 'AKIALU3NDADVWPP7FXBG'\n", path="x.py"
        )
        self.assertEqual([], list(report.findings))

    def test_a_waived_value_is_recorded_rather_than_blindfolded(self):
        """V3 blanked the line before matching, so the waiver left no trace."""
        report = analysis.analyse_source(
            # This checker's own test data: the assertion below is that this
            # exact shape IS judged a secret, so it has to look like one.
            # pragma: allow-secret
            "# pragma: allow-secret\nKEY = 'AKIALU3NDADVWPP7FXBG'\n", path="x.py"
        )
        self.assertEqual(1, len(report.suppressed))
        self.assertEqual(analysis.RULE_PRAGMA, report.suppressed[0].judgement.rule)
        self.assertEqual(2, report.suppressed[0].candidate.line)

    def test_the_pragma_waiver_expires_after_one_line(self):
        report = analysis.analyse_source(
            # This checker's own test data: the assertion below is that this
            # exact shape IS judged a secret, so it has to look like one.
            # pragma: allow-secret
            "# pragma: allow-secret\nFIRST = 'AKIALU3NDADVWPP7FXBG'\n"
            # This checker's own test data: the assertion below is that this
            # exact shape IS judged a secret, so it has to look like one.
            # pragma: allow-secret
            "SECOND = 'AKIALU3NDADVWPP7FXBG'\n",
            path="x.py",
        )
        self.assertEqual(1, len(report.findings))
        self.assertEqual(3, report.findings[0].candidate.line)

    def test_an_anthropic_key_is_reported_once_not_twice(self):
        """V3 reported ``sk-ant-...`` as both Anthropic and OpenAI."""
        report = analysis.analyse_source(
            (RED / "anthropic_key.py").read_text(), path="x.py"
        )
        self.assertEqual(["anthropic"], [v.candidate.pattern_id for v in report.findings])

    def test_base64_wrapped_credentials_are_still_found(self):
        report = analysis.analyse_source(
            (RED / "base64_wrapped.txt").read_text(), path="x.txt"
        )
        self.assertEqual(1, len(report.findings))
        self.assertTrue(report.findings[0].candidate.pattern_id.startswith("encoded_"))


class Determinism(unittest.TestCase):
    def test_two_runs_of_the_same_input_are_byte_identical(self):
        names = sorted(p.name for p in GREEN.iterdir() if p.is_file())
        first_code, first = run(GREEN, names, explain=True)
        second_code, second = run(GREEN, names, explain=True)
        self.assertEqual(first_code, second_code)
        self.assertEqual(first, second)

    def test_output_does_not_depend_on_the_order_of_subject_refs(self):
        names = sorted(p.name for p in GREEN.iterdir() if p.is_file())
        forward_code, forward = run(GREEN, names, explain=True)
        reverse_code, reverse = run(GREEN, list(reversed(names)), explain=True)
        self.assertEqual(forward_code, reverse_code)
        self.assertEqual(forward, reverse)


class KernelContract(unittest.TestCase):
    """DESIGN.md 7.1/7.3: the three flags, the --out payload, no leaked value."""

    def invoke(self, rel_paths, *, facts=None, want_out=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            subject = tmp / "subject.json"
            subject.write_text(json.dumps({
                "claim_id": "c", "claim_kind": "secret", "task_id": "t",
                "repo_root": str(RED), "diff_base": "x", "symbol": "", "variant": "",
                "params": {},
                "subject_refs": [{"kind": "file", "path": p} for p in rel_paths],
            }))
            argv = [sys.executable, str(ROOT / "checkers" / "secret_scan.py"),
                    "--subject", str(subject)]
            if facts is not None:
                facts_path = tmp / "facts.json"
                facts_path.write_text(json.dumps(facts))
                argv += ["--facts", str(facts_path)]
            out_path = tmp / "out.json"
            if want_out:
                argv += ["--out", str(out_path)]
            done = subprocess.run(argv, capture_output=True, text=True)
            payload = json.loads(out_path.read_text()) if out_path.is_file() else None
        return done, payload

    def test_all_three_kernel_flags_are_accepted(self):
        done, payload = self.invoke(["anthropic_key.py"], facts={"baselines": {}})
        self.assertEqual(done.returncode, checker.EXIT_FAIL, done.stderr)
        self.assertIsNotNone(payload)

    def test_out_payload_states_the_same_verdict_as_the_exit_code(self):
        done, payload = self.invoke(["anthropic_key.py"])
        self.assertEqual(payload["exit_code"], done.returncode)
        self.assertEqual(payload["verdict"], "fail")
        self.assertEqual(payload["counts"]["findings"], 1)

    def test_out_payload_is_valid_json_on_a_pass_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clean.md").write_text((GREEN / "url_placeholders.md").read_text())
            subject = root / "subject.json"
            subject.write_text(json.dumps({
                "repo_root": str(root),
                "subject_refs": [{"kind": "file", "path": "clean.md"}],
            }))
            out_path = root / "out.json"
            done = subprocess.run(
                [sys.executable, str(ROOT / "checkers" / "secret_scan.py"),
                 "--subject", str(subject), "--out", str(out_path)],
                capture_output=True, text=True,
            )
            payload = json.loads(out_path.read_text())
        self.assertEqual(done.returncode, checker.EXIT_PASS, done.stderr)
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(payload["counts"]["findings"], 0)

    def test_a_finding_is_redacted_in_stdout_and_in_the_out_payload(self):
        # This checker's own test data: the assertion below is that this
        # exact shape IS judged a secret, so it has to look like one.
        # pragma: allow-secret
        secret = "sk-ant-api03-UJ1Tde7I26IfvQvWCooKgObJ1DBfvXDCxA_s4qJqwTH"
        done, payload = self.invoke(["anthropic_key.py"])
        self.assertNotIn(secret, done.stdout)
        self.assertNotIn(secret, json.dumps(payload))
        self.assertIn("redacted", done.stdout)
        self.assertIn("anthropic_key.py:", done.stdout)

    def test_a_pem_inside_a_json_string_is_redacted_too(self):
        """Escaped newlines must not defeat the redaction."""
        code, out = run(RED, ["service_account.json"])
        self.assertEqual(code, checker.EXIT_FAIL, out)
        self.assertIn("-----BEGIN PRIVATE KEY----- [redacted,", out)
        body = (RED / "service_account.json").read_text()
        payload = analysis.pem_payload(body.replace("\\n", "\n"))
        self.assertGreater(len(payload), 40)
        self.assertNotIn(payload[:40], out)

    def test_a_suppressed_value_is_printed_in_full_because_it_is_not_a_secret(self):
        code, out = run(GREEN, ["vendor_example_ids.json"], explain=True)
        self.assertEqual(code, checker.EXIT_PASS, out)
        self.assertIn("AKIAIOSFODNN7EXAMPLE", out)


class Registration(unittest.TestCase):
    """DESIGN.md 7.4: the kernel's own registration gate, run for real."""

    def test_the_kernel_would_register_this_checker(self):
        from kernel import register

        ok, report_ = register.verify_checker(
            repo_root=ROOT / "tests" / "fixtures" / "secret",
            checker_path=ROOT / "checkers" / "secret_scan.py",
            fixtures_dir=ROOT / "tests" / "fixtures" / "secret",
            kind="secret",
        )
        self.assertTrue(ok, report_["failures"])
        self.assertEqual(report_["passed"], report_["total"])
        self.assertGreaterEqual(report_["total"], 16)


class Process(unittest.TestCase):
    """The exit code the kernel actually reads, through a real subprocess."""

    def invoke(self, repo_root: Path, rel_paths):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {"repo_root": str(repo_root),
                 "subject_refs": [{"kind": "file", "path": p} for p in rel_paths]},
                handle,
            )
            subject = handle.name
        return subprocess.run(
            [sys.executable, str(ROOT / "checkers" / "secret_scan.py"), "--subject", subject],
            capture_output=True, text=True,
        )

    def test_exit_one_on_a_red_fixture(self):
        done = self.invoke(RED, ["anthropic_key.py"])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_exit_zero_on_the_green_fixtures(self):
        names = sorted(p.name for p in GREEN.iterdir() if p.is_file())
        done = self.invoke(GREEN, names)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_malformed_subject_is_the_checker_breaking_not_a_verdict(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{ not json")
            subject = handle.name
        done = subprocess.run(
            [sys.executable, str(ROOT / "checkers" / "secret_scan.py"), "--subject", subject],
            capture_output=True, text=True,
        )
        self.assertGreaterEqual(done.returncode, checker.EXIT_BROKEN)
        self.assertIn("checker broke", done.stderr)


if __name__ == "__main__":
    unittest.main()


class TheTableIsData(unittest.TestCase):
    """A credential family is a row, not a code change.

    The table shipped as a literal in `secret_patterns.py`, which made adding a
    family an edit to the kernel -- and `.v4/**` and `checkers/**` are on
    protected_paths, so the correct move needed a signature while ignoring the
    gap needed nothing. The reference repo has four families this table does not
    match, and none of them had been added.

    Ported back to what V3 had: `secret-prefixes.json`. V4 turned it into code
    and this turns it back.
    """

    def test_the_shipped_table_is_the_json_beside_the_module(self):
        table = json.loads(
            (ROOT / "kernel" / "analysis" / "secret_patterns.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(len(analysis.PATTERNS), len(table["patterns"]))
        self.assertEqual([p.id for p in analysis.PATTERNS],
                         [r["id"] for r in table["patterns"]])

    ROW = {"id": "probe_family", "label": "probe",
           "regex": r"\bPRB_[A-Za-z0-9]{20,}\b",
           "order": 99, "prefix": "PRB_"}

    def test_an_adopter_row_is_added_not_substituted(self):
        # Union, never replace: a repo declaring four of its own must not
        # thereby lose the twelve it never had to think about.
        table = analysis.extended([dict(self.ROW, kind=analysis.KIND_PROVIDER)])
        self.assertEqual(len(table), len(analysis.PATTERNS) + 1)
        ids = {p.id for p in table}
        self.assertIn("shopify_admin", ids)
        self.assertIn("probe_family", ids)
        hits = analysis._direct_matches(
            "TOKEN = 'PRB_abcdefghijklmnopqrstuvwx'", "a.py", table=table)
        self.assertTrue(any(c.pattern_id == "probe_family" for c in hits), hits)

    def test_the_shipped_table_is_not_changed_by_extending_it(self):
        """`extend` mutated `PATTERNS` in place, so `analyse_source` answered
        differently depending on whether an earlier caller in the same process
        had called it -- and the two production callers are separate processes
        with different tables."""
        before = tuple(analysis.PATTERNS)
        analysis.extended([dict(self.ROW, kind=analysis.KIND_PROVIDER)])
        self.assertEqual(analysis.PATTERNS, before)

    def test_a_duplicate_id_cannot_shadow_a_shipped_rule(self):
        table = analysis.extended([{"id": "openai", "label": "hijacked",
                                    "kind": analysis.KIND_PROVIDER,
                                    "regex": "x", "order": 1}])
        by_id = {p.id: p for p in table}
        self.assertEqual(by_id["openai"].label,
                         analysis.PATTERN_BY_ID["openai"].label)

    def test_one_table_for_the_checker_and_the_engagement_gate(self):
        """`table_for` is what both read, so an adopter's families cannot be
        visible to `secret` and invisible to `engage`."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        self.assertEqual(analysis.table_for(tmp), analysis.PATTERNS)
        (tmp / ".v4" / "secret_patterns.json").write_text(json.dumps(
            {"patterns": [dict(self.ROW, kind="provider")]}))
        self.assertEqual(len(analysis.table_for(tmp)),
                         len(analysis.PATTERNS) + 1)


class ATextFileIsScannedWhateverItsSize(unittest.TestCase):
    """The size limit is about memory, and it was answering a different question.

    A file over it came back `unverifiable`, so the checker exited 4 and said
    the repo could not be judged. Measured on one adopter: the file that tripped
    it was `.v4/ledger_export.jsonl` -- written by `v4 ship`, append-only, so it
    only grows -- and it carries engagement prose verbatim, which is where a
    worker pasting a real credential would land. The one file most likely to
    hold one was the one file nothing could read, and the operator signed a
    repo-scoped `unprovable` to get past it.
    """

    def _module(self):
        """The checker's top half, without running its argument parser."""
        src = (Path(__file__).resolve().parents[1]
               / "checkers" / "secret_scan.py")
        ns = {"__file__": str(src)}
        text = src.read_text(encoding="utf-8")
        exec(compile(text[:text.index("def inspect(")], "secret_scan", "exec"), ns)
        return ns

    def _big(self, ns, secret_at):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        path = tmp / "big.jsonl"
        rows = ['{"payload":"harmless %d %s"}' % (i, "x" * 80)
                for i in range(ns["CHUNK_LINES"] * 3)]
        rows[secret_at - 1] = ('{"text":"postgresql://realuser:S3cretPassw0rd'
                               '@db.internal:5432/app"}')
        path.write_text("\n".join(rows), encoding="utf-8")
        self.assertGreater(path.stat().st_size, ns["MAX_FILE_BYTES"])
        return path

    def test_streaming_gives_the_same_answer_as_reading_it_whole(self):
        """Including the line number, which is what a chunked scan gets wrong."""
        ns = self._module()
        at = ns["CHUNK_LINES"] * 2 + 5          # past two chunk boundaries
        path = self._big(ns, at)
        whole = ns["analysis"].analyse_source(
            path.read_text(encoding="utf-8"), path="big.jsonl")
        streamed, suppressed = ns["_analyse_streamed"](path, "big.jsonl")
        key = lambda vs: [(v.candidate.line, v.candidate.col, v.candidate.pattern_id)
                          for v in vs]
        self.assertEqual(key(whole.findings), key(streamed))
        self.assertEqual(key(whole.suppressed), key(suppressed))
        self.assertEqual([v.candidate.line for v in streamed], [at])

    def test_a_secret_on_a_chunk_boundary_is_still_found_once(self):
        """The overlap that keeps a pragma with its line must not double-count."""
        ns = self._module()
        at = ns["CHUNK_LINES"] + 1              # first line of the second chunk
        streamed, _ = ns["_analyse_streamed"](self._big(ns, at), "big.jsonl")
        self.assertEqual([v.candidate.line for v in streamed], [at])

    def test_a_blob_is_still_unverifiable_rather_than_scanned(self):
        """"Too big" and "a text scanner has nothing true to say about this"
        are different answers and must stay different."""
        ns = self._module()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        blob = tmp / "model.onnx"
        blob.write_bytes(b"\0\1\2\3" * (ns["MAX_FILE_BYTES"] // 2))
        why = ns["_oversize_why"](blob, blob.stat().st_size)
        self.assertIn("binary", why)
        self.assertNotIn("of text", why)


class ASentenceThatWouldBreakTheLedgerIsRefusedWhenItIsWritten(unittest.TestCase):
    """`v4 ship` writes engagement prose verbatim into the ledger export, that
    file is committed, and it is scanned. So a sentence carrying a literal
    credential shape stops the ledger being committable -- hours later, with
    nothing pointing back at the sentence.

    Measured: the `secret-chain` rule asks a worker to say why a handler does
    not wrap a token; a good answer named the `https://user:token@` form; the
    pre-commit scanner refused the export; and the fix was to declare one more
    placeholder pair. The check belongs where the sentence is written.

    Also measured, over 73 real sentences from six tasks: three reached the
    scanner, all three were classified as placeholders, and none would have been
    refused. This is not a tax on discussing credentials.
    """

    def _cfg(self):
        class Cfg:
            thresholds = {"min_chars": 40, "dup_threshold": 0.8}
            kinds = {}
        return Cfg()

    def test_a_literal_credential_is_refused(self):
        from kernel import engagement
        ok, why = engagement.judge_text(
            self._cfg(),
            sentence=("core/db.py builds its dsn from settings, so nothing like "
                      # This checker's own test data: the assertion below is that this
                      # exact shape IS judged a secret, so it has to look like one.
                      # pragma: allow-secret
                      "postgresql://realuser:S3cretPassw0rd@db.internal:5432/app "
                      "is ever written down in this repo."),
            subject_words={"core/db.py"})
        self.assertFalse(ok)
        self.assertIn("real credential", why)
        self.assertIn("ledger_export", why)

    def test_a_declared_placeholder_shape_still_passes(self):
        """Several rules ask the worker to name the shape. Refusing that would
        make the claim they belong to unanswerable."""
        from kernel import engagement
        ok, why = engagement.judge_text(
            self._cfg(),
            sentence=("core/db.py takes its dsn from the environment, so the "
                      "postgresql://user:pass@host form never reaches a "
                      "traceback from here."),
            subject_words={"core/db.py"})
        self.assertTrue(ok, why)

    def test_a_sentence_with_no_credential_shape_is_untouched(self):
        from kernel import engagement
        ok, why = engagement.judge_text(
            self._cfg(),
            sentence=("core/db.py opens one pool at import and hands it out, so "
                      "a second caller cannot open a competing one behind it."),
            subject_words={"core/db.py"})
        self.assertTrue(ok, why)
