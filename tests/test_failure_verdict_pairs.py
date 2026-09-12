"""A failed write can report an explicit (ok, error) result to its caller."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kernel.analysis.fail_closed import analyse_source

SOURCE = '''import sqlite3
def store(path):
    try:
        with sqlite3.connect(path) as conn:
            conn.execute('INSERT INTO missing_table VALUES (1)')
        return True, None
    except sqlite3.Error as error:
        return False, f'Could not store the record: {error}'
'''


class FailureVerdictPairs(unittest.TestCase):
    def test_actual_database_failure_is_reported_to_caller(self):
        namespace = {}
        exec(SOURCE, namespace)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'owner.db'
            ok, error = namespace['store'](str(path))
            self.assertIs(ok, False)
            self.assertIn('no such table', error)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)
        self.assertEqual(analyse_source(SOURCE, path='store.py'), [])

    def test_equivalent_dict_and_literal_pair_report_failure(self):
        for result in ("False, 'Database refused the write'",
                       "{'ok': False, 'error': str(error)}"):
            with self.subTest(result=result):
                source = SOURCE.replace("False, f'Could not store the record: {error}'", result)
                self.assertEqual(analyse_source(source, path='store.py'), [])

    def test_success_silence_and_ambiguous_pairs_still_report(self):
        for result in ('True, str(error)', 'False, None', "False, ''", 'False, []',
                       'False, False', 'False, 0', 'False,',
                       'False, str(error), 3', "0, 'a result'", "None, 'a result'"):
            with self.subTest(result=result):
                source = SOURCE.replace("False, f'Could not store the record: {error}'", result)
                self.assertTrue(analyse_source(source, path='store.py'))

    def test_nested_unexecuted_refusal_does_not_hide_success(self):
        source = SOURCE.replace("return False, f'Could not store the record: {error}'",
                                "def ignored():\n            return False, str(error)\n        return True, None")
        self.assertTrue(analyse_source(source, path='store.py'))

    def test_conditional_or_early_success_is_not_hidden_by_a_refusal_pair(self):
        for handler in ("if False:\n            return False, str(error)\n        return True, None",
                        "if True:\n            return True, None\n        return False, str(error)"):
            with self.subTest(handler=handler):
                source = SOURCE.replace("return False, f'Could not store the record: {error}'", handler)
                namespace = {}; exec(source, namespace)
                with tempfile.TemporaryDirectory() as tmp:
                    self.assertEqual(namespace['store'](str(Path(tmp) / 'owner.db')), (True, None))
                self.assertTrue(analyse_source(source, path='store.py'))

    def test_absent_data_with_an_explicit_failure_result_reports_real_sqlite_error(self):
        source = '''import sqlite3
from contextlib import closing
class LookupFailure(str):
    pass
def read(path):
    try:
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute('SELECT * FROM missing_table').fetchall(), None
    except sqlite3.Error as error:
        return None, LookupFailure(str(error))
'''
        namespace = {}; exec(source, namespace)
        with tempfile.TemporaryDirectory() as tmp:
            data, error = namespace['read'](str(Path(tmp) / 'owner.db'))
            self.assertIsNone(data)
            self.assertIsInstance(error, namespace['LookupFailure'])
            self.assertIn('no such table', error)
        self.assertEqual(analyse_source(source, path='reader.py'), [])

    def test_absent_payload_uses_the_existing_explicit_error_contract(self):
        for value in ('None, BackendError(str(error))',
                      'None, Response.rejected(str(error))',
                      "None, {'ok': False, 'error': str(error)}"):
            with self.subTest(value=value):
                source = SOURCE.replace("False, f'Could not store the record: {error}'", value)
                self.assertEqual(analyse_source(source, path='reader.py'), [])

    def test_error_pair_must_not_disguise_success_or_plain_data(self):
        for value in ('True, LookupFailure(str(error))', '0, LookupFailure(str(error))',
                      'None, DataRecord(str(error))', 'None, Response.success()',
                      "None, {'ok': True, 'error': None}"):
            with self.subTest(value=value):
                source = SOURCE.replace("False, f'Could not store the record: {error}'", value)
                self.assertTrue(analyse_source(source, path='reader.py'))

    def test_early_success_still_wins_over_later_null_failure_pair(self):
        source = SOURCE.replace("return False, f'Could not store the record: {error}'",
                                "if True:\n            return True, None\n        return None, LookupFailure(str(error))")
        self.assertTrue(analyse_source(source, path='reader.py'))
