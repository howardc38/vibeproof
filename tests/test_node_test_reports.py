"""Use the real Node runner to distinguish passed, failed and skipped suites."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is not installed for its execution lane')
class NodeReports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.v4').mkdir()
        (self.root / 'app.cjs').write_text('exports.double = n => n * 2;\n')
        self.subject = self.root / 'subject.json'
        self.subject.write_text(json.dumps({'repo_root': str(self.root)}))

    def run_suite(self, reporter=None, skip=False, title='the real function doubles its input'):
        (self.root / 'app.test.cjs').write_text(
            "const test=require('node:test');\nconst assert=require('node:assert/strict');\n"
            "const {double}=require('./app.cjs');\n"
            f"test({json.dumps(title)}, {{skip:{str(skip).lower()}}}, () => assert.equal(double(3),6));\n")
        argv = [shutil.which('node'), '--test']
        if reporter: argv.append('--test-reporter=' + reporter)
        argv.append('app.test.cjs')
        (self.root / '.v4/config.json').write_text(json.dumps({'test_command': shlex.join(argv)}))
        return subprocess.run([sys.executable, str(ROOT / 'checkers/test.py'),
                               '--subject', str(self.subject)], cwd=self.root,
                              text=True, capture_output=True, timeout=20)

    def test_default_spec_and_tap_success_are_recognized(self):
        for reporter in (None, 'spec', 'tap'):
            with self.subTest(reporter=reporter):
                r = self.run_suite(reporter)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_all_skipped_is_not_success_in_either_reporter(self):
        for reporter in ('spec', 'tap'):
            with self.subTest(reporter=reporter):
                r = self.run_suite(reporter, skip=True)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_real_assertion_failure_is_not_reported_as_runner_startup_failure(self):
        (self.root / 'app.cjs').write_text('exports.double = n => n * 3;\n')
        r = self.run_suite('spec')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn('did not finish', r.stdout + r.stderr)

    def test_zero_tests_in_a_test_name_does_not_override_the_run_totals(self):
        r = self.run_suite('spec', title='handles 0 tests in a diagnostic label')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
