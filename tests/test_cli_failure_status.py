"""A caught write failure can leave through a named CLI exit status."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kernel.analysis.fail_closed import analyse_source

SOURCE = '''import sys
from pathlib import Path
def launch():
    status = 0
    if len(sys.argv) > 1:
        try:
            Path(sys.argv[1]).write_text('output')
        except OSError as error:
            report = {'valid': False, 'reason': str(error)}
            status = 5
    print('finished')
    return status
if __name__ == '__main__':
    raise SystemExit(launch())
'''


class CLIFailureStatus(unittest.TestCase):
    def run_failure(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = root / 'program.py'
            program.write_text(source)
            # Writing text over an existing directory genuinely fails.
            result = subprocess.run([sys.executable, str(program), str(root)],
                                    text=True, capture_output=True, timeout=10)
            return result.returncode

    def test_named_status_reaches_real_system_exit(self):
        self.assertEqual(self.run_failure(SOURCE), 5)
        self.assertEqual(analyse_source(SOURCE, path='program.py'), [])

    def test_sys_exit_and_another_status_name_work_too(self):
        source = SOURCE.replace('status', 'result_code').replace(
            'raise SystemExit(launch())', 'sys.exit(launch())')
        self.assertEqual(self.run_failure(source), 5)
        self.assertEqual(analyse_source(source, path='program.py'), [])

    def test_an_ordinary_value_return_is_not_an_exit_contract(self):
        source = SOURCE[:SOURCE.index("if __name__")]
        self.assertTrue(analyse_source(source, path='library.py'))

    def test_zero_or_wrapping_status_is_not_failure(self):
        for value in (0, 256):
            with self.subTest(value=value):
                source = SOURCE.replace('status = 5', f'status = {value}')
                self.assertEqual(self.run_failure(source), 0)
                self.assertTrue(analyse_source(source, path='program.py'))

    def test_literal_return_uses_the_same_real_exit_semantics(self):
        for value, expected in ((5, 5), (256, 0)):
            with self.subTest(value=value):
                source = SOURCE.replace('status = 5', f'return {value}').replace('return status', 'return 0')
                self.assertEqual(self.run_failure(source), expected)
                self.assertEqual(bool(analyse_source(source, path='program.py')), expected == 0)

    def test_reset_after_handler_is_still_a_swallow(self):
        source = SOURCE.replace("    print('finished')", "    status = 0\n    print('finished')")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))

    def test_finally_can_overwrite_the_failure(self):
        source = SOURCE.replace("    print('finished')", "        finally:\n            status = 0\n    print('finished')")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))

    def test_early_success_in_the_handler_is_not_hidden(self):
        source = SOURCE.replace("            report =", "            if True:\n                return 0\n            report =")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))

    def test_shadowed_exit_is_not_a_cli_contract(self):
        source = SOURCE.replace('raise SystemExit(launch())', 'sys.exit(launch())')
        source = source.replace("if __name__", "sys.exit = lambda result: None\nif __name__")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))

    def test_conditional_shadow_of_system_exit_is_not_trusted(self):
        source = SOURCE.replace("if __name__", "if True:\n    def SystemExit(value):\n        sys.exit(0)\nif __name__")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))

    def test_an_early_return_after_the_handler_is_not_skipped(self):
        source = SOURCE.replace("    print('finished')", "    return 0\n    print('finished')")
        self.assertEqual(self.run_failure(source), 0)
        self.assertTrue(analyse_source(source, path='program.py'))
