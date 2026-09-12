"""Flags and positional paths must not exchange roles in the facts CLI."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FactsCLIArguments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True, capture_output=True)
        (self.repo / 'service.py').write_text(
            'def run(path, actor):\n    if allowed(actor):\n        path.write_text("x")\n'
            '    return path.read_text()\ndef allowed(actor):\n    return actor == "local"\n')
        (self.repo / 'settings.py').write_text('MODE="local"\n')
        self.table = self.repo / 'facts.json'
        self.table.write_text(json.dumps({
            'repo': 'probe', 'generated_from_commit': '0' * 40,
            'outbound_write': [{'pattern': 'path.write_text', 'seen_at': 'service.py:3', 'kind': 'fs'}],
            'outbound_read': [{'pattern': 'path.read_text', 'seen_at': 'service.py:4', 'kind': 'fs'}],
            'auth_decision': [{'pattern': 'allowed', 'seen_at': 'service.py:2', 'kind': 'authz'}],
            'entrypoint_globs': ['service.py'], 'ui_globs': [],
            'config_files': ['settings.py'], 'protected_paths': ['.v4/**']}))
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True, capture_output=True)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'kernel.facts', *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, timeout=15)

    def test_flag_never_fills_a_missing_root(self):
        for args in [('verify', self.table, '--gone-only'), ('scan', self.table, '--sites'),
                     ('restate', self.table), ('verify', self.table)]:
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn('usage:', r.stderr)
                self.assertNotIn('Traceback', r.stderr)
                self.assertNotIn('BAD ', r.stdout)

    def test_flag_positions_do_not_change_the_repository(self):
        for args in [('verify', '--gone-only', self.table, self.repo),
                     ('verify', self.table, '--gone-only', self.repo),
                     ('verify', self.table, self.repo, '--gone-only')]:
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn('0 rows citing something that is gone', r.stdout)

    def test_scan_keeps_the_requested_table_after_flags(self):
        r = self.cli('scan', self.table, '--sites', self.repo, '--tests', 'outbound_read')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('service.py:4', r.stdout)
        self.assertNotIn('== outbound_write', r.stdout)

    def test_invalid_options_and_extra_paths_fail_before_restate_writes(self):
        original = self.table.read_bytes()
        for tail in [('--gone-only',), ('--unknown',), ('extra',)]:
            with self.subTest(tail=tail):
                r = self.cli('restate', self.table, self.repo, *tail)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(self.table.read_bytes(), original)

    def test_scan_unknown_table_is_usage_error(self):
        r = self.cli('scan', self.table, self.repo, 'outbound_typo')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn('Traceback', r.stderr)


if __name__ == '__main__':
    unittest.main()
